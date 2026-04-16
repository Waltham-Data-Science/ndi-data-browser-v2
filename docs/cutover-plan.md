# v2 Cutover Plan

Playbook for moving production traffic from v1 (ndi-data-browser) to v2 (ndi-data-browser-v2).

## Pre-cutover readiness checklist

- [x] All milestones M0–M6 closed with evidence
- [x] Backend tests: 62 unit + integration, 100% pass
- [x] Frontend E2E: 8 Playwright tests, 100% pass against live cloud
- [x] Docker multi-stage build succeeds
- [x] Contract tests against dev cloud scheduled nightly
- [ ] Railway service `ndb-v2` provisioned (prod project)
- [ ] Railway Redis add-on attached to `ndb-v2`
- [ ] Prod env vars configured: `NDI_CLOUD_URL`, `REDIS_URL`, `SESSION_ENCRYPTION_KEY`, `CSRF_SIGNING_KEY`, `CORS_ORIGINS`, `SENTRY_DSN`, `ENVIRONMENT=production`
- [ ] DNS: `ndb-v2.walthamdatascience.com` pointed to Railway
- [ ] Sentry project created + DSN applied
- [ ] Grafana dashboards provisioned (scrape `/metrics`)

## Rollout phases

### Phase 0 — internal validation (≥ 1 week)

- Deploy v2 to `ndb-v2-preview.up.railway.app` with `ROLLOUT_PCT=0`
- Internal users only: Audri, Steve, 2–3 Marder reviewers
- They use v2 daily for 1 week
- Blockers filed as GitHub issues; fix-rate > 80% before Phase 1

**Gate:** zero P0 issues open; P1 issue count ≤ 3 with clear owners.

### Phase 1 — 10% of traffic (24-h soak)

- Flip `ROLLOUT_PCT=10` on v2 service
- Load balancer / Cloudflare sends 10% of requests to v2 by cookie-stable hash
- Monitor every 5 min:
  - 5xx rate vs pre-ramp baseline (auto-rollback if > 1% for 10 consecutive minutes)
  - p95 latency regression > 20% triggers rollback
  - Sentry event volume

**Gate:** 24 h green. v1 remains default.

### Phase 2 — 25% / 50% / 100% (24 h each)

Identical gates to Phase 1. Each ramp promotes traffic by the next step only after 24 h green.

### Phase 3 — archive v1

- v1 remains alive at `v1-archive.ndb.walthamdatascience.com` for 2 weeks.
- After 14 days with v2 at 100% and zero rollbacks, archive v1 repo (read-only) and tear down the service.

## Automated rollback

GitHub Actions cron runs every 5 min:

```yaml
- query Prometheus for 5xx rate over last 10 min
- if > 1%: set ROLLOUT_PCT back to previous step
- page on-call via Sentry / PagerDuty
```

## Rollback procedure (manual)

1. In Railway: `railway env set ROLLOUT_PCT=0` on v2 service
2. Verify v1 is still serving 100% (health check `/api/health` on v1)
3. Post to `#ndb-incidents` with trigger + next steps

## Data migration

None required. v2 is stateless per-user except Redis sessions. Users with v1 SQLite caches do not need to migrate — cloud is fast enough that re-browsing is free.

## Deep-link preservation

| v1 URL | v2 URL |
|---|---|
| `/datasets` | `/datasets` (identical) |
| `/datasets/:id` | `/datasets/:id` (identical; default tab = subjects) |
| `/datasets/:id/documents` | `/datasets/:id/documents` (identical) |
| `/datasets/:id/documents/:docId` | `/datasets/:id/documents/:docId` (identical) |
| `/query` | `/query` (new builder, old URL params best-effort mapped) |

Stale bookmarks remain functional. No URL rewrites needed.

## Post-cutover review

Held within 2 weeks of 100%:
- What worked, what didn't, what surprised us
- Update ADRs where reality differed from plans
- File follow-ups for known lingering debt (e.g., combined-table perf)
