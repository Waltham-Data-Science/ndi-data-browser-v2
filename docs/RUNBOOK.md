# RUNBOOK

Operational reference for the NDI Cloud platform. Pairs with
[`operations.md`](./operations.md) (build/deploy mechanics) and
[`error-catalog.md`](./error-catalog.md) (typed-error semantics).

Audit close-out: synthesis §A7 + §A9.

---

## Quick reference

| Surface | URL | Owner | Logs |
|---|---|---|---|
| Marketing + app + catalog | `https://ndi-cloud.com` | Vercel project `ndi-cloud-app` | Vercel dashboard → Logs |
| FastAPI proxy | `https://ndb-v2-production.up.railway.app` | Railway service `ndi-data-browser-v2` | Railway dashboard → Logs |
| Cloud (Cognito + DocumentDB) | `https://api.ndi-cloud.com/v1` | AWS account, `ndi-cloud-node` repo | CloudWatch → log group `ndi-cloud-node` |
| Redis | Railway add-on | Railway dashboard → Redis service | Railway dashboard → Redis logs |
| (Legacy) data browser | `https://app.ndi-cloud.com` | Same Railway service, pre-cutover | Same as FastAPI |

**On-call**: `audri@walthamdatascience.com` (single primary; 30-min response target during US business hours).

---

## Common incidents

### "The site is down"

1. **Check Vercel status**: `https://www.vercel-status.com/` — if Vercel is down, no action other than wait + status-page comms.
2. **Check the marketing surface**: `curl -I https://ndi-cloud.com` — expect `200 OK` with `content-type: text/html`. If 5xx, the Vercel build is broken; roll back via Vercel UI → Deployments → previous deployment → "Promote to Production."
3. **Check the API surface**: `curl -I https://ndb-v2-production.up.railway.app/api/health` — expect `200 OK`. If 5xx or timeout, see "FastAPI is failing health checks" below.
4. **Check the cloud**: `curl https://api.ndi-cloud.com/v1/datasets/published?page=1&pageSize=1 -i | head -5` — expect `200 OK` with JSON. If 5xx, the AWS Lambda is down — check `ndi-cloud-node` dashboard.

### FastAPI is failing health checks

`GET /api/health/ready` is the readiness probe Railway uses. It checks Redis connectivity. Failure modes:

- **Redis unreachable**: Railway logs will show `redis.connection_error`. Restart the Redis add-on from Railway dashboard. The FastAPI process degrades to in-memory fallback for sessions + rate limit, so the site stays up but loses session continuity across replicas (fine at 1 replica).
- **Cloud client failing to start**: Logs show `cloud_client.start_failed`. Usually means `NDI_CLOUD_URL` is unset or unreachable. Check Railway env vars match `infra/railway.toml`'s required-keys list.
- **Lifespan crash on startup**: Logs show `app.startup_failed` followed by a Python traceback. The container will restart per `restartPolicyType=ON_FAILURE` (max 10 retries). If it keeps crashing, roll back via Railway → Deployments → previous → Redeploy.

### Login isn't working

1. **Check `/api/auth/csrf`**: `curl https://ndb-v2-production.up.railway.app/api/auth/csrf -i` — expect `200` with `Set-Cookie: XSRF-TOKEN=...`. If 5xx, FastAPI is down (above).
2. **Check the cloud's auth path**: `curl -X POST https://api.ndi-cloud.com/v1/auth/login -H 'Content-Type: application/json' -d '{"email":"...","password":"..."}'` — if it hangs or 5xx, the Cognito side is degraded.
3. **Check rate limits**: if a user reports "I've been logged out repeatedly," they may have hit `RATE_LIMIT_LOGIN_PER_USER_HOUR` (default 10/hour). Check Railway logs for `rate_limit.rejected` events with their `subject:u:<hash>`. Wait the configured window or temporarily bump the limit via Railway env.

### "I see CSRF errors on every action"

The CSRF cookie is `XSRF-TOKEN` scoped to `Domain=.ndi-cloud.com`. Common breakage:

- **Cookie domain mismatch**: if the user is on `app.ndi-cloud.com` (legacy) but the cookie was issued at `ndi-cloud.com`, the apex cookie can't be read by the subdomain. Ask them to clear cookies + re-login.
- **CSRF rate-limited**: O4 added a per-IP rate limit on CSRF failures (default 20/5min). A user with stale state may burn through the budget on auto-retries; the response code shifts from `403 CSRF_INVALID` to `429 AUTH_RATE_LIMITED`. Wait the window or clear cookies.
- **CSRF signing key rotated mid-session**: every existing token becomes invalid. Force-bounces all sessions. Don't rotate `CSRF_SIGNING_KEY` without scheduling a re-login window — see "Rotation procedures" below.

