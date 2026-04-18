# CLAUDE.md — NDI Data Browser v2

Notes for Claude Code sessions working on this repo.

## What this is

Cloud-first React + FastAPI browser for NDI Cloud datasets. v2 is a complete rewrite of the v1 SQLite-download browser, architected for the indexed `classLineage` / `depends_on` / auto-injected-`isa` performance in ndi-cloud-node as of 2026-04-16.

**No SQLite dataset storage anywhere.** Every read hits the cloud directly. The only local state is:
- Redis: encrypted session tokens + rate-limit counters
- Ephemeral `/tmp` SQLite: ontology term cache (safe to lose)

## Stack

- **Backend**: Python 3.12, FastAPI, httpx HTTP/2, structlog, prometheus-client, cryptography (Fernet), redis-py
- **Frontend**: React 19, Vite 6, TypeScript strict, TanStack Query/Table/Virtual, React Router 7, Tailwind v4, uPlot
- **Infra**: Docker multi-stage, Railway Pro (2 replicas, private Redis), GitHub Actions CI

## Architecture snapshot

```
Browser ──/api/*──> FastAPI proxy ──Cognito Bearer──> ndi-cloud-node
                        │
                        ├── Redis (sessions, rate limits)
                        └── SQLite (ontology cache, /tmp)
```

See:
- [docs/architecture.md](docs/architecture.md) — full diagram + data flow
- [docs/workflows.md](docs/workflows.md) — every user workflow with failure modes
- [docs/error-catalog.md](docs/error-catalog.md) — 20 typed error codes
- [docs/operations.md](docs/operations.md) — deploy, rollback, incident response
- [docs/adr/](docs/adr/) — 8 ADRs (proxy backend, session cookies, Redis, dropping SQLite, refresh tokens — superseded by 008, React Router, summary-table enrichment, deprecate Cognito refresh, services HTTP client boundary)

## Workflow rules

1. **Never reintroduce SQLite dataset storage.** ADR 004 is the source of truth. If a feature seems to need it, consult ADR 004 first.
2. **Every error goes through `backend/errors.py`.** No bare `Exception` reaches a router. `tests/unit/test_errors.py` gates the catalog.
3. **Services never do HTTP to ndi-cloud-node directly; that stays in `clients/ndi_cloud.py`.** Services also never import `fastapi`. External ontology lookups are a documented exception — see ADR-009.
4. **Session tokens never reach JavaScript.** Opaque session-id cookie only. ADR 002.
5. **Edits to the error catalog are breaking API changes.** Bump the code version and announce.
6. **The SQLite ontology cache is OK to lose at any time.** Never rely on its durability.

## Common commands

```bash
# Setup + run
make install         # venv + deps for backend and frontend
make backend         # uvicorn on :8000
make frontend        # vite on :5173

# Tests
make test            # pytest (unit + integration) + vitest
make test-backend-cov
make test-e2e        # Playwright (manual-only; no CI gate)

# Quality gates
make lint            # ruff + mypy + ESLint + tsc
make typecheck       # TypeScript + mypy

# Build + perf
make build           # frontend build + Docker image
make lighthouse      # Lighthouse against a local preview build
make fixtures-refresh # re-record pinned E2E JSON fixtures from prod
```

## Environment

Required env vars (see `backend/.env.example`):
- `NDI_CLOUD_URL`
- `REDIS_URL`
- `SESSION_ENCRYPTION_KEY` (Fernet key) — generate with: `python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"`
- `CSRF_SIGNING_KEY` (32 bytes hex) — generate with: `python -c "import secrets; print(secrets.token_hex(32))"`

Everything else has sensible defaults.

## Deployment

- **Railway project:** `ndi-data-browser-v2` (dedicated, separate from v1's project — `81a57456-ae9a-47d0-98ef-2b5463f4815b`)
- **Service:** `ndb-v2` (`b55c2cb8-c0a8-4a96-a91e-6e7aefec6917`)
- **Redis:** `Redis` (redis:7-alpine, private networking only, internal `redis.railway.internal:6379`)
- **Public URL:** https://ndb-v2-production.up.railway.app
- **Auto-deploys** from `main` branch of `Waltham-Data-Science/ndi-data-browser-v2`
- **Health check:** `GET /api/health/ready`
- **Replicas:** 1 (per `infra/railway.toml`; scaled down 2026-04 per commit 3f7cdb7 for rate-limit correctness)

v1 continues to serve `ndi-data-browser-production.up.railway.app` in its own Railway project (unchanged). v2 runs in parallel; cutover is a future step.

## Testing

- `backend/tests/unit/` — 82 tests across error catalog, cloud client, circuit breaker, session store, CSRF, rate limiter, projection, query validation, cache
- `backend/tests/integration/` — 15 tests covering routes end-to-end with respx-mocked cloud + fakeredis
- `backend/tests/contract/` — runs against dev cloud nightly
- `frontend/tests-e2e/` — Playwright scenarios for public catalog, auth, error recovery
- Coverage gate: 85% on backend unit+integration (enforced in CI)
- E2E is manual-only — no CI workflow runs it (dropped 2026-04 per commit 3f7cdb7). Run `make test-e2e` locally before landing UI changes.

## Cloud API reference

The 2026-04-16 cloud capabilities we depend on:

| Endpoint | What we use it for |
|---|---|
| `POST /auth/login` | Per-user Cognito auth — token stored encrypted in Redis |
| `GET /datasets/published` | Catalog page |
| `GET /datasets/unpublished` | `/my` authenticated page |
| `GET /datasets/:id` | Dataset detail |
| `GET /datasets/:id/document-class-counts` | Class breakdown bar chart |
| `POST /ndiquery` | Everything class-filtered; scope accepts CSV of dataset IDs |
| `POST /datasets/:id/documents/bulk-fetch` | Batched detail hydration for tables (max 500/call) |
| `GET /datasets/:id/documents/:docId` | Single doc detail |

Cloud auto-injects `isa` on field queries and has indexed `depends_on` — we rely on both. If either regresses, summary table performance will fall off a cliff.

## Gotchas

- **Cognito token TTL is 1 hour.** We don't have a refresh endpoint yet (ADR 005 notes this); sessions expire and force re-login. When Steve ships `/auth/refresh`, the code in `backend/auth/token_refresh.py` already handles it — we just stop the no-op in `backend/clients/ndi_cloud.py::refresh()`.
- **bulk-fetch is 500 docs max per call.** The summary table service batches with concurrency limit 3. Don't raise this without checking Lambda timeouts.
- **Redis under the hood is single-process TTL counters.** If we go multi-region, rate limiting will drift and session refresh locks could race.
- **The Railway volume from v1 is NOT attached to v2.** v2 is deliberately stateless. Attaching one would violate ADR 004.
