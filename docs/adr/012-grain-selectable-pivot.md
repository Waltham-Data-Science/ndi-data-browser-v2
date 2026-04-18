# ADR 012 — Grain-selectable pivot v1 (Plan B B6e)

**Status:** Accepted, 2026-04-17
**Supersedes:** —
**Related:** Plan B amendment [§4.B6e](../plans/spike-0-amendments.md#b6e--grain-selectable-pivot-per-decision-2), ADR-007 (summary-table enrichment), ADR-009 (services HTTP boundary), ADR-010 (dataset-summary synthesizer), ADR-011 (dataset provenance)

**ADR numbering:** B5 (dataset provenance) and B6e (this) were dispatched in parallel and both initially drafted as `ADR-011`. B5 landed first, so this ADR renumbered to `012` at merge time. The document body is unchanged except this header.

## Context

Researchers want one cross-class, denormalized view of a dataset keyed by whichever entity they're currently thinking about: "show me one row per subject and its species/strain/sex"; "show me one row per session and roll up subjects"; "show me one row per probe with its location and cell type". NDI-matlab's workflow spells this out in `ndi.fun.docTable.subject|probe|epoch` + `ndi.fun.table.join` (Report C §1.1–§1.3), producing wide denormalized tables per grain.

v2 already ships per-class summary tables (`/api/datasets/:id/tables/:className`) and a combined cross-class view (`/api/datasets/:id/tables/combined`). Those aren't grain-pivoted — they're either one class at a time or a fixed subject⋈element⋈element_epoch join. B6e adds a grain-selectable pivot as an explicit composition primitive, starting with the three grains every dataset has in practice.

## Decision

Introduce `backend/services/pivot_service.py::PivotService` — a pure-logic service (no `httpx`/`requests`/`aiohttp`/`urllib3` imports; ruff enforces this per ADR-009) that composes ndiquery + bulk-fetch into a grain-keyed pivot envelope. Expose it behind feature flag `FEATURE_PIVOT_V1` at `GET /api/datasets/:id/pivot/:grain`. Mirror the response shape as `PivotResponse` in `frontend/src/api/datasets.ts`. Render in `frontend/src/components/datasets/PivotView.tsx`, mounted as a sibling route under the `DatasetDetailPage` `<Outlet />` at `/datasets/:id/pivot/:grain`.

### Grain set in v1

- `subject` — 9-column projection (subject doc/local ID, session doc ID, species/strain/sex with ontology pairs).
- `session` — aggregated across subjects sharing a `base.session_id` (subject count, CSV-joined species/strain/sex rollups, CSV-joined subject-doc IDs).
- `element` — 9-column projection (probe doc ID, name, type, reference, probe-location + cell-type split via UBERON/CL prefix, subject cross-reference).

Exotic grains (e.g. `stimulus_presentation`, `daqsystem`) defer to v2 of the pivot. Amendment §4.B6e explicitly says *"handle obvious grains. Don't pre-solve exotic edges."*

### Cache key and TTL

- Key: `pivot:v1:{dataset_id}:{grain}:{user_scope_for(session)}`. `user_scope_for` is the PR-3 per-user cache scope — two authenticated users cannot share a cached pivot entry.
- TTL: **5 minutes**, matching the DatasetSummary cache. Freshness over economy (amendment §4.B3 rewrite).
- Dedicated Redis bucket (`pivot_cache`), so a pivot schema bump (`pivot:v1` → `pivot:v2`) does not invalidate `summary:v1` / `table:v4` / `provenance:v1` / `depgraph:v4` and vice versa.

### Feature-flag rationale

`FEATURE_PIVOT_V1: bool = False` by default. Rationale:

1. **Staged rollout under real traffic.** Row shapes are per-grain; subtle projection bugs are easier to fix before the endpoint becomes load-bearing. Enabling it on staging first is a single env flip.
2. **Bounded blast radius.** The pivot fans out ndiquery + bulk-fetch per-grain; datasets with thousands of subjects amplify cloud Lambda load. The flag lets us lift it off quickly if performance regressions surface.
3. **Frontend non-intrusion.** When the flag is off, `/pivot/:grain` returns 503; the `DatasetPivotNavGuard` probes once and hides the entire pivot surface — nothing new appears in the sidebar or detail layout. A researcher opening a direct-link URL sees a dedicated "feature disabled" card, not a generic error.

The backend reads the flag via `get_settings()` at each request — no startup logic changes (`app.py::lifespan` just wires the Redis cache bucket). Tests toggle via `monkeypatch.setenv` + `get_settings.cache_clear()`.

### Grain-restriction rationale (subject/session/element only in v1)

- **Coverage.** These three grains have ≥1 doc on every published dataset today (Dabrowska, Haley, Van Hooser). Datasets with only `stimulus_presentation` docs exist as a theoretical edge but aren't in the current corpus — deferring exotic grains per §4.B6e.
- **Shared enrichment.** All three grains use the same openminds_subject + probe_location enrichment pipeline that `summary_table_service` already implements. Adding them is a thin composition over existing helpers; we reuse `_openminds_name_and_ontology`, `_attach_openminds_enrichment`, `_probe_location_split`, `_probe_locations_for`, `_element_subject_ndi`.
- **Session as a derived grain.** We don't require a first-class `session` document class (not always present on older datasets). Instead we group subjects by `base.session_id` — matches the MATLAB/NDI-python conceptual tree (`dataset → sessions → subjects`).

### Frozen-contract flag acknowledgement

- `DatasetSummary` shape: READ-only for the grain-selector auto-population (`counts.*`). Not mutated.
- Detail page shared surface: we add a new sibling route under `<Outlet />`. `<aside>` grid unchanged. No new sidebar cards or modals.
- Redis prefix: `pivot:v1` claimed. Does not collide with `summary:v1`, `provenance:v1`, `table:v4`, `depgraph:v4`.

## Rationale

1. **Composition, not replacement.** Pivot reuses the enrichment helpers from `summary_table_service` + the cloud-plumbing pattern from `dataset_summary_service`. Schema-A/B dispatch, openminds attachment, probe-location splitting — all single source of truth.
2. **Grain-auto-populate from counts.** The frontend selector reads `DatasetSummary.counts` (already cached) — one extra call → zero: we piggyback on the landing-card request.
3. **Feature-flag-hide on 503.** 503 on `/pivot/subject` is the only signal the frontend needs to know the backend flag is off; a `DatasetPivotNavGuard` probes once and hides any wrapped nav element. No additional env var needs to cross the network boundary.
4. **Separate cache bucket.** `pivot_cache` isolates schema-version churn. Bumping `PIVOT_SCHEMA_VERSION` doesn't force a summary / table / provenance recompute.
5. **Coordination note for ADR numbering.** Resolved at merge time: B5 (dataset provenance) kept `011`; this ADR renumbered from `011` to `012` in the merge that landed B6e. No body content changed.

## Consequences

- One new public route (`GET /api/datasets/:id/pivot/:grain`), one new Pydantic type (`PivotResponse`), one new Redis cache bucket (`pivot_cache`, 5-minute TTL), one new React Router route (`/datasets/:id/pivot/:grain`), one new component (`PivotView`), one new hook (`useDatasetPivot`).
- Each pivot build spends 2 cloud calls for the subject/session grains (ndiquery + bulk-fetch for subjects, same pair for openminds_subject) and 4 for the element grain (element + subject + probe_location + openminds_subject). All bounded by `Semaphore(3)` — matches the other services' concurrency ceiling.
- Feature flag default is `false`. Flipping to `true` requires a deliberate env change in Railway after staging validation.
- Frontend's `DatasetPivotNavGuard` probes the pivot endpoint once per dataset to discover the flag state — caches hit warm via TanStack Query so the cost is bounded per page.
- B6a coordination (per-grain column defaults) — the pivot currently imports `frontend/src/data/table-column-definitions.ts`'s flat camelCase dictionary. Keys already match for subject/element grains. A TODO in `PivotView.tsx` flags the switch to B6a's per-grain export shape when it ships.

## Alternatives considered

- **Ship without a feature flag.** Rejected: pivot rollouts benefit from the ability to disable without a revert (see rationale §1/§2). Same posture as ADR-008's handling of a risky product change.
- **Extend the existing `/api/datasets/:id/tables/combined` endpoint.** Rejected: combined hard-codes the subject⋈element⋈element_epoch join. Grain-pivoting is an orthogonal concept; conflating the two routes hides the new semantics.
- **Cloud-side pivot endpoint.** Rejected: cross-team blocker, same reason as ADR-010's dataset summary synthesizer — Plan B doesn't gate on Steve.
- **Ship all six grains (probe, epoch, treatment included).** Rejected: amendment §4.B6e caps v1 at the "obvious" grains. Probe/epoch/treatment surfaces already live under `/api/datasets/:id/tables/:className` — the v1 scope is "the grains that need a pivot right now".

## References

- [amendment §4.B6e](../plans/spike-0-amendments.md#b6e--grain-selectable-pivot-per-decision-2)
- [Spike-0 Report B](../plans/spike-0-reports/spike0-B-ndi-python.md) — NDI-python `doc_table` paradigm.
- [Spike-0 Report C](../plans/spike-0-reports/spike0-C-ndi-matlab.md) — MATLAB per-grain tables + `ndi.fun.table.join`.
- ADR-007 (summary-table enrichment), ADR-009 (services HTTP boundary), ADR-010 (dataset-summary synthesizer).
