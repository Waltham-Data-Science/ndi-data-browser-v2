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
