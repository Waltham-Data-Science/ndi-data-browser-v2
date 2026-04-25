# Changelog

All notable changes to the NDI Data Browser. v2 is a full rewrite; this changelog starts from the rewrite.

## [2.0.0] — Unreleased (2026-04)

### Audit 2026-04-23 (P0 + P1 fixes, PR #76)

#### Fixed
- **SSRF in `download_file`** (#49): non-http(s) schemes hard-rejected; off-allowlist hosts hard-rejected with `BinaryNotFound`. Removed the soft-observe `DOWNLOAD_ALLOWLIST_ENFORCE` flag — hard-reject is unconditional.
- **CDN cache cross-user leak** (#50): `CacheControlMiddleware` checked the wrong cookie name (`ndi_session=` vs the real `session=`). Fixed; `Vary: Cookie, Accept-Encoding` now emitted unconditionally as defense in depth.
- **Login CSRF** (#53): dropped `/api/auth/login` from the CSRF middleware exempt list — the frontend has always sent the token, so the exemption was just an open door.
- **Visualize router path injection** (#54): `DistributionBody` body fields now pattern-validated against `DATASET_ID_PATTERN` + dotted-attribute / class-name regexes.
- **`do_logout` cookie clear** (#55): cookies clear unconditionally in a `finally`; cloud-logout failures swallowed.
- **Session corrupt-payload resilience** (#56): drifted/decryption-failing Redis blobs are soft-deleted, returning `None` (re-login) instead of crashing with 500.
- **OntologyPopover URL safety** (M3): `data.url` now passes through `safeHref` before becoming an `<a href>`.

#### Performance
- **Backend cold start: 3.6s → 0.4s** (#57). scipy / numpy / Pillow lazy-imported inside the methods that use them.
- **Dropped `pandas` runtime dep** (#58) — declared but never imported, ~30 MB image bloat.
- **OntologyCache persistent SQLite connection** (#59): one long-lived connection per process; previously every `get`/`set` opened a fresh connection + ran `PRAGMA journal_mode=WAL` under a lock.
- **DatasetSummaryService concurrency 3 → 6** (#60), aligning with `SummaryTableService`.
- **Facet cache pre-warmer** (#61): runs every 4 min in production so the user-visible request is always a cache hit instead of paying a ~300-cloud-call cold build.
- **Cancel-on-disconnect on long-running routes** (#62): `/summary`, `/provenance`, `/pivot`, `/tables/combined` now race the service coroutine against `request.is_disconnected()` and cancel the inner task on hangup. New `routers/_cancel.py` helper.
- **Frontend main bundle: 86.6 KB gz → 22.2 KB gz** (#52). React.lazy on 9 routes (Login, Query, MyDatasets, DocumentDetail, etc.); home / datasets / dataset-detail stay eagerly imported.
- **PivotView virtualized** (#63) via a new shared `VirtualizedTable` primitive extracted from `SummaryTableView`. 5000-row pivot no longer freezes the tab.

#### Accessibility
- **Dataset-detail tab bar** (#65) now emits `aria-selected`, uses roving `tabindex`, and supports ArrowLeft/ArrowRight/Home/End keyboard navigation per the WAI-ARIA tab pattern.

#### SEO / UX
- **Dynamic document.title** on dataset detail (#67): the real dataset name is stamped into `document.title` once the query resolves, so LinkedIn/Slack share previews and screen-reader announcements are informative instead of generic.
- **MyDatasets row memoization** (#64 partial): filter-chip toggles no longer re-render the entire visible table.

#### Cleanup
- **Stripped 168 dead `dark:*` Tailwind classes** across 24 files (#68). The app forces `color-scheme: light`; the dead classes never activated and were misleading.
- Aligned `index.html` `color-scheme` to `light` (was `light dark`, contradicted `index.css`).

#### CI / hygiene
- **`hygiene` CI job** (#51) rejects macOS Finder/iCloud "Filename 2.ext" duplicate files. `.dockerignore` updated as belt-and-suspenders.
- **Frontend coverage gate** (#73) via `@vitest/coverage-v8` with thresholds calibrated to measured baseline.
- **`backend/tests/`** excluded from the production Docker image (#74). Test fixtures and test-only Fernet keys no longer ship.
- **Bundle-size script** correctly excludes lazy route chunks; "initial paint" budget now 148.5 KB gz (61.5 KB headroom).

#### Docs
- W14 transparent-refresh stub replaced with ADR-008 deprecation note (#69). Architecture/README/workflows aligned with no-refresh-flow reality.
- `operations.md` CI table corrected (#71): rollout-health.yml ghost removed; stale `pytest 212` count removed; pr-branch-freshness + xlsx-cdn-advisory listed.
- E2E spec references that pointed at non-existent files (#70) now cite `auth.spec.ts` where coverage exists; gaps marked as tracked follow-ups.
- `infra/dashboards/` claim downgraded (#72) — directory was empty; doc points at real Prometheus metric names with a tracked follow-up to commit Grafana JSONs.
- ADR-005 Status header consolidated (M29). `error-catalog.md` filename ref corrected (M28). pyproject `addopts --cov=.` removed (M39).

#### Known follow-ups
- **#64** — full items-based virtualization for MyDatasetsPage admin scope (memoization shipped, virtualization deferred).
- **#66** — full structural port of `OntologyPopover` to `<FloatingPanel>` (security fix shipped via `safeHref`; structural port needs hover-delay test coverage first).
- **#72** — export Grafana dashboards to JSON and commit to `infra/dashboards/`.

### Audit 2026-04-23 (consolidated audit doc, PR #75)

#### Added
- `docs/reviews/Audit_2026-04-23.md` — 86 audit findings (3 CRITICAL, 23 HIGH, 40 MEDIUM, 20 LOW) across security + correctness, frontend perf + bundle + a11y, backend perf + caching + deps, tests + docs + CI + infra.

---

## [2.0.0] — Pre-audit baseline

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
