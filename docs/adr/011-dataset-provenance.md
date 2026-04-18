# ADR 011 — Dataset provenance (branchOf + depends_on aggregation)

**Status:** Accepted, 2026-04-17
**Supersedes:** —
**Related:** Plan B amendment [§4.B5](../plans/spike-0-amendments.md#b5--lineage--aggregate-in-our-backend--surface-branchof), ADR-003 (Redis sessions), ADR-009 (services HTTP boundary), ADR-010 (dataset-summary synthesizer)

## Context

Researchers landing on a dataset detail page need to understand where the dataset came from and how it relates to the rest of the corpus: was this dataset forked from another one (`IDataset.branchOf`)? Have other datasets been forked off it (`GET /datasets/:id/branches`)? When documents inside this dataset declare `depends_on` edges, which *other* datasets do those edges point at?

The cloud exposes the primitives — `GET /datasets/:id` carries `branchOf`, `GET /datasets/:id/branches` lists children, and each document has a `data.depends_on[].value` array of ndiIds — but nothing aggregates across documents at dataset scope. Plan B amendment §4.B5 directs us to aggregate in the v2 proxy, mirroring the ownership split we made for the dataset-summary synthesizer (ADR-010).

Crucially, the cloud *also* ships a field called `classLineage` (cloud PR #9), which is *class-ISA lineage* — a document's superclass chain (e.g. a `spikesorting` doc's `["spikesorting", "element", ...]`). That is a **different concept** from "dataset derivation lineage" and uses the same word. Naming this v2 feature "lineage" unqualified would be a guaranteed confusion between two unrelated ideas.

## Decision

Introduce `backend/services/dataset_provenance_service.py::DatasetProvenanceService` — a pure-logic service (no `httpx`/`requests`/`aiohttp`/`urllib3` import; ruff enforces this via ADR-009) that composes three cloud primitives into one `DatasetProvenance` Pydantic model:

1. `GET /datasets/:id` → `branchOf` (parent relationship)
2. `GET /datasets/:id/branches` → list of child datasets
3. For every document class reported by `GET /datasets/:id/document-class-counts`:
   - `POST /ndiquery isa=<class> scope=<dataset_id>`
   - `POST /datasets/:id/documents/bulk-fetch` (concurrency 3)
   - Scan each doc's `data.depends_on[].value` ndiIds
   - Dedupe ndiIds, then for each unique one, `POST /ndiquery exact_string base.id=<ndi> scope=public|all` to resolve the owning dataset. Emit one aggregated `DatasetDependencyEdge` per `(sourceDataset, targetDataset, viaDocumentClass)` with `edgeCount = |docs carrying that edge|`.

Expose at `GET /api/datasets/:id/provenance`. Mirror the shape in `frontend/src/types/dataset-provenance.ts`. Render as a sidebar card `frontend/src/components/datasets/DatasetProvenanceCard.tsx` below `DatasetOverviewCard` on the dataset detail page.

### Vocabulary lock

We use **"dataset provenance"** or **"derivation graph"** in code, tests, UI, and documentation. We do **not** use "lineage" unqualified — that word is reserved for the cloud's `classLineage` (class-ISA lineage) to avoid a naming clash. Internal uses in the code (function names, log events, Redis key prefix) use `provenance`; the card title reads "Dataset provenance"; empty states read "Not a branch" / "No branches" / "No cross-dataset dependencies".

### Schema decisions

- **`branchOf: str | None`** — `None` means this dataset is not a branch. `[]` doesn't apply here because it's a one-parent relationship.
- **`branches: list[str]`** — `[]` means this dataset has no child forks. Pure list of child dataset IDs; richer metadata (branch names, timestamps) is available via `GET /datasets/:id` on each child and intentionally not duplicated here.
- **`documentDependencies: list[DatasetDependencyEdge]`** — aggregated cross-dataset edges. One edge per `(sourceDatasetId, targetDatasetId, viaDocumentClass)` tuple. Same-dataset references (a doc in DS1 points at another doc in DS1) are filtered out — that's the per-document dependency graph's concern (ADR-M5 / `dependency_graph_service`), not dataset provenance.
- **Source → target direction only.** We do not record "who depends on us" (reverse provenance). Reverse would require scanning every OTHER dataset for inbound refs, which is a different cost profile. A future "reverse provenance" endpoint can add it.
- **`schemaVersion: "provenance:v1"`** — literal enum so a future shape change forces a bump and cache invalidation. Schema version matches the Redis key prefix (`provenance:v1`) for parsability.
- **All Pydantic models use `model_config = ConfigDict(extra="forbid")`** — any unknown field on an inbound or deserialized payload raises. Matches the B1 convention.

### Cache key and TTL

- **Key:** `provenance:v1:{dataset_id}:{user_scope_for(session)}`. Same per-user scoping scheme as ADR-010, using `backend.auth.session.user_scope_for` (`"public"` for unauthenticated, `"u:<16-hex>"` for authenticated). Prevents two authenticated users from sharing a cached entry — insurance against the cloud starting to return per-user variation. **Prefix reserved: `provenance:v1`** — any schema bump becomes `provenance:v2`.
- **TTL: 5 minutes.** Matches ADR-010's freshness strategy (amendment §4.B3): a dataset published at T=0 must become visible on the provenance card within minutes, not hours. The per-request read from Redis is a single GET; the invalidation cost at write time is bounded.
- **Cache fills post-success.** `RedisTableCache.get_or_compute` does not write on producer exception. A transient cloud blip during a provenance build cannot pin a stale or partial blob for the full TTL.

### Why dataset-level aggregation rather than reusing the per-document dependency-graph service

`backend/services/dependency_graph_service.py` (ADR-M5) walks BFS *both directions* from a *single target document*, producing a node/edge graph constrained to one dataset (via `ndiquery scope=<dataset_id>`). The question it answers: "What depends on THIS `spikesorting` document, and what does it depend on, within this dataset?" It's the right tool for the per-document sidebar in the document detail view.

The dataset-provenance service asks a fundamentally different question: "At dataset scope, which OTHER datasets does this dataset's data point at, and how many refs of each document class?" Reusing the per-document walker would require running it once per document, which scales as O(N_docs × BFS_depth × fanout) — orders of magnitude more cloud traffic than the single-class-fanout approach. A coarser service with a different cost profile is the correct separation.

Both services coexist. The per-document walker is invoked from the document detail page; the dataset-provenance aggregator is invoked from the dataset detail page. They never call each other.

### Error handling

- `GET /datasets/:id` → 404 propagates as typed `NOT_FOUND` through the `NdiCloudClient` (pre-existing behavior).
- `GET /datasets/:id/branches` → failure **degrades gracefully** to empty `branches` with a warning log. An older cloud deployment that hasn't shipped the branches endpoint, or a transient 5xx, should not fail the entire provenance build. The observable behavior is identical to "this dataset has no branches".
- Per-class ndiquery / bulk-fetch failure → logged and skipped for that class. Other classes still contribute edges.
- Single-ndiId resolution failure → that ndiId is simply absent from the resolved mapping, its edge is dropped. Logged for observability.

### Safety caps

Bound pathological inputs without failing the build:

- `_MAX_CLASSES_WALKED = 25` — top-N classes by document count. Tail classes with `≤0` documents or class name `"unknown"` are skipped entirely.
- `_MAX_UNIQUE_TARGETS = 1000` — unique ndiIds we attempt to resolve. Exceeding truncates deterministically (sorted).
- Concurrency: `Semaphore(3)` on bulk-fetch (matches `summary_table_service`), `Semaphore(8)` on ndiId resolution.

## Rationale

1. **Own-proxy aggregation beats cloud-side.** Mirrors the ADR-010 decision. No cross-team blocker, no blocking on cloud roadmap.
2. **Per-user cache scoping is free insurance.** Matches the PR-3 + ADR-010 scheme. Two users cannot share a provenance entry even if the cloud's output is currently user-invariant.
3. **5-minute TTL beats 1-hour TTL on freshness.** Same tradeoff as the dataset-summary synthesizer — compute is cheap post-cache, cache-miss cost bounded by the aggregator's concurrency caps. A freshly published dataset shows up on the card within 5 minutes, not 60.
4. **Vocabulary separation from the cloud's `classLineage`** is a deliberate, documented choice. Using "lineage" unqualified in v2 would create a naming collision the cloud already owns. "Provenance" is accurate (W3C PROV-DM uses it for derivation relationships) and distinct.
5. **Dataset-level service as a separate module** — `dataset_provenance_service.py` — rather than extending `dependency_graph_service.py`. Different grain, different cost, different cache strategy, different API surface. Combining them into one file would obscure the differences for callers.

## Consequences

- One new public route (`GET /api/datasets/:id/provenance`), one new Pydantic type (`DatasetProvenance`), one new Redis cache bucket (`dataset_provenance_cache`, 5-minute TTL, prefix `provenance:v1`).
- One new cloud-client method: `NdiCloudClient.get_dataset_branches`. Tolerates both `{datasets: [...]}` and a bare `[...]` response shape for forward compatibility.
- One new sidebar card on the dataset detail page, below `DatasetOverviewCard`. The existing sidebar structure is not restructured; the new card is appended. A card-render failure degrades silently (no `ErrorState`) so a slow or flaky aggregator never blocks the detail view.
- ADR-009 (`ruff` ban on `import httpx` in services) covers the new file — enforced by the existing `tests/unit/test_services_http_boundary.py` AST walk.
- No breaking change to `DatasetSummary` (shape untouched) or the per-document `DependencyGraphService` (unmodified).

## Alternatives considered

- **Cloud-side dataset-provenance endpoint.** Rejected: cross-team blocker (same reasoning as ADR-010 rejection of the cloud-side synthesizer). The primitives exist; the aggregation is cheap on our side.
- **Extend `DependencyGraphService` to produce dataset-level rollups.** Rejected: the BFS walker is per-document. Reusing it at dataset scope would cost O(N_docs × BFS_depth × fanout) instead of O(N_classes × avg_docs_per_class). Different cost profile → different service.
- **Reuse the `summary:v1` Redis prefix.** Rejected: a summary-schema bump would unnecessarily invalidate provenance, and vice versa. Separate prefixes let each ADR bump its own schema independently.
- **Call the feature "lineage".** Rejected: naming clash with cloud `classLineage` (class-ISA lineage). Vocabulary locked to "provenance" / "derivation" in code + UI + docs.
- **Surface `branchOf` inside `DatasetSummary` instead of a separate endpoint.** Rejected: the contract for `DatasetSummary` is frozen (amendment §3) and extending it would pull the B5 scope into a B1 mutation. A separate endpoint keeps the shapes independent and makes the Redis invalidation story cleaner.

## References

- [amendment §4.B5](../plans/spike-0-amendments.md#b5--lineage--aggregate-in-our-backend--surface-branchof)
- [Spike-0 Report A](../plans/spike-0-reports/spike0-A-ndi-cloud-node.md) — cloud API inventory, `IDataset.branchOf`, `/branches` endpoint, `depends_on` semantics.
- ADR-003 (Redis sessions + cache), ADR-009 (services HTTP boundary), ADR-010 (dataset-summary synthesizer).
