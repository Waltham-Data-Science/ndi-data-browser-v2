# Architecture

## Summary

NDI Data Browser v2 is a stateless proxy+enricher in front of ndi-cloud-node. The browser talks only to our FastAPI; FastAPI forwards requests to the cloud with the user's Cognito token, enriches binary and ontology data, and returns stable typed error codes.

The v1 SQLite dataset cache is gone. Every read is a cloud read.

## Component diagram

```
┌───────────────────────────────────────────────────────────────┐
│  Browser                                                       │
│                                                                │
│  React 19 + Vite + TanStack Query v5 + React Router 7          │
│  • One API client (src/api/client.ts) → our FastAPI            │
│  • httpOnly session cookie (JWT + refresh never in JS)         │
│  • Types auto-generated from FastAPI OpenAPI                   │
│  • axe-core clean, WCAG 2.1 AA                                 │
└──────────────────────────┬─────────────────────────────────────┘
                           │ /api/*  (credentials: include)
                           ▼
┌───────────────────────────────────────────────────────────────┐
│  FastAPI backend (Python 3.12)                                 │
│                                                                │
│  Request pipeline:                                             │
│    request-id → structlog → CORS → security-headers            │
│    → rate-limit → CSRF (mutations) → auth-dependency           │
│    → route handler                                             │
│                                                                │
│  Auth:                                                         │
│    • Redis session store (opaque session-id → encrypted JWT)   │
│    • Cognito access token refreshed transparently on 401       │
│    • Refresh protected by short-lived Redis lock               │
│                                                                │
│  Cloud client:                                                 │
│    • httpx.AsyncClient, HTTP/2, keep-alive, pool size 50       │
│    • Retry: 3 tries, exponential + jitter, only 5xx/network    │
│    • Circuit breaker: open after 5 fails, cool 30s, probe      │
│    • OpenTelemetry spans (M5+), Prometheus metrics (always)    │
│                                                                │
│  Enrichment:                                                   │
│    • Binary decode (NBF, VHSB, image via Pillow, video URLs)   │
│    • Ontology lookups with 13-provider registry + SQLite cache │
│    • Violin/box distribution math                              │
│                                                                │
│  Cache (in-process TTL):                                       │
│    • document-class-counts: 5m                                 │
│    • datasets list: 1m                                         │
│                                                                │
│  Error model:                                                  │
│    • Every error → typed BrowserError → stable JSON code       │
│    • No Python traceback leaks                                 │
└──────────────────────────┬─────────────────────────────────────┘
                           │ HTTPS/2, JWT in Authorization
                           ▼
┌───────────────────────────────────────────────────────────────┐
│  ndi-cloud-node  (https://api.ndi-cloud.com/v1)                │
│                                                                │
│  GET  /datasets, /datasets/published, /datasets/:id            │
│  GET  /datasets/:id/document-class-counts                      │
│  GET  /datasets/:id/documents, /documents/:docId               │
│  POST /datasets/:id/documents/bulk-fetch        (<=500 IDs)    │
│  POST /ndiquery   scope = public | private | all | CSV IDs     │
│  POST /auth/login, /auth/refresh, /auth/logout                 │
│                                                                │
│  Indexing (enables cloud-first arch):                          │
│    • {dataset, classLineage}          (isa, auto-injected)     │
│    • {depends_on.name, depends_on.value}                       │
│    • {dataset, 'data.document_class.class_name'}               │
└───────────────────────────────────────────────────────────────┘
```

## Data flow examples

### Dataset overview page (unauthenticated)

```
GET /api/datasets/:id                        ← frontend
  → cloud GET /datasets/:id                  ← backend (cache 1m)
GET /api/datasets/:id/class-counts           ← frontend
  → cloud GET /datasets/:id/document-class-counts  (cache 5m)
```

Two round-trips, both cached.

### Subjects summary table

```
GET /api/datasets/:id/tables/subjects        ← frontend
  → POST /ndiquery  {isa: "subject", scope: ":id"}  ← backend
    ← returns N subject IDs
  → POST /datasets/:id/documents/bulk-fetch
    {documentIds: [...]}                     (parallel batches of 500, max 3 concurrent)
    ← returns full subject docs
  → project fields (name, species, sex, strain, age, etc.)
  ← returns {columns, rows}
```

### Combined table (subjects ⋈ probes ⋈ epochs)

```
ndiquery isa=subject scope=:id                → subject IDs
bulk-fetch subjects                           → subject rows
ndiquery isa=probe AND depends_on=subject_ids → probe IDs   (indexed depends_on)
bulk-fetch probes                             → probe rows
ndiquery isa=epoch AND depends_on=probe_ids   → epoch IDs
bulk-fetch epochs                             → epoch rows
client-side join on depends_on                → combined rows
```

