# Backend service dependency map

**Audience:** contributors changing the FastAPI backend; operators
investigating an incident; auditors tracing data flow.

**Last updated:** 2026-05-15

This doc inventories every service the FastAPI backend depends on, in
the direction of the dependency (who-calls-whom). For each: what it's
used for, when failure is acceptable, and the failure-mode hooks.

The complementary docs (in the sibling `ndi-cloud-app` repo):
- `apps/web/docs/operations/vendor-dependencies.md` — vendor + BAA
  inventory at the higher level
- `apps/web/docs/operations/disaster-recovery.md` — runbooks per
  failure mode

---

## Topology

```
                ┌──────────────────────────────┐
                │      FastAPI backend         │
                │    (this repo, on Railway)   │
                └─────┬───────────┬────────────┘
                      │           │
                      │           │
                      ▼           ▼
            ┌─────────────┐ ┌──────────────┐
            │   Redis     │ │   Postgres   │
            │ (Railway)   │ │  (Railway)   │
            └─────────────┘ └──────────────┘
                      │
                      │  (rate limits, sessions, table cache)
                      │
                      ▼
            ┌──────────────────────────────────┐
            │      ndi-cloud-node              │
            │    (AWS Lambda + API Gateway)    │
            └──────┬───────────────────────────┘
                   │
                   ├── AWS Cognito User Pool   (identity)
                   ├── AWS DocumentDB          (metadata)
                   └── AWS S3                  (binary recordings)
```

---

## Outbound dependencies (what FastAPI calls)

### Redis (Railway-hosted)

| Field | Value |
|---|---|
| **Used for** | Session store (Fernet-encrypted access tokens), rate-limit counters, summary-table response cache, CSRF-failure budget |
| **Failure mode** | Sessions: every request returns 401 (forces re-login). Rate limit: middleware fails-open (allows requests) per the swallow-error-and-pass pattern in `csrf.py:_maybe_promote_to_rate_limit`. Cache: every read becomes a miss (slower but correct). |
| **Acceptable downtime?** | Sessions: no — platform unusable. Rate limit + cache: yes, with degraded UX. |
| **Code surface** | `backend/auth/session.py` (sessions), `backend/middleware/rate_limit.py`, `backend/cache/redis_table.py`. |

### Postgres (Railway-hosted)

| Field | Value |
|---|---|
| **Used for** | pgvector RAG store for `/ask` semantic search; future `chat_usage_events` table (Stream 3) for per-user cost tracking. |
| **Failure mode** | Semantic search returns soft error; chat falls back to structured catalog tools. |
| **Acceptable downtime?** | Yes — chat works without semantic search via fallback. |
| **Code surface** | The RAG-store schema lives in the sibling `ndi-cloud-app` repo at `apps/web/lib/ai/db/`. The cloud-app side reads pgvector directly via `@vercel/postgres`. FastAPI doesn't currently touch the RAG store; it WILL when Stream 3.2 (`chat_usage_events`) lands. |

### ndi-cloud-node (AWS Lambda)

| Field | Value |
|---|---|
| **Used for** | All catalog reads, all auth (Cognito-backed login), all dataset metadata, all NDI Query DSL evaluation, all binary-document downloads (proxied via signed S3 URLs). |
| **Failure mode** | Circuit breaker opens after 5 consecutive failures (default `CLOUD_CIRCUIT_BREAKER_THRESHOLD`); cooldown 30s. While the breaker is open, every FastAPI request that needs the cloud returns `CloudUnreachable` typed error → 503 `cloud_unreachable`. |
| **Acceptable downtime?** | No — platform unusable. AWS SLO is the binding constraint. |
| **Code surface** | `backend/clients/ndi_cloud.py` (HTTP client + circuit breaker), `backend/clients/circuit_breaker.py`. |
| **Auth** | Bearer access-token (Cognito JWT) per-request, no service account; the user's session-stored token is decrypted and forwarded on the request. |

### AWS S3 (via signed URLs)

| Field | Value |
|---|---|
| **Used for** | Binary recording downloads. ndi-cloud-node returns a signed S3 URL; FastAPI forwards the URL to the client OR streams the bytes through (depending on size). |
| **Failure mode** | Binary downloads return 502. Catalog reads + metadata are unaffected. |
| **Code surface** | `backend/clients/_url_allowlist.py` enforces an allowlist of S3 hostnames before any FastAPI-side download proxy. The May 2026 audit (`test_download_from_off_allowlist_host_hard_rejects`) verifies the allowlist rejects non-S3 hosts even when ndi-cloud-node returns a redirect to one. |

### OpenTelemetry collector (optional)

| Field | Value |
|---|---|
| **Used for** | Trace export when `OTEL_EXPORTER_OTLP_ENDPOINT` is non-empty. Default: empty (tracing disabled). |
| **Failure mode** | Tracing dropped silently. No impact on application requests. |
| **Code surface** | `backend/observability/` (sender), `backend/middleware/request_id.py` (per-request id propagation). |

---

## Inbound dependencies (who calls FastAPI)

### Vercel-hosted ndi-cloud-app frontend (production + preview)

| Field | Value |
|---|---|
| **Used for** | Every `/api/*` request from the browser is proxied to FastAPI via Vercel `rewrites()`. Same for RSC-server-side fetches (`INTERNAL_API_URL`). |
| **Auth posture** | Cookie + CSRF — matches the FastAPI middleware contract. |
| **Branch awareness** | The cloud-app's `feat/experimental-ask-chat` branch routes `/api/*` to **this** experimental FastAPI env (`ndb-v2-experimental`) via the branch-aware rewrite. Main branch routes to production FastAPI. See ADR-005 in the cloud-app repo. |

### vh-lab-chatbot + shrek-lab-chatbot

| Field | Value |
|---|---|
| **Used for** | These two sibling chatbots historically read the same Postgres RAG index. Today they don't call FastAPI directly — they query their own embedding indices. Listed here for completeness because they share the Voyage API key (incident-prone: see the May 2026 leaked-credentials postmortem in the cloud-app repo). |

---

## Service-startup order

The FastAPI app's lifespan handler (`backend/app.py:lifespan`) starts services in this order:

1. **NdiCloudClient.start()** — opens the httpx pool. Lazy DNS, no
   eager call to the cloud.
2. **SessionStore** — instantiates with the Fernet key from settings.
3. **RateLimiter** — Redis-backed; lazy on first use.
4. **Ontology cache** — SQLite at `ONTOLOGY_CACHE_DB_PATH`, created if
   absent.

Shutdown is reverse order. If startup fails at any step, the container
crashes before serving the first request — by design (fail-loud).

---

## Update history

| Date | Change |
|---|---|
| 2026-05-15 | Initial draft (Stream 4.8 deliverable). |
