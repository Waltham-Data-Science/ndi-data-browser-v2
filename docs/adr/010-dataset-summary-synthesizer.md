# ADR 010 — DatasetSummary synthesizer backend + landing card

**Status:** Accepted, 2026-04-17
**Supersedes:** —
**Related:** Plan B amendment [§4.B1](../plans/spike-0-amendments.md#b1--dataset-detail-landing-card--reshape-dont-cancel), ADR-003 (Redis sessions), ADR-007 (summary-table enrichment), ADR-009 (services HTTP boundary)

## Context

Researchers landing on a dataset detail view need a one-glance synthesis: "How many subjects? What species? Which brain regions? Who wrote this? What's it licensed under?" The cloud exposes the raw material — `GET /datasets/:id`, `GET /datasets/:id/document-class-counts`, `POST /ndiquery`, and `POST /datasets/:id/documents/bulk-fetch` — but nothing that aggregates across those calls. NDI-python's `ndi.fun.doc_table.subject` has the field-access logic encoded in pandas-oriented per-document tables, not dataset-wide rollups.

Plan B initiative B1 is to fill the gap on the v2 proxy side, not at the cloud. The amendment doc §2 explains the trade: cross-team is what we learned to avoid (ADR-008 / token_refresh), and the frontend would pay N × synthesizer cost per catalog render, so a server-side synthesizer with Redis caching beats either alternative.

## Decision

Introduce `backend/services/dataset_summary_service.py::DatasetSummaryService` — a pure-logic service (no `httpx`/`requests`/`aiohttp`/`urllib3` import; ruff enforces this via ADR-009) that composes the four cloud primitives into one `DatasetSummary` Pydantic model. Expose it at `GET /api/datasets/:id/summary`. Mirror the shape in `frontend/src/types/dataset-summary.ts`. Render it as the landing card in `frontend/src/components/datasets/DatasetSummaryCard.tsx`, mounted atop `DatasetOverviewCard` in the dataset detail sidebar.

### Data-shape commitments (amendment doc §3)

- **Structured, not prose.** Counts, biology pills, anatomy pills, probe-type chips, scale block, citation block. No sentence-summary primary. Researchers recognize the schema vocabulary; the MATLAB tutorial and NDI-python both yield structured output, never prose.
- **`[]` vs `null` carry different meaning.** Empty array = the extraction ran and found nothing. `null` = extraction did not run (e.g. zero subjects → species/strains/sexes stay `null`, not `[]`). The frontend card renders "—" for `[]` and "Not applicable" for `null`.
- **Full strings preserved, never truncated.** `SubjectLocalIdentifier`-style long IDs survive verbatim. Tooltip + hover on ontology pills reveal the ID; click opens the resolver (OBO, SciCrunch, WormBase, PubChem).
- **`schemaVersion: "summary:v1"`.** A literal enum so any future shape change forces a bump and a cache invalidation.

### Cache key and TTL

- Key: `summary:v1:{dataset_id}:{user_scope_for(session)}`. `user_scope_for` is the PR-3 per-user cache scope (`"public"` for unauthenticated, `"u:<16-hex>"` for authenticated). Two authenticated users cannot share a cached entry — protects against the cloud starting to return per-user variation at any point.
- TTL: **5 minutes**, not 1 hour. Amendment §4.B3 rewrote the earlier "aggressive 1h TTL" language in favor of freshness bounded to minutes. A dataset published at T=0 must become visible on its detail page's summary card within minutes, not an hour.
- Cached values are the serialized `DatasetSummary.model_dump(mode="json")`. A cloud failure during compute propagates — `RedisTableCache.get_or_compute` intentionally does not write on exception.

### Short-circuit-if-cloud-DatasetListResult-lands story

[ndi-cloud-node issue #15](https://github.com/Waltham-Data-Science/ndi-cloud-node/issues/15) proposes a 5-line expansion to return `species`, `brainRegions`, `numberOfSubjects`, `neurons`, `associatedPublications` directly on `GET /datasets/published` and `GET /datasets/:id`. If/when that ships, the synthesizer will short-circuit: read the cloud-provided fields instead of issuing the ndiquery fanout, with no caller-visible shape change (the resolver still dedupes and enriches labels). The current `_build` function is intentionally structured so a future patch can branch on cloud-provided fields before starting Stage 2.

## Rationale

1. **Server-side synthesis beats frontend-side.** Frontend-side would cost N × synthesis per catalog render; server-side amortizes via Redis and returns in one round trip.
2. **Own-proxy synthesis beats cloud-side synthesis.** We don't block on Steve's roadmap. The cross-team dependency failure mode (ADR-008 / token_refresh) is exactly what this avoids.
3. **Port NDI-python's field-access logic, not its orchestrator.** The per-document JSON paths in `ndi.fun.doc_table.subject` are hardened against real datasets; reinventing them would be weeks of work. But NDI-python's orchestrator uses plain `requests` and a local SQLite (violates ADR-004), so we keep only the path logic and wrap it in v2's httpx + proxy idiom (via `clients/ndi_cloud.py`).
4. **Reuse the summary-table enrichment attach pattern (ADR-007).** `_attach_openminds_enrichment` + `_openminds_name_and_ontology` dispatch live in `services/summary_table_service.py`. The synthesizer imports and reuses them — Schema-A/B dispatch stays in one place.
5. **Explicit ruff ban enforcement (ADR-009).** `services/dataset_summary_service.py` does not import `httpx` / `requests` / `aiohttp` / `urllib3`. `tests/unit/test_services_http_boundary.py` asserts this by AST-walking the services directory.

## Consequences

- One new public route (`GET /api/datasets/:id/summary`), one new Pydantic type (`DatasetSummary`), one new Redis cache bucket (`dataset_summary_cache`, 5-minute TTL).
- A rename: the old `interface DatasetSummary` in `frontend/src/api/datasets.ts` (the raw `IDataset` cloud shape) is now `interface DatasetRecord`. Callers: `DatasetCard.tsx`, `DatasetDetailPage.tsx`, `useDataset`, `usePublishedDatasets`, `useMyDatasets`.
- Every summary build spends 2 additional cloud calls on datasets with 0 subjects (none — the service short-circuits) and 2–5 on datasets with subjects (ndiquery for openminds_subject/probe_location/element then their bulk-fetches, all concurrency-3 capped via Semaphore).
- `extractionWarnings` surface label-without-ontology fallbacks explicitly, visible in the debug tooltip on the card. Real datasets hit this on Haley's `GeneticStrainType` (empty `preferredOntologyIdentifier`) — so researchers see a machine-readable trail of what the synthesizer chose.
- `computedAt` is visible ("Last computed Xm ago"). Stale reads remain bounded to the 5-minute TTL.
- Coverage gate: the new service sits at 91% line coverage — above the 85% bar set for new backend code.

## Alternatives considered

- **Cloud-side synthesizer endpoint.** Rejected: cross-team blocker, and the design-doc shape that would have supported projection (`POST /documents/query`) did not ship (amendment §1). Plan B would have been gated on Steve.
- **Frontend-side synthesis via parallel fetches.** Rejected: N × synthesizer cost per catalog render (B2). The amendment §2 memo calls this out.
- **Port NDI-python as a subprocess.** Rejected: requires the full `CloudClient` (plain `requests`, no circuit breaker) and `downloadDataset` (violates ADR-004). The cost-to-benefit is negative.
- **Prose-first landing card.** Rejected: amendment §4.B1 drops prose as the primary. Researchers recognize the schema vocabulary; NDI-matlab has zero prose output anywhere. Prose as a frontend-only derived render on top of the structured shape remains open.

## References

- [amendment §4.B1](../plans/spike-0-amendments.md#b1--dataset-detail-landing-card--reshape-dont-cancel)
- [amendment §3 shape](../plans/spike-0-amendments.md#3-datasetsummary-data-shape)
- [Spike-0 Report B](../plans/spike-0-reports/spike0-B-ndi-python.md) — NDI-python `doc_table.subject` extraction patterns.
- [Spike-0 Report D](../plans/spike-0-reports/spike0-D-schema-and-v1.md) — DID-Schema canonical JSON paths.
- ADR-003 (Redis sessions + cache), ADR-007 (summary-table enrichment), ADR-009 (services HTTP boundary).