No SQLite. No local download. Typical completion: 1–3s.

### "Appears elsewhere" (cross-cloud)

Triggered from a subject detail page by `subject_a`:

```
POST /api/query/appears-elsewhere
  body: {documentId: <subject_a.id>, excludeDatasetId: <current>}
  →  POST /ndiquery
     {searchstructure: [{operation: "depends_on", param1: "*", param2: subject_a.id}],
      scope: "public"}          (or "all" if user is authenticated)
  →  returns docs across the entire cloud that depend on this subject
  →  group by datasetId, count, return {datasetId → count}
← frontend renders "Referenced by N docs across M other datasets"
```

### Session expiry with transparent refresh

```
frontend → GET /api/datasets/myorg
  backend:
    session cookie present → Redis lookup → decrypt tokens
    access_token expires_at < now → acquire Redis lock session:<id>:refresh
    POST cloud /auth/refresh {refreshToken}
      success → write new access_token to Redis, release lock, continue
      failure → delete session, release lock, raise AUTH_EXPIRED
    forward request with fresh access_token
  ← 200 with data
```

Frontend never sees the 401. No re-login prompt unless the refresh token itself is dead.

## Why FastAPI proxy (not direct browser → cloud)

1. **Token safety.** Cognito tokens never touch JavaScript. XSS → no credential theft.
2. **Enrichment.** Binary decoding (NBF/VHSB), ontology lookups, violin math are server-side Python.
3. **Single pane of glass.** Rate limiting, observability, error mapping live in one place.
4. **Refresh flow.** Transparent refresh requires server-side state (Redis lock, encrypted refresh token).

~20-50ms added per request; cloud is fast enough that p95 still hits budget.

## Why Redis (not Postgres)

Sessions are small (~500 bytes), short-lived (max 24h), and the only stateful thing we keep. Redis is:
- Lighter than Postgres for this use case.
- Already on Railway's plan.
- Naturally supports TTL expiration.
- Fast enough for the per-request session lookup (sub-ms).

Rate-limit counters also live in Redis (sliding window via sorted sets).

## Why no SQLite dataset storage

Cloud queries are now fast enough (classLineage index + auto-isa injection + indexed depends_on) that the SQLite cache's value dropped below its maintenance cost. The v1 `dataset_manager.py` (729 lines, file locks, background threads, status JSON, download races) and `CloudDatasetAdapter` (400 lines bridging cloud → NDI-python session) don't ship in v2.

Offline mode users: use NDI-python directly, or keep v1 running.

## Ports-and-adapters inside FastAPI

```
routers/*.py           — HTTP only, parse+validate, call service
services/*.py          — business logic, return typed models
clients/ndi_cloud.py   — cloud-facing I/O, typed per endpoint
auth/*.py              — session, login, Cognito exchange
cache/ttl.py           — in-process TTL cache abstraction
errors.py              — BrowserError hierarchy + handler
```

Routers never import httpx. Services never import FastAPI. Unit tests mock `ndi_cloud.py`; integration tests use real FastAPI + real Redis + respx-mocked cloud.

## Observability

| Signal | Tool | Start milestone |
|---|---|---|
| Structured logs (JSON) | structlog | M0 |
| Request IDs + per-request context | contextvars + structlog | M0 |
| Prometheus metrics (req count/latency/errors by route, cloud call latency, session count, breaker state) | prometheus-client | M0 |
| OpenTelemetry spans (request → service → cloud) | opentelemetry-sdk | M4 |
| Sentry (unhandled + top-level breaker trips) | sentry-sdk | M3 |
| Grafana dashboards as code | JSON in `infra/dashboards/` | M5 |

Starting light and adding as the system grows is deliberate — we don't need distributed tracing to ship M0.

## Performance budgets

Enforced in CI:

| Budget | Value |
|---|---|
| Initial JS bundle gzipped | ≤200 KB |
| Dataset list TTI (simulated 3G) | ≤2s |
| Dataset detail TTI | ≤3s |
| Document detail TTI (excl. binary) | ≤3s |
| Single-class summary table | <1s typical |
| Combined summary table | <3s typical |
| Cross-cloud query (bounded) | <5s |
| Document list first page | <2s |

## Deployment

Stateless → any platform works. Target: Railway (same as v1) because we already have an account and Redis add-on. Multi-stage Dockerfile; single container serves the API + static frontend build.

See [operations.md](operations.md) for deploy, rollback, incident response.
