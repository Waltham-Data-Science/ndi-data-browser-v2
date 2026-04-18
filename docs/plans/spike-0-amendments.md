# Spike-0 Synthesis + Plan B Amendments

**Status:** Amended 2026-04-17
**Supersedes:** the initial in-chat synthesis delivered earlier in session `c6113a77`.
**Source of truth:** this document. Reference it for Plan B launch, not the chat.

---

## 0. What changed in this amendment

Three targeted updates layered on top of the earlier synthesis, per user direction 2026-04-17:

1. **HIPAA finding verified** directly against `ndi-cloud-node` main (`a4e1050`, 2026-04-17). **Not fixed.** Filed as [ndi-cloud-node#14](https://github.com/Waltham-Data-Science/ndi-cloud-node/issues/14). Blocker for v2 adoption of `POST /datasets/search` and `POST /documents/search`. See §6.
2. **B3 facet caching language rewritten** from "aggressive cache, 1h TTL" to "cache for freshness, invalidate on dataset publish." See §4.B3.
3. **PR-mapping table memorialized** in this doc (previously lived in chat only). See §1.

Everything else below is the earlier synthesis, preserved intact so this doc stands alone.

---

## 1. Steve's 5 cloud-side PRs (recent state of ndi-cloud-node main) and v2's leverage

The ndi-cloud-node `main` branch at commit `a4e1050` (2026-04-17) contains five shipped PRs that materially change the primitives Plan B composes on. None of these are "draft" any longer — they are all merged and deployed.

| Cloud-shipped | Commit(s) | Where it shows up in v2 / Plan B |
|---|---|---|
| **PR #8 — gzip at API Gateway level** | `eaa031a` | `httpx` already sends `Accept-Encoding: gzip` by default; v2 gets the 5–10× JSON response shrink automatically, at zero Lambda CPU cost. No code change needed in v2. Verify once post-PR-1 merge by inspecting `Content-Encoding` in a response. |
| **PR #9 — `classLineage` field + indexed `isa`** | `b59cd79`, `fb6951f`, `474c62f`, `5da89d0`, `9c499c2`, `c2804e6` | Every `POST /ndiquery` the B1 synthesizer fires with `isa='subject'/'probe'/'epoch'` is now an **indexed equality lookup**, not a regex scan. This is what makes the synthesizer viable at catalog scale. **Naming trap:** this `classLineage` is *class-ISA lineage* (a `spikesorting` doc's superclasses), **not** dataset-derivation lineage. Our B5 ("dataset lineage") must use distinct vocabulary — e.g. "dataset provenance" or "derivation graph" — because the identical word is already claimed by the cloud for a different concept. |
| **PR #10 — `GET /datasets/:datasetId/document-class-counts`** | `95f85d3`, `89a383e` | `DatasetSummary.counts.{sessions, subjects, probes, elements, epochs, totalDocuments}` are populated from one indexed aggregation call. Runs in milliseconds on any dataset size. No client-side pagination, no tallying. |
| **PR #11 — `/ndiquery` scope accepts CSV of dataset IDs** | `fb876fc`, `ce15f4e` | The primary fetch path for B1 is `POST /ndiquery` with `scope: "<datasetId>"` + `isa=subject` (then `isa=probe`, `isa=epoch`). The DAL's `DatasetRepository.filterAccessibleIds` silently drops inaccessible IDs, so v2 can't use the multi-ID path to reach forbidden datasets. Unlocks B3 multi-dataset cohort queries via `scope: "idA,idB,idC"` without any cross-team work. |
| **PR #12 — `POST /datasets/:datasetId/documents/bulk-fetch` (500-doc cap, sync)** | `8ad5d56`, `0977f4c` | B1's openminds-doc hydration (strain, species, biological-sex, treatment docs for each subject) uses this. The 500 cap is deliberate — synchronous inside Lambda's 29-second ceiling. Already documented in CLAUDE.md gotchas. For oversize requests the cloud responds 400 pointing the caller at `bulk-download` (async). v2's summary-table service already batches at concurrency 6 (per CLAUDE.md §Gotchas). |

**What the design-doc cycle did NOT ship (useful to track):**

- **`POST /datasets/:id/documents/query` with field projection.** The original `2026-04-13-api-performance-additions-design.md` proposed this; Steve instead broadened `/ndiquery` scope (PR #11) which is more useful for multi-dataset but doesn't give us projection. **Net v2 impact:** B3's sample-row preview still returns full docs; we drop fields client-side. Not a blocker, just a slightly heavier response than the design doc would have produced.

**What's still missing cloud-side that Plan B needs built ourselves:**

- No synthesizer endpoint (for B1).
- No cross-dataset facet/DISTINCT endpoint (for B3's research-vocab chips).
- No dataset-level derivation-lineage endpoint (for B5).
- No `technique` field at dataset level anywhere (see §4.B1 for the three-location spread).
- `DatasetListResult` omits `species`/`brainRegions`/`numberOfSubjects`/`neurons` fields that exist on the Mongoose schema — see [ndi-cloud-node#15](https://github.com/Waltham-Data-Science/ndi-cloud-node/issues/15) for the 5-line ask.

**Net:** not reinventing anything Steve shipped. Leveraging all 5 PRs as the composition primitives.

---

## 2. Decision memo — where the `DatasetSummary` synthesizer lives

**Decision: synthesizer lives in our v2 FastAPI backend** as a new service `backend/services/dataset_summary_service.py`, composing existing cloud primitives (#3, #4, #5 from §1). Field-extraction logic ports **verbatim** from NDI-python's `src/ndi/fun/doc_table.py:684-826`. Cached in Redis with PR-3's user-scoped key scheme.

**Why not cloud-side:** Steve already punted on `/auth/refresh`; the design-doc's `/documents/query` with projection shipped-as-something-else. Cross-team dependency is precisely what we learned to avoid from token_refresh.

**Why not frontend-side:** the summary appears on catalog cards (B2), which means N datasets × synthesizer work per catalog render. Server compute + Redis cache is strictly better.

**Why not NDI-python as a subprocess:** its `CloudClient` uses plain `requests` (worse than v2's httpx), its `downloadDataset` violates ADR 004. We extract the field-access logic only; build the orchestrator in v2's idiom.

**Also do (additive, non-blocking):** the 5-line ask at [ndi-cloud-node#15](https://github.com/Waltham-Data-Science/ndi-cloud-node/issues/15). If Steve ships it, our synthesizer short-circuits ("if cloud already returned `species`, don't recompute"). If not, our backend computes from documents. Plan B does not block on this issue.

---

## 3. `DatasetSummary` data shape

Names changed from `ExperimentSummary` → `DatasetSummary` per vocabulary finding. Both a Pydantic model (backend) and a TypeScript interface (frontend) live at `backend/services/dataset_summary_service.py::DatasetSummary` + `frontend/src/types/dataset-summary.ts`.

### TypeScript (frontend contract)

```ts
/** Single source of truth for a dataset's synthesized facts.
 *  Backed by `GET /api/datasets/:id/summary`. Also embedded (compact form) in
 *  each element of `GET /api/datasets/published` once that endpoint is
 *  augmented (either by our synthesizer side-car OR by Steve's DatasetListResult
 *  expansion, whichever lands first).
 *
 *  Name intentionally uses the NDI-vocabulary "dataset" rather than the
 *  invented term "experiment" (see NDI-matlab ndimodel/2_vocabulary.md).
 */
export interface DatasetSummary {
  datasetId: string;

  /** Counts — sourced from `GET /document-class-counts`, no synthesis needed. */
  counts: {
    sessions: number;
    subjects: number;
    probes: number;
    elements: number;         // supertype of probes + inferred elements
    epochs: number;
    totalDocuments: number;
  };

  /** Multi-valued facts. Every value is a (label, ontology_id) pair so
   *  downstream code can always link to the ontology term. Empty array =
   *  fact genuinely absent; `null` = extraction did not run (e.g. 0 subjects). */
  species: OntologyTerm[] | null;     // e.g. [{label:"Rattus norvegicus", ontologyId:"NCBITaxon:10116"}]
  strains: OntologyTerm[] | null;     // e.g. [{label:"SD", ontologyId:"RRID:RGD_70508"}]
  sexes: OntologyTerm[] | null;       // e.g. [{label:"male", ontologyId:"PATO:0000384"}]
  brainRegions: OntologyTerm[] | null;// e.g. [{label:"bed nucleus of stria terminalis", ontologyId:"UBERON:0001880"}]
  probeTypes: string[] | null;        // free-text bucket — no canonical ontology

  /** Scale signals for catalog cards. */
  dateRange: { earliest: string | null; latest: string | null };  // ISO-8601
  totalSizeBytes: number | null;

  /** Citation surface — available verbatim from `GET /datasets/:id`. Included
   *  here so catalog cards and the detail view share one shape. */
  citation: {
    title: string;
    license: string | null;          // e.g. "CC-BY-4.0"
    datasetDoi: string | null;       // prefix 10.63884/ — minted by ndi.cloud.admin.createNewDOI
    paperDois: string[];             // from associatedPublications
    contributors: { firstName: string; lastName: string; orcid: string | null }[];
    year: number | null;
  };

  /** Extraction provenance. Rendered as "Last computed X ago" + debug tooltip. */
  computedAt: string;                 // ISO-8601
  schemaVersion: "summary:v1";
  extractionWarnings: string[];       // e.g. "species not found via animalsubject path; fell back to openminds_data"
}

export interface OntologyTerm {
  label: string;
  ontologyId: string | null;         // e.g. "NCBITaxon:10116"; null if ontology-free
}
```

### Pydantic (backend contract)

Mirror of the above; same fields, `pydantic.BaseModel`, `StrictStr`, `conint(ge=0)`. Lives at `backend/services/dataset_summary_service.py::DatasetSummary`.

### Deliberately NOT in the shape

- **No prose sentence.** Derived frontend representation only. Researchers don't recognize prose as the canonical summary (NDI-matlab evidence: zero prose output anywhere in the ecosystem).
- **No `technique`.** Diffused across four schema locations (`element.element_type`, `probe_geometry.probe_type`, `daqsystem.ndi_daqsystem_class`, `stimulus_presentation.stimulus_type`). `probeTypes` as a free-text bucket is the honest v1 representation.
- **No row-level tables.** Summary is aggregates; row-level served by the existing `/api/datasets/:id/tables/:className` endpoints.

---

## 4. Plan B amendments per initiative

### B1 — Dataset-detail landing card — **reshape, don't cancel**

- **DROP** the one-sentence prose synthesis as the landing-card primary. Primary is the structured `DatasetSummary` shape above, rendered as a labeled-facts block. Prose if wanted is a pure frontend render on top of the structured fields.
- **RENAME** "experiment summary" → "dataset summary" everywhere. NDI vocabulary has no "experiment" term.
- **PRESERVE** `SubjectLocalIdentifier` full strings (e.g. `sd_rat_OTRCre_220819_175@dabrowska-lab.rosalindfranklin.edu`) — structured and researcher-parseable. Never truncate.
- **ACKNOWLEDGE** the `dataset → sessions → subjects` tree. Don't flatten. Show session count explicitly (`counts.sessions`).
- **PORT from NDI-matlab** the 13-column subject / 9-column probe / 12-column epoch canonical grains for "drill-down from summary" views — pass through the existing `/api/datasets/:id/tables/:className` endpoints with per-grain column defaults.

### B2 — Catalog cards — **two-path plan**

- **Fast path:** [ndi-cloud-node#15](https://github.com/Waltham-Data-Science/ndi-cloud-node/issues/15) — Steve's 5-line `DatasetListResult` expansion to include `species`, `brainRegions`, `numberOfSubjects`, `neurons`, `associatedPublications`. Zero breaking risk.
- **Independent path:** our synthesizer populates a compact `DatasetSummary` per dataset; `/api/datasets/published` embeds it.
- **Resolution:** ship whichever lands first. If Steve's ships, our synthesizer short-circuits to cloud-provided fields.
- **Acknowledge:** `technique` is not available without our independent path (or a new cloud field). If Plan B needs `technique` as a catalog filter, we build it ourselves.

### B3 — Query page — **port MATLAB filter conventions + cache for freshness**

- **Default string match = `contains`, not `identical`.** NDI-matlab tutorial convention.
- **Output-shape preview** = **literal tables from the NDI-matlab Francesconi tutorial** (13-col subject, 9-col probe, 12-col epoch). They're already publishable, researcher-recognizable, and attributable with a link to the tutorial.
- **Facet endpoint** (distinct species / brain regions / techniques across datasets) — cloud doesn't have one. Our backend aggregates from page-through `/datasets/published` + computed `DatasetSummary` rows.
- **Cache strategy — REWRITTEN (freshness, not TTL):** the earlier synthesis said "cache aggressively with 1h TTL." With cloud PR #9 (indexed `isa`) + PR #11 (scope=datasetId), per-row query cost is no longer the bottleneck — **consistency is.** A 1-hour TTL means a dataset published at T=0 is invisible on the research-vocab filters until T+60min, which is a user-visible correctness gap for any researcher who saw it go live and then filters for it.
  - **New strategy:** cache writes on compute; invalidate on dataset publish / unpublish / update (three cloud actions that reach the proxy as webhooks or polled deltas). Facet cache key: `facets:v1`. Invalidation is by-key purge on any `POST /datasets/:id/publish|unpublish|:id update` that touches fields we aggregate on. If no notification mechanism is feasible (likely short-term), fall back to a **short TTL (≤5 minutes)** + background recompute on read-after-TTL, so freshness lag is bounded to minutes not hours.
  - Cost of the new strategy: per-request facet retrieval is still a single Redis read; the extra work is the invalidation hook, not the hot path. Net response time unchanged.
- **Track** the cloud's draft `POST /documents/query` design (not shipped). If it lands, B3 sample-rows preview simplifies from "download-then-filter" to "one call with projection." Don't block on it.

### B4 — Extraction affordances — **ship the literal canonical snippets**

- **Python tab** — literal, not invented:
  ```python
  import os
  from ndi.cloud import downloadDataset
  from ndi.cloud.auth import login
  from ndi.cloud.client import CloudClient
  from ndi.fun.doc_table import subject, probe, epoch

  config = login(os.environ["NDI_CLOUD_USERNAME"], os.environ["NDI_CLOUD_PASSWORD"])
  client = CloudClient(config)
  dataset = downloadDataset("<DATASET_ID>", "~/ndi-datasets", verbose=True, client=client)
  subject_df = subject(dataset)
  ```
- **MATLAB tab** — literal, from every NDI-matlab tutorial:
  ```matlab
  dataPath = [userpath filesep 'Datasets'];
  datasetPath = fullfile(dataPath, '<DATASET_ID>');
  if isfolder(datasetPath)
      dataset = ndi.dataset.dir(datasetPath);
  else
      dataset = ndi.cloud.downloadDataset('<DATASET_ID>', dataPath);
  end
  subjectSummary = ndi.fun.docTable.subject(dataset);
  ```
- **Acknowledge dissonance:** both snippets show "download once, work locally forever" while v2 is cloud-first. Name it in the modal ("This snippet downloads the dataset. v2's browser lets you explore without downloading — this is for local analysis.").
- **Export formats: CSV + XLS first-class.** NDI-matlab's default is `.xls` via `writetable`. Current v2 ships CSV + JSON; add XLS (frontend lib, no backend surface).
- **Two DOIs per dataset — paper DOI + dataset DOI (`10.63884/` prefix).** Cite modal distinguishes visually. Dataset DOI is the canonical cite.
- **BibTeX + RIS formatting:** client-side from `DatasetSummary.citation`. No backend endpoint.

### B5 — Lineage — **aggregate in our backend + surface `branchOf`**

- Cloud has only document-level `depends_on`. Dataset-level lineage must aggregate in our backend.
- **Also surface `branchOf`** — `IDataset.branchOf` captures parent-branch relationships. `GET /datasets/:id/branches` exists. Narrow-but-real lineage signal.
- **Vocabulary:** use "dataset provenance" or "derivation graph" in our UI/code. Do NOT use "lineage" unqualified — cloud's `classLineage` is class-ISA lineage, a different concept. Naming clash will confuse.
- Expect heavy computation; cache aggressively with invalidation on dataset publish.

### B6a — Column defaults — **port the canonical set**

- **Default columns = NDI-matlab's 13-column subject shape:** `SubjectDocumentIdentifier`, `SubjectLocalIdentifier`, `StrainName`, `StrainOntology`, `BackgroundStrainName`, `BackgroundStrainOntology`, `GeneticStrainTypeName`, `SpeciesName`, `SpeciesOntology`, `BiologicalSexName`, `BiologicalSexOntology`, plus dynamic treatment-location columns.
- **Hide `SessionIdentifier` by default** — tutorial hides it.
- **Multi-valued cells = CSV join** (matches MATLAB `join({...}, ', ')`). Power users can split into chips via column config later.
- **Do NOT default age/weight.** Not in canonical MATLAB view; stored as generic `subjectmeasurement` KV pairs per DID-Schema. Adding them invents a convention that doesn't exist.

### B6e — Grain-selectable pivot (per Decision 2)

- Subject grain ships first, behind `FEATURE_PIVOT_V1=true`.
- Grain selector auto-populated from per-dataset `document-class-counts`.
- Handle obvious grains in v1 (subject / session / element where present). Don't pre-solve exotic edges.

---

## 5. Cross-plan dependencies (updated)

1. **Plan A PR-3 (user-scoped cache keys) blocks Plan B B1/B2/B5.** The `DatasetSummary` service and its Redis cache inherit PR-3's key scheme.
2. **Plan A PR-6 (download_file host allowlist) affects B4's snippets.** The NDI-python `CloudClient` embedded in the B4 Python snippet has the same unvalidated-URL pattern (`cloud/api/files.py:139 getFile`). Cross-repo, out of scope here — note in the B4 modal's help text.
3. **[ndi-cloud-node#15](https://github.com/Waltham-Data-Science/ndi-cloud-node/issues/15) (DatasetListResult expansion)** unblocks B2's fast path. Not a blocker — our synthesizer handles either path.
4. **[ndi-cloud-node#14](https://github.com/Waltham-Data-Science/ndi-cloud-node/issues/14) (HIPAA search-auth gap)** **BLOCKS any v2 adoption of `POST /datasets/search` and `POST /documents/search`.** We were not planning to use these endpoints in Plan B (B3 uses `/ndiquery`), but confirm before any future feature reaches for them.
5. **Plan A PR-9 (ruff ban on `import httpx` under services)** must land AFTER any Plan B backend that adds a `backend/services/` module. Plan B's `dataset_summary_service.py` does NOT make external HTTP (composes existing endpoints via the cloud client). Clean under the rule.
6. **PR-3's SCHEMA_VERSION v3→v4 bump** produces a ~30s Redis cache-cold window per unique table key on deploy. Plan B B1 lands AFTER PR-3 settles; this avoids debuting B1 against a dirty cache.

---

## 6. Open cloud-side items

### [ndi-cloud-node#14](https://github.com/Waltham-Data-Science/ndi-cloud-node/issues/14) — HIPAA: search endpoints missing `assignUserIfPresent`

- **Verified directly against ndi-cloud-node `main` at `a4e1050` (2026-04-17).**
- `POST /datasets/search` and `POST /documents/search` are registered in `api/src/routes/search.router.ts:17-19` with **no middleware at all.** Compare with `/ndiquery` on line 20-22 which correctly uses `assignUserIfPresent`.
- **Steve's recent 5 PRs did not fix this.** The HIPAA review doc the search thread referenced flagged it as a pre-existing "critical" gap; the associated fix did not land.
- **Open finding. Blocker for v2 adoption of these two endpoints** — any v2 feature that sends `Authorization` to them will silently get public-only results.
- **v2 does not currently use these endpoints.** Plan B does not either (B3 uses `/ndiquery`). Defer any future adoption until fixed.
- **Proceed with Plan A + amendment doc without waiting for cloud-side resolution.**

### [ndi-cloud-node#15](https://github.com/Waltham-Data-Science/ndi-cloud-node/issues/15) — `DatasetListResult` serializer expansion

- Additive 5-line change. Exposes fields already in Mongoose schema.
- Unblocks B2's fast path. Not a blocker — our synthesizer handles both paths.

### `/auth/refresh` — CONFIRMED NOT PLANNED

- Verified across `auth.router.ts`, `auth.controller.ts`, `swagger.json`, `auth.middleware.ts` in ndi-cloud-node main. No endpoint, no stub.
- Steve confirmed in Slack thread (2026-04) — not on roadmap.
- Drives Plan A PR-7 (delete v2's speculative `token_refresh.py`).
- Hourly-re-login UX problem remains unsolved. ADR-008 flags as separate product concern.

### Cloud design doc's `POST /documents/query` with projection — NOT SHIPPED

- The original design doc proposed this. Steve instead broadened `/ndiquery` scope (PR #11), which doesn't include projection.
- Track but don't depend. B3 handles it either way.

---

## 7. Status checklist for Plan B launch

- [ ] User signs off on this amendment doc.
- [ ] Plan A PR-3 (user-scoped cache keys) merged.
- [ ] Then dispatch B1 subagent — implements `dataset_summary_service.py` composing cloud PRs #9/#10/#11/#12.
- [ ] B2 follows B1 (shares the synthesizer); tracks cloud issue #15 as a short-circuit opportunity.
- [ ] B3 follows B2 (shares the facet-aggregation backend).
- [ ] B4, B5, B6a/B6e can dispatch in parallel once B1 is merged.

---

## 8. Non-goals (carried from original synthesis, explicit)

- **Tab rename to Who/How/When/What/Context** — dropped per Decision 2. Not implemented, not planned. Scientists use the schema vocabulary.
- **Circuit breaker HALF_OPEN redesign** — downgraded to a code comment only (Plan A PR-12 per Decision 2).
- **Binary OOM refactor** — scope-capped to one-line guards (Plan A PR-11 per Decision 2).
- **Publish/curate JTBDs** — explicitly out of scope for Plan B per original persona direction.
- **Solving hourly-re-login UX** — flagged in ADR-008 as open product concern. Separate work item.
- **Circuit breaker per-endpoint** — deferred.
- **Invalidation-on-logout for Redis cache** — TTL handles it.

---

## 9. Provenance

- Spike-0 research: 4 parallel subagents, 2026-04-17 20:59–21:17 UTC.
- Subagent reports on disk at `/tmp/ndb-reviews/spike0-{A,B,C,D}-*.md`.
- Synthesis initially in-chat, memorialized here per user direction 2026-04-17.
- User-directed amendments: HIPAA verify + B3 cache language rewrite + PR-mapping table memorialization.
- Sign-off state: **awaiting user sign-off on this amended doc** before B1 dispatch.