### "I'm logged in but I can't see my datasets"

1. **Check the user's session has org IDs**: `GET /api/auth/me` from their browser devtools should show `organizationIds: [...]` non-empty. If empty, the cloud-side login response didn't include orgs (cloud-side bug).
2. **Check `/api/datasets/my`**: scope=`mine` (default) fans out to `/organizations/{orgId}/datasets` per org. If one cloud call 5xxs, the proxy returns whatever it could aggregate; check Railway logs for `datasets.my.partial`.
3. **Admin firehose**: scope=`all` is admin-only. Non-admins are silently downgraded to `mine`.

### Catalog is empty / out of date

The catalog at `/datasets` is anonymous-public — no per-user state. Cached server-side on a 1-hour TTL via `RedisTableCache` (`backend/app.py:97`). If a freshly-published dataset isn't appearing:

1. **Wait 5-10 minutes**: the facet warmer (`backend/app.py:189-231`) rebuilds every 4 minutes. The catalog page reads from the most-recent successful build.
2. **Manual cache flush** (rarely needed): `redis-cli -u $REDIS_URL FLUSHDB` purges everything. The next request rebuilds. Brief perf hit (~5-10s) on the first hit per cache class.

### Frontend bundle bloat warning in CI

The `bundle-size` job in `ndi-cloud-app:.github/workflows/ci.yml` enforces 200 KB gz on app routes, 80 KB on marketing. If a PR breaches it:

1. **Check `next build` output** for the chunk that grew.
2. **Common culprits**: a static import of a heavy lib (uPlot, d3, MUI) into a route that doesn't need it. Convert to `next/dynamic({ ssr: false })`.
3. **Audit CQ5 set the precedent**: `DataPanel`'s uPlot import is dynamic. Mirror that pattern.

---

## Deploy

### Marketing + app (ndi-cloud-app)

Vercel auto-deploys on push to `main`. Pre-deploy gates:

- All 8 CI jobs green (hygiene, install, lint, typecheck, unit, build, e2e, security, gitleaks).
- Bundle budget under 200 KB gz.
- Author allowlist passes (`audri@walthamdatascience.com` or GitHub squash-merge noreply).

Preview URLs are auto-created for every PR (private repo). Open PR → Vercel comment with preview link.

### FastAPI (ndi-data-browser-v2)

Railway auto-deploys on push to `main`. Pre-deploy gates:

- All 7 CI jobs green (hygiene, branch-fresh, backend-lint, backend-test, frontend-checks, security-scan, docker-build).

Required env vars on Railway dashboard:
- `NDI_CLOUD_URL` — `https://api.ndi-cloud.com/v1`
- `REDIS_URL` — auto-provided by Railway Redis add-on
- `SESSION_ENCRYPTION_KEY` — 32-byte Fernet key, base64. Rotate **only** with a scheduled re-login window.
- `CSRF_SIGNING_KEY` — 32 bytes hex. Same rotation caveat as `SESSION_ENCRYPTION_KEY`.
- `CORS_ORIGINS` — comma-separated; must include `https://ndi-cloud.com` and (until cutover) `https://app.ndi-cloud.com`.
- `ENVIRONMENT` — `production`. Gates Swagger lockdown (B7), keep-warm loops, etc.
- `OTEL_EXPORTER_OTLP_ENDPOINT` — optional; setting it enables tracing (O7).
- `CSP_REPORT_URI` — optional; setting it adds `report-uri` + `Report-To` for CSP violation reporting (O2).

---

## Rollback

### Marketing + app

Vercel UI → Project → Deployments → previous deployment → "Promote to Production." Atomic, sub-second.

### FastAPI

Railway UI → Service → Deployments → previous deployment → "Redeploy." ~30s for the new container to come up; the old one stays warm during the rollover (zero-downtime within the limits of a 1-replica setup).

### Database (DocumentDB / Cognito)

The FastAPI proxy is **stateless** — all data lives in the cloud (DocumentDB) or in Redis (sessions). Rolling back FastAPI does **not** roll back data. If a deploy corrupted DocumentDB data, the rollback is on the `ndi-cloud-node` side (separate repo, separate runbook).

**Sessions**: rollback wipes Redis sessions for any in-flight requests during the rollover (~30s). Affected users see one extra login. Acceptable.

---

## Phase 7 cutover

`docs/cutover-plan.md` (and `docs/plans/cross-repo-unification-2026-04-24.md`) are the authoritative cutover docs. RUNBOOK summary:

