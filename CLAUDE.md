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
- **Infra**: Docker multi-stage, Railway Pro (1 replica, private Redis), GitHub Actions CI

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
- [docs/adr/](docs/adr/) — 13 ADRs (proxy backend, session cookies, Redis, dropping SQLite, refresh tokens — superseded by 008, React Router, summary-table enrichment, deprecate Cognito refresh, services HTTP client boundary, dataset-summary synthesizer, dataset provenance, grain-selectable pivot, cross-dataset facet aggregation)

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

Feature flags:
- `FEATURE_PIVOT_V1` (default `false`) — gate the Plan B B6e grain-selectable pivot at `GET /api/datasets/:id/pivot/:grain` (subject/session/element grains). When off the endpoint returns 503 and the frontend hides the nav. See ADR-012.

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

- `backend/tests/unit/` — 356 tests across error catalog, cloud client, circuit breaker, session store, CSRF, rate limiter, projection, query validation, cache, ontology, dependency graph, document/binary/openminds shape, dataset-summary synthesizer, catalog-summary enricher, dataset provenance aggregator, grain-selectable pivot, cross-dataset facet aggregator
- `backend/tests/integration/` — 29 tests covering routes end-to-end with respx-mocked cloud + fakeredis (including `/api/datasets/my` auth + cloud-proxy behavior added 2026-04-20)
- `backend/tests/contract/` — runs against dev cloud nightly
- `frontend/src/**/*.test.{ts,tsx}` — **206 vitest** covering error catalog surfaces, dataset-summary card (pill + tooltip + warnings footer), dataset provenance card, pivot view, catalog card, cite modal, use-this-data modal, summary table view, query builder + facet panel + output-shape preview, ontology-utils, safe-href, orcid, ExternalAnchor, Modal.
- `frontend/tests-e2e/` — Playwright scenarios for public catalog, auth, error recovery
- Coverage gate: 70% on backend unit+integration (enforced in CI via explicit --cov-fail-under=70). Lowered from aspirational 85% (2026-04-17) to match actual coverage measured at CI. Raise deliberately as coverage improves.
- E2E is manual-only — no CI workflow runs it (dropped 2026-04 per commit 3f7cdb7). Run `make test-e2e` locally before landing UI changes.
- **Known flake**: `backend/tests/unit/test_dependencies.py::test_ip_change_logs_warning_allows_request` fails when the full `backend/tests/` suite runs together (contract → integration → unit discovery order). Passes in isolation (`pytest backend/tests/unit/` alone → 356/356). Something in the integration suite's setup leaks state into a later unit test despite the function-scoped `fake_redis` fixture. Reproduces on main; CI passes with whatever order GitHub Actions uses. Fix is test-isolation surgery — tracked in `project_open-followups.md`.

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

- **Cognito access tokens are 1-hour TTL.** Sessions expire and force re-login. This is a known UX limitation — see ADR-008.
- **bulk-fetch is 500 docs max per call.** The summary table service batches with concurrency limit 3. Don't raise this without checking Lambda timeouts.
- **Redis under the hood is single-process TTL counters.** If we go multi-region, rate limiting will drift.
- **The Railway volume from v1 is NOT attached to v2.** v2 is deliberately stateless. Attaching one would violate ADR 004.
- **`xlsx` is sourced from SheetJS's CDN tarball**, not the npm registry. Version pinned at `https://cdn.sheetjs.com/xlsx-0.20.3/xlsx-0.20.3.tgz`. SheetJS published `0.18.5` as the last npm release; 0.19+ fixes (CVE-2023-30533 / CVE-2024-22363) only ship via the CDN URL, which is SheetJS's documented install method. Tradeoff: **Dependabot, `npm audit`, and GitHub's dependency graph do NOT track this URL**. Automated tripwire: `.github/workflows/xlsx-cdn-advisory-check.yml` polls [cdn.sheetjs.com/advisories](https://cdn.sheetjs.com/advisories/) weekly and opens an issue (labels: `dependencies`, `security`, `xlsx-advisory`) whenever a new CVE appears that isn't in the workflow's known-addressed allowlist. Manual re-check: trigger the workflow via `workflow_dispatch`.
- **`/api/facets` uses a 5-minute short-TTL fallback** rather than invalidation-on-publish (ADR-013). The primary strategy is "invalidate on dataset-publish" but no cloud-to-proxy notification path exists today. Until one ships, a freshly published dataset shows up on the query page's facet chips within ≤5 minutes, not instantly. `FacetService.invalidate()` is the dormant hook to wire into a future publish-notification flow.
- **PR-branch freshness is gated by CI, not by branch protection.** This is the permanent design on the Free-plan private repo — we are deliberately not upgrading to GitHub Team and not making the repo public. `.github/workflows/pr-branch-freshness.yml` fails with a clear error when a PR's base is behind `origin/main`, putting a red status on the PR. CI red is the signal: any human or agent that respects red-before-merge will catch stale bases. If someone ignores red CI, stale-base merges remain possible — that's an accepted residual risk, not an escalation path. The latent-conflict pattern that motivated this gate is documented in fix-main PR #23 (2026-04-18).
- **`FloatingPanel` is the canonical floating-UI primitive.** When you need a tooltip / popover / dropdown that shouldn't be clipped by a scrolling ancestor (`overflow-auto` on a table wrapper, `overflow-x: clip` on body, etc.), reach for `frontend/src/components/ui/FloatingPanel.tsx`. It portals the panel to `document.body` with `position: fixed`, anchors to an `anchorRef`, auto-flips above ↔ below based on viewport space, and re-anchors on scroll (capture phase — catches nested scrollers) + resize. Already used by the column-header info tooltip, the ontology-term pill tooltip, the summary-card warnings toggle, and `OntologyPopover`. New tooltips should NOT use raw `position: absolute` + Tailwind `top-*` / `bottom-*` styles — the summary-table scroll wrapper clips them.
- **Cloud bulk_fetch and ndiquery individual calls are 30s-bounded by the upstream Lambda.** `CLOUD_HTTP_TIMEOUT_SECONDS: float = 30.0` is deliberately aligned with the `apilambda.timeout: 30` in `ndi-cloud-node/api/serverless.yml`. Bumping it won't help with cold-cache slowness on 70k-doc datasets — Lambda dies regardless. For large-dataset paths, prefer the `required + optional(return_exceptions=True)` graceful-degrade pattern used in `summary_table_service._build_combined` so the shape still renders when optional enrichments time out.
- **Geist + Geist Mono are self-hosted** via `@fontsource-variable/geist` and `@fontsource-variable/geist-mono` (imported side-effect in `frontend/src/main.tsx` before `index.css`). No Google Fonts CDN — avoids the third-party DNS on first paint, keeps the site usable behind corporate firewalls, and is GDPR-clean. The CSS font-family tokens in `index.css` are `"Geist Variable"` and `"Geist Mono Variable"` (not `"Geist"`) — that's what fontsource registers.
