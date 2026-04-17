# Operations

## Deploy

Target platform: Railway. Multi-stage Dockerfile builds the frontend then copies static assets into the Python image; single container serves `/api/*` and SPA static files.

```bash
# Local docker-compose (Redis + backend + frontend)
docker-compose -f infra/docker-compose.yml up

# Railway
railway up  # on ndi-data-browser-v2 service
```

Required Railway env vars (see [config](../backend/config.py)):
- `NDI_CLOUD_URL`
- `REDIS_URL`
- `SESSION_ENCRYPTION_KEY`  (32-byte Fernet key, base64)
- `CSRF_SIGNING_KEY`         (32 bytes hex)
- `CORS_ORIGINS`
- `SENTRY_DSN` (optional)

### Replica count

`infra/railway.toml` sets `numReplicas = 1`. This is deliberate at the
current load (<10 concurrent users). Trade-offs:

- **Cost**: one replica = 1× Railway compute bill. Two would be 2×.
- **Rate-limit correctness**: `backend/middleware/rate_limit.py` falls
  back to per-replica in-process counters when Redis is unavailable.
  Multiple replicas multiply the effective rate-limit ceiling by that
  count; a single replica is the correct model until Redis-backed
  counters become the single source of truth.
- **Deploy blip**: a deploy on one replica takes the service offline
  for ~30s (healthcheck on new → cutover). Tolerable for <10 users;
  not for larger loads.

Scale `numReplicas` back to 2+ when:
- Concurrent sessions climb above ~100, or
- A deploy blip of 30s becomes user-visible churn.

### CI scope

The repo has four CI workflows — three of them run unattended:

| Workflow | Trigger | What it gates |
|---|---|---|
| `ci.yml` | every PR + push to `main` | ruff, mypy strict, pytest 212, vitest, typecheck, frontend build, bundle-size ≤ 200 KB gzip, Docker build, security audit |
| `nightly-contract.yml` | daily cron (04:00 UTC) | contract tests against live dev cloud |
| `load-test.yml` | `workflow_dispatch` only | Locust p95 + 5xx gates |
| `rollout-health.yml` | `workflow_dispatch` only (schedule intentionally off) | prod /metrics probe during a staged cutover |

Playwright + Lighthouse do **not** run in CI. The specs + fixtures +
lighthouserc.json live under `frontend/tests-e2e/` and the repo root
for on-demand local use. Run via:

```sh
make test-e2e         # mocked fixtures (fast, drift-safe)
make test-e2e-live    # hits prod cloud (catches live-data drift)
npx lhci autorun      # Lighthouse against a local preview build
```

Rationale: CI-gated E2E + Lighthouse against a GHA-hosted dev server
was flaky (Chrome interstitial against Vite) and slow (5–10 min per
PR). For a <10-user project the maintenance cost exceeded the signal.
When the project grows or staging/preview envs are added, revisit.

## Rollback

1. **Feature-flag rollback (during cutover):** flip `ROLLOUT_PCT` env var to 0. v1 continues serving 100%.
2. **Full rollback:** redeploy previous git tag via Railway dashboard or `railway rollback`.
3. **DB rollback not needed:** v2 has no dataset DB. Redis sessions are ephemeral by design.

## Incident response

### Backend 5xx spike
1. Check Prometheus `ndb_http_requests_total{status=~"5.."}` rate.
2. Check structured logs for the top error code: `jq '.code'` on last 1000 events.
3. If code is `CLOUD_*`: check ndi-cloud-node status, wait for recovery; circuit breaker should auto-open and protect us.
4. If `INTERNAL`: unhandled exception — grep `traceback` in logs, patch the service.

### Session failures
1. `redis-cli ping` — is Redis up?
2. Check `ndb_session_lookup_seconds_p95` — >50ms suggests Redis latency.
3. If Redis is down: login attempts fail with `CLOUD_UNREACHABLE` (chosen deliberately — don't leak internal infra detail). Reads with valid cookies still fail with `AUTH_EXPIRED` until Redis returns.

### Query timeout spike
1. Check `ndb_query_timeout_total` metric.
2. Look at the top source: `POST /api/query` → which scope? If `"all"` or `"public"` + no `isa`: users issuing unbounded queries.
3. Mitigation: the UI already warns on unbounded queries. If we see programmatic abuse, add per-user rate limit adjustment.

### Cognito token issues
1. `ndb_cognito_refresh_failures_total` rising → users being forced to re-login.
2. Check Cognito User Pool config (AWS console): refresh token TTL, rotation settings.

## Observability endpoints

| Endpoint | What |
|---|---|
| `GET /api/health` | Liveness: returns 200 if app is running |
| `GET /api/health/ready` | Readiness: Redis + cloud reachable |
| `GET /metrics` | Prometheus scrape endpoint |

## Dashboards

Committed as code under `infra/dashboards/`:
- `overview.json` — req count, latency, error rate by route
- `cloud.json` — cloud client call latency, retry count, breaker state
- `auth.json` — login rate, refresh success rate, session count
- `business.json` — top datasets viewed, queries per hour, ontology cache hit rate

## Runbook: cutover

1. Deploy v2 to prod URL under `ROLLOUT_PCT=0`.
2. Audri + Steve + 2–3 Marder reviewers get `X-NDB-Rollout: force-on` header via dev console to validate v2.
3. Week 1 validation → blockers fixed.
4. Ramp:
   - Day 1: `ROLLOUT_PCT=10`, 24h soak, review dashboards.
   - Day 2: `ROLLOUT_PCT=25`.
   - Day 3: `ROLLOUT_PCT=50`.
   - Day 4: `ROLLOUT_PCT=100`.
5. Automated rollback condition (cron checking Prometheus every 5m):
   - 5xx rate >1% for 10 consecutive minutes
   - p95 latency regression >20% vs. pre-ramp baseline
6. Keep v1 at `/v1-archive` for 2 weeks.
7. Post-cutover retrospective, archive v1 repo.
