# Changelog

All notable changes to the NDI Data Browser. v2 is a full rewrite; this changelog starts from the rewrite.

## [2.0.0] — Unreleased (2026-04)

### Added
- Cloud-first architecture: every read hits ndi-cloud-node directly, no SQLite dataset storage.
- Redis-backed sessions with Fernet-encrypted Cognito tokens, httpOnly cookie.
- CSRF double-submit protection on all mutations.
- Typed error catalog (20 codes) with stable frontend UI mapping (retry / login / contact_support / none).
- Cloud client with HTTP/2, pagination, retry+jitter, circuit breaker.
- Summary tables for subject, element (probes), element_epoch (epochs), combined (subject⋈element⋈epoch), treatments.
- Enrichment via `openminds_subject` / `probe_location` joined client-side.
- Cross-cloud query builder with `~` negation; `~or` rejected with typed error.
- "Appears elsewhere" cross-cloud reference search.
- Ontology term popover with 30-day SQLite cache across 13 providers.
- Binary data rendering: NBF/VHSB timeseries (uPlot), images (Pillow), video (signed URL), fitcurve (parametric evaluation).
- Observability: structlog JSON, `/metrics` Prometheus, OpenTelemetry spans stubbed, Sentry opt-in.
- Rate limiting per user: 120/min reads, 30/min ndiquery, 10/min bulk-fetch; 5/IP/15min login.
- CI: ruff + mypy + pytest, vitest, Playwright E2E, Docker build, pip-audit + npm audit.
- Infra: multi-stage Dockerfile, railway.toml, docker-compose for local dev.

### Changed
- SQLite dataset cache removed (ADR 004).
- Sessions: Postgres → Redis (ADR 003).
- Frontend router: React Router 7 kept (ADR 006).
- Observability: structlog + Prometheus from commit 1; OpenTelemetry added incrementally.

### Removed
- `dataset_manager.py` download pipeline (729 lines).
- `CloudDatasetAdapter` bridge (~400 lines).
- Startup dataset prefetch.
- Railway persistent volume for datasets.
- Offline download UI and backend routes.

## [1.x]

v1 changelog archived in the v1 repo under [CHANGELOG.md](../ndi-data-browser/CHANGELOG.md).
