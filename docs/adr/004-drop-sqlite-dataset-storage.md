# ADR 004 — Drop SQLite dataset storage entirely

**Status:** Accepted, 2026-04-16

## Context

v1 downloaded every browsed dataset into a local SQLite file (~/.ndi/did-sqlite.sqlite) so NDI-python's `Dataset` class could answer queries instantly. This was engineered because `ndi-cloud-node` could not efficiently answer:
- "What classes are in this dataset?"
- "Give me all subjects."
- "Which probes depend on this subject?"

Those questions required either a full document scan (unindexed) or traversal in application code.

Between 2026-04-15 and 2026-04-16, `ndi-cloud-node` shipped:
- `classLineage` denormalized field + compound index `{dataset, classLineage}`.
- `GET /document-class-counts` aggregation.
- `POST /ndiquery` with multi-dataset scope.
- `POST /bulk-fetch` for up to 500 doc IDs.
- Auto-injection of `isa` on field queries, so every field search benefits from the `classLineage` index.
- Compound + single-field indexes on `depends_on`.

Net effect: every query pattern SQLite served is now indexed in the cloud and returns in seconds, even cloud-wide.

## Decision

Remove SQLite dataset storage from v2 entirely. Keep the SQLite ontology term cache (it's a tiny disk cache for external ontology lookups, unrelated to dataset storage).

## Rationale

1. **Cloud is fast enough.** Every v1 pattern (class counts, class filtering, property search, dependency traversal) has an indexed cloud counterpart. Measured: seconds for cloud-wide searches, sub-second for single-dataset.
2. **Drops ~1,200 lines of code.** `dataset_manager.py` (729), `CloudDatasetAdapter` (≈400), and the download routes, status polling, prefetch, and lock management all go.
3. **Stateless backend.** No Railway volume dependency. The v2 can deploy anywhere: Fly, Render, GCP Cloud Run, wherever. This unlocks flexibility for future infra decisions.
4. **Simpler operational model.** No "is this dataset cached?" state to reason about. Every user sees the same view, always.
5. **Eliminates a class of bugs.** The v1 team has been debugging race conditions in the download pipeline for weeks (stale locks, status JSON out of sync with SQLite, background thread lifecycle). All of that goes away.

## Consequences

- Truly offline users are not supported by v2. They can stay on v1, or use NDI-python directly. This is acceptable: the v2 audience is scientists with reliable internet.
- Users with large v1 SQLite caches are asked to re-browse; there's no migration path. With cloud speed, re-browsing is essentially free.
- No "Download for offline" button in v2. If feedback demands it, revisit in v2.1 with a streamlined JSON export endpoint.

## Non-consequences

- Binary decoding (NBF/VHSB/image/video) is unchanged — it fetches signed file URLs from the cloud and processes in Python. It never used SQLite.
- Ontology SQLite cache is unchanged — it's a small local cache for external ontology services (EBI OLS, NCBI, SciCrunch), not for dataset data.

## Alternatives considered

- **Keep SQLite as opt-in.** Rejected: the code is huge; "optional" still means maintaining it. Drop entirely; revisit only if users demand it.
- **Replace SQLite with a smarter in-memory cache.** Rejected: TTL caches at the proxy level (class-counts 5m, dataset metadata 1m) are enough. No need for a query-result cache since cloud is fast and results are dynamic.
