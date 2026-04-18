# ADR 013 — Cross-dataset facet aggregation (query page)

**Status:** Accepted, 2026-04-17
**Supersedes:** —
**Related:** Plan B amendment [§4.B3](../plans/spike-0-amendments.md#b3--query-page--port-matlab-filter-conventions--cache-for-freshness), ADR-003 (Redis sessions), ADR-009 (services HTTP boundary), ADR-010 (dataset-summary synthesizer), ADR-011 (dataset provenance), ADR-012 (grain-selectable pivot)

## Context

The query page needs to let researchers discover what's in the corpus at a glance: *"Which species are represented? Which brain regions? Which strains?"* The cloud has no cross-dataset DISTINCT / facet endpoint — `POST /ndiquery` searches within a single class-scope and returns documents, not rolled-up distinct values across every dataset in the catalog.

Plan B amendment §4.B3 directs us to aggregate these facets on the proxy side, mirroring the ownership split we made for the dataset-summary synthesizer (ADR-010) and provenance aggregator (ADR-011). The source material already exists: B2's catalog enricher embeds a `CompactDatasetSummary` per row in `GET /api/datasets/published`, and B1's full `DatasetSummary` is available per dataset. The remaining work is to aggregate distinct values across all of them.

Two decisions loomed large and are resolved below: **which cache strategy** (the earlier synthesis said 1-hour aggressive TTL; the amendment rewrote this to freshness-first), and **where the endpoint lives** (a new router, or folded into the existing query router).

## Decision

Introduce `backend/services/facet_service.py::FacetService` — a pure-logic service (no `httpx`/`requests`/`aiohttp`/`urllib3` import; ruff enforces this via ADR-009) that composes the catalog-list + per-dataset-summary services into one `FacetsResponse` Pydantic model.

Expose at `GET /api/facets` via a new router `facets_router` defined alongside the existing query router in `backend/routers/query.py`. Mirror the shape in `frontend/src/types/facets.ts`. Render as a sidebar card `frontend/src/components/query/FacetPanel.tsx` on the query page. Clicking a chip in that panel appends a `contains_string` filter to the `QueryBuilder`.

### Response shape — `FacetsResponse`

```
species: list[OntologyTerm]       # distinct across all published datasets
brainRegions: list[OntologyTerm]  # distinct across all
strains: list[OntologyTerm]       # distinct across all
sexes: list[OntologyTerm]         # distinct across all
probeTypes: list[str]             # distinct free-text bucket
datasetCount: int                 # how many datasets contributed
computedAt: str                   # ISO-8601
schemaVersion: "facets:v1"
```

`OntologyTerm` is reused from the dataset-summary synthesizer (ADR-010). `probeTypes` is the free-text bucket called out in amendment §3 (no canonical ontology).

### Cache strategy — freshness > TTL economy (amendment §4.B3, CRITICAL)

The earlier in-chat synthesis recommended an "aggressive 1-hour TTL." The amendment rewrote this. The reason: with cloud PR #9 (indexed `isa`) + PR #11 (scope=datasetId) landed, per-dataset-summary compute cost is already low and Redis-cached for 5 minutes. What's expensive is consistency, not compute. A 1-hour TTL means a dataset published at T=0 is invisible on the facets for up to an hour — and researchers who saw the dataset go live will notice.

- **Primary strategy (FUTURE): invalidate on dataset-publish events.** If/when a cloud-to-proxy publish notification mechanism (webhook, poll delta) ships, the proxy's publish handler calls `FacetService.invalidate()`, which is already implemented as a dormant hook. Today there is **no such notification path** — the cloud does not push webhooks to the proxy, and no polling-delta mechanism exists.
- **Fallback strategy (CURRENT): short TTL + background recompute.** 5-minute TTL (`FACETS_CACHE_TTL_SECONDS`), served by the same `RedisTableCache.get_or_compute` primitive the summary and provenance services use. Freshness lag is bounded to ≤5 minutes. Recompute on read-after-TTL is a single Redis GET for hits and a catalog walk + per-dataset summary fetch for miss. The summary fetch amortizes through the 5-minute summary cache → typical facet miss pays for one catalog walk only.

Per-request cost unchanged. The "extra work" is the invalidation hook, which is dormant until a publish-notification path exists.

### Cache key — `facets:v1`

No per-user scope. Facets are strictly public data aggregated from the published catalog. Two users reading `/api/facets` share the same cached blob. This is a deliberate departure from ADR-010 / ADR-011's `{dataset_id}:{user_scope}` scheme: there's no per-user variation to isolate because the aggregation is over the public catalog only.

**Prefix reserved: `facets:v1`.** Any future schema bump becomes `facets:v2`. The sibling prefixes (`summary:v1`, `provenance:v1`, `pivot:v1`, `table:v4`, `depgraph:v4`) are untouched.

### Relationship to ADR-010 (dataset-summary synthesizer)

The facet aggregator reads `CompactDatasetSummary` (attached by B2's enricher on catalog rows) for the fast species + brainRegions path, and falls back to fetching the full `DatasetSummary` per dataset when strains, sexes, or probeTypes are needed. **Any shape change to `CompactDatasetSummary` or `DatasetSummary` cascades directly into this service.**

Specifically:
- If a new structured-fact list is added to `DatasetSummary` (say, `techniques`), the facet aggregator needs an explicit addition to pick it up. It won't magically appear in `FacetsResponse`.
- If an existing list's ontology-dedupe key changes, the aggregator's `_add_ontology_term` helper may need adjustment.
- The compact summary's `null` semantics (extraction did not run) are preserved — the aggregator skips null lists gracefully, matching the `null` vs `[]` contract in amendment §3.

### MATLAB `contains` string-match default

Alongside the facet endpoint, the query page's string-match default flips from `exact_string` (equals) to `contains_string` (case-insensitive substring). This ports the NDI-matlab tutorial convention (Spike-0 Report C §2.2 + §7.6): the flagship Francesconi et al. 2025 tutorial teaches `stringMatch='contains'` as the default. The operator dropdown still offers `exact_string`, `exact_string_anycase`, `regexp`, and `isa` for researchers who want tighter matching.

### Output-shape preview on the query page

B6a has landed the canonical NDI-matlab column defaults (`SUBJECT_DEFAULT_COLUMNS`, `PROBE_DEFAULT_COLUMNS`, `EPOCH_DEFAULT_COLUMNS` in `frontend/src/data/table-column-definitions.ts`). The query page adds an `OutputShapePreview` sidebar card that renders the header row of each canonical grain, linked to the Francesconi tutorial as the citation source. Purely static — no live row data, no backend call — a "what you'll get" affordance that matches the existing column shape researchers know from MATLAB.

## Rationale

1. **Own-proxy aggregation beats cloud-side.** No cross-team blocker. The cloud's design-doc `POST /documents/query` with projection (tracked but unshipped) would simplify the query page's *result* preview, but doesn't help with cross-dataset DISTINCT. Plan B does not block on it; a `// TODO` in `facet_service.py` points the future switch at the right call site.
2. **Freshness budget beats TTL economy.** Amendment §4.B3 rewrite. 5 minutes is the operational maximum for "fresh enough when a dataset just shipped." 1 hour was unacceptable for researchers who watched a dataset go live.
3. **Public-only cache scope is safe.** Unlike summaries and provenance, facets aggregate only published datasets. There's no per-user variation to protect — a single cache key for everyone is correct.
4. **Reuse the B2 enricher's output instead of re-fetching.** The catalog list already carries compact summaries (species, brainRegions). The facet aggregator prefers them over full-summary re-fetches for those two facets. Strains/sexes/probeTypes still require the full summary — and those fetches amortize through the 5-minute per-dataset summary cache, so repeat facet builds within a TTL window pay zero extra cloud traffic.
5. **Separate service module, not an extension of `DatasetService`.** Different grain (cross-dataset vs per-dataset), different cache bucket, different TTL strategy. Combining would obscure the boundaries.

## Consequences

- One new public route (`GET /api/facets`), one new Pydantic type (`FacetsResponse`), one new Redis cache bucket (`facets_cache`, 5-minute TTL, prefix `facets:v1`).
- One new frontend hook (`useFacets`), one new component (`FacetPanel`), one new component (`OutputShapePreview`). Query page recomposes into a three-column layout (facet sidebar / builder / preview).
- `QueryBuilder` new-condition default flips from `isa` to `contains_string`. Existing URL deep-link behavior (op+field+param1+param2 hydration for the ontology "Find everywhere" cross-link) is untouched; only the "add new filter" default changes.
- Short-TTL-based freshness means a freshly published dataset shows up on the facets within 5 minutes, not up to an hour. Acceptable operational budget per amendment.
- ADR-009 (`ruff` ban on `import httpx` in services) covers the new file — enforced by the existing `tests/unit/test_services_http_boundary.py` AST walk.
- No breaking change to `DatasetSummary` or `CompactDatasetSummary` or `DatasetProvenance` (all shapes untouched).

## Alternatives considered

- **1-hour aggressive TTL.** Rejected (amendment rewrite). Publishing a new dataset and not seeing it reflected on the facet chips until up to an hour later is a visible correctness gap for any researcher watching the dataset land.
- **Invalidation-only (no TTL).** Rejected: no publish-notification path exists today. Would mean the cache could grow stale indefinitely (a crashed replica might never flush). TTL is belt-and-suspenders insurance.
- **Per-user cache scope.** Rejected: facets aggregate public-catalog data only. No per-user variation exists to isolate.
- **Cloud-side facet endpoint.** Rejected: cross-team blocker (same reasoning as ADR-010 rejection). The primitives exist; the aggregation is cheap on our side.
- **Fold into the query router.** Partly accepted. The router module is the same (`backend/routers/query.py`) but the new `facets_router` is a separate `APIRouter` instance with its own prefix (`/api/facets`) and read-only rate limit (`limit_reads`) — the query router uses `limit_queries` which is a different bucket. Keeping them as two `APIRouter`s makes the rate-limit story cleaner.
- **Extend `DatasetSummary` with a cross-dataset roll-up field.** Rejected: the contract for `DatasetSummary` is frozen (amendment §3). Extending it pulls B3 scope into a B1 mutation. Separate endpoints keep the shapes independent.
- **Surface cloud's `POST /documents/query` with projection immediately.** Rejected: not shipped yet. Tracked via a TODO comment at the call site that would benefit.

## References

- [amendment §4.B3](../plans/spike-0-amendments.md#b3--query-page--port-matlab-filter-conventions--cache-for-freshness) — cache-strategy rewrite (freshness > TTL economy).
- [amendment §3](../plans/spike-0-amendments.md#3-datasetsummary-data-shape) — `OntologyTerm` shape, null vs [] semantics, probeTypes as free-text bucket.
- [Spike-0 Report C](../plans/spike-0-reports/spike0-C-ndi-matlab.md) — NDI-matlab tutorial `stringMatch='contains'` convention (§2.2, §7.6).
- [Spike-0 Report A](../plans/spike-0-reports/spike0-A-ndi-cloud-node.md) — cloud API inventory; `POST /ndiquery` scope semantics; `POST /documents/query` with projection tracking.
- ADR-003 (Redis sessions + cache), ADR-009 (services HTTP boundary), ADR-010 (dataset-summary synthesizer), ADR-011 (dataset provenance).
