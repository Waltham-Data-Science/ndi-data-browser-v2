# ADR 007 — Summary-table enrichment via sibling-class fetch + client-side join

**Status:** Accepted, 2026-04-16

## Context

Real NDI Cloud datasets split subject metadata across two classes:
- `subject` — minimal leaf: `local_identifier`, `description`
- `openminds_subject` — per-attribute docs (one each for species, sex, strain, age) linked back to the subject via `depends_on`

Similarly:
- `element` — minimal leaf
- `probe_location` — location metadata linked via `depends_on`

The plan's original 2-step orchestration (ndiquery → bulk-fetch → client-side project) handles the leaf class but misses the enrichment.

## Decision

When building a summary table for class X, if an enrichment companion class Y exists (defined in `_ENRICHMENT_FOR`), fetch all Y documents in the same dataset in parallel, index them by `depends_on.value`, and merge into the primary docs as `_enriched_list`. The projection functions (`_row_subject`, etc.) check both the primary `data` and each enrichment entry's `data.openminds.fields.*`.

Why "fetch all, filter locally" instead of a chained `depends_on` ndiquery?
- `depends_on` is a single-value indexed filter; batching a list across IDs isn't supported directly.
- For typical dataset sizes (tens to thousands of subjects), the extra fetch is cheap relative to the latency win of avoiding N chained queries.

## Consequences

- 1–2 extra cloud calls per summary-table build (ndiquery + bulk-fetch on the sibling class).
- Rows without sibling metadata render null for enriched columns (visible as "—" in the UI) — gracefully degraded.
- Adding a new enriched class is a one-line change to `_ENRICHMENT_FOR`.

## Not considered

- Server-side join in ndiquery. The cloud does not support it. If Steve adds a field-projection endpoint later, this pattern simplifies dramatically.
- Dataset-scoped LRU for enrichment docs. Possible future optimization if we see repeat builds.