1. **Pre-flight** (T-24h): post the pre-swap checklist to user. Verify all 9 cutover blockers (B1-B9) closed.
2. **The swap** (T-0): in Vercel UI, attach `ndi-cloud.com` to the new project (`ndi-cloud-app`). Detach from the old project.
3. **DNS** (T+0 to T+5min): TTL 300s. CDN caches purge within 5 min globally.
4. **Soak** (T+0 to T+24h): watch Vercel + Railway logs for 5xx spike. Watch Sentry (if wired) for unhandled errors. Watch user reports via support channel.
5. **Rollback** (if needed): in Vercel UI, re-attach `ndi-cloud.com` to the old project. Sub-second.
6. **Soak window post-cutover**: keep the legacy `app.ndi-cloud.com` host pointing at the old Railway service for 7 days as a fallback, then deprovision.

**Hard rule**: every cutover step requires explicit user authorization. The agent doesn't flip DNS, doesn't detach domains, doesn't change Vercel project settings. Surface the proposed action; wait for go-ahead.

---

## Rotation procedures

### `SESSION_ENCRYPTION_KEY`

Sessions are Fernet-encrypted with this key in Redis. Rotating the key invalidates **every existing session** — all users get logged out simultaneously.

Procedure:
1. Schedule a maintenance window (off-hours; affects all users).
2. Post a "we'll log you out for a security key rotation" notice 24h ahead.
3. Generate a new key: `python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"`
4. Update `SESSION_ENCRYPTION_KEY` on Railway. Redeploy.
5. Existing sessions in Redis fail Fernet decrypt → `CorruptSession` → soft re-login. Users see a one-time login.

### `CSRF_SIGNING_KEY`

CSRF tokens are HMAC-signed with this key. Same blast radius as session encryption — every existing token becomes invalid. Same procedure.

### `OTEL_EXPORTER_OTLP_ENDPOINT`

No blast radius — toggling this just enables/disables tracing. Set or clear, redeploy. Tracing degrades silently if the endpoint is unreachable.

---

## Observability

### Logs

- **Vercel**: structured JSON logs per request. Filter by `requestId` for full request flow.
- **Railway**: structured JSON via `structlog`. Each line carries `request_id` for join across middleware/handler/cloud-call.
- **CloudWatch**: AWS Lambda logs for `ndi-cloud-node`. Joined to Railway logs via the same `request_id` (FastAPI forwards `X-Request-ID` to the cloud client).

### Metrics

- **Railway dashboard**: container CPU/RAM, request rate, response time percentiles.
- **Vercel Analytics**: web vitals (LCP, FID, CLS), top routes, geographic distribution.
- **Prometheus** (`/metrics` endpoint on FastAPI): rate-limit rejection counters, cloud-call timing histograms, circuit-breaker trips. Not exposed publicly — scrape from inside Railway's network only.

### Alerts

(Out of scope for this cycle — Sentry, Datadog, or PagerDuty wiring is a follow-up. Audit synthesis §A8 was deferred per scope.)

---

## Security review surface

- **CSRF**: double-submit cookie + signed token (`backend/middleware/csrf.py`). O4 adds per-IP rate limit on failures.
- **Origin**: O5 added strict server-side Origin enforcement on mutations (`backend/middleware/origin_enforcement.py`).
- **CSP**: dual-CSP architecture documented in `docs/adr/014-dual-csp-architecture.md`. Vercel-side governs HTML; FastAPI-side is defense-in-depth on JSON responses.
- **Sessions**: Redis-backed, Fernet-encrypted, per-user idle TTL (`SESSION_IDLE_TTL_SECONDS`, default 2h) + absolute TTL (`SESSION_ABSOLUTE_TTL_SECONDS`, default 24h). O3 wired the idle gate.
- **Rate limits**: per-IP on auth + login + CSRF-fail, per-user on change-password. See `backend/middleware/rate_limit.py`.
- **IDOR**: investigated O6, no live findings. Cloud handles authorization; FastAPI is a thin pass-through with `access_token` forwarding. Two informational forward-compat notes in `/tmp/ndi-review/O6-IDOR-investigation.md` (cache scoping assumption + cross-repo ACL contract test recommendation).
- **Secret scanning**: O9 — pre-commit gitleaks hook + CI backstop on `ndi-cloud-app` (sister PR pending on `ndi-data-browser-v2`).

---

## When in doubt

- The `error-catalog.md` is the authoritative typed-error reference. If a user reports a code (e.g. `AUTH_INVALID_CREDENTIALS`, `CSRF_INVALID`, `RESPONSE_SHAPE_INVALID`), look it up there first for the recovery action.
- The `cross-repo-unification-2026-04-24.md` plan doc is the source of truth for what's intentionally pending vs. shipped.
- For anything not in either doc + not in this RUNBOOK: ping `audri@walthamdatascience.com`.
