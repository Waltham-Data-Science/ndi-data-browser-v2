# Spike-0 Report A: ndi-cloud-node vs. Plan B

**Subagent:** A / 4
**Target repo:** `/Users/audribhowmick/Documents/ndi-projects/ndi-cloud-node`
**Date:** 2026-04-17
**Scope:** Answer whether ndi-cloud-node already serves initiatives B1-B5 of the Plan B IA redesign.

---

## TL;DR table

| Initiative | Status | Notes |
|---|---|---|
| B1 — Experiment-summary synthesizer | **Doesn't exist** (primitives available) | No dataset-level "experiment summary" endpoint. `document-class-counts` gives class histograms, and `POST /ndiquery` with `isa` can reach subject/probe/epoch docs. Synthesis must happen in the v2 proxy. |
| B2 — Catalog cards with species/region/technique/N | **Partial — ~30%** | `species`, `brainRegions`, `numberOfSubjects`, `neurons` exist on the dataset schema, but `DatasetListResult` (what `/datasets/published` returns) does **not** include them. `technique` field does not exist at all. |
| B3 — Query page with examples / preview / facets | **Doesn't exist** | No facet/distinct/aggregation endpoint across datasets. No sample-rows endpoint. Building on `/ndiquery` + client-side aggregation is the path. |
| B4 — Extraction affordances (cite + snippet) | **Partial — ~60%** | Single `GET /datasets/:id` returns `doi`, `contributors`, `correspondingAuthors`, `associatedPublications`, `funding`, `license`, `pubMedId` — all the raw material needed to format BibTeX/RIS client-side. No citation endpoint that returns pre-formatted BibTeX. No canonical API-URL template is published. |
| B5 — Lineage / provenance at dataset scope | **Doesn't exist** | `depends_on` lives on individual documents (`data.depends_on[].value`), and is searchable via `POST /ndiquery` with the `depends_on` op. There is **no dataset-level rollup endpoint** that says "dataset X is upstream/downstream of dataset Y." Must be aggregated client-side. |

---

## Routing layer — full inventory

The app is an Express app (`api/src/app.ts`) deployed as a single Lambda. All routes are mounted under `/v1`.

### Routers (all at `api/src/routes/`)

| Router file | Mount | File |
|---|---|---|
| `auth.router.ts` | `/v1/auth` | lines 15-25 |
| `user.router.ts` | `/v1/users` | lines 18-26 |
| `dataset.router.ts` | `/v1/` | lines 18-84 |
| `document.router.ts` | `/v1/datasets` | lines 16-31 |
| `search.router.ts` | `/v1/` | lines 15-25 |
| `compute.router.ts` | `/v1/compute` | lines 14-24 |
| `stripe.router.ts` | `/v1/` | — |

### Every dataset/document endpoint that exists today

```
POST   /v1/auth/login
POST   /v1/auth/logout
POST   /v1/auth/confirmation/resend
POST   /v1/auth/verify
POST   /v1/auth/password
POST   /v1/auth/password/forgot
POST   /v1/auth/password/confirm

GET    /v1/datasets/published                     (public)
GET    /v1/datasets/unpublished                   (auth)
GET    /v1/datasets/deleted                       (auth)
GET    /v1/datasets/:datasetId
GET    /v1/datasets/:datasetId/document-count
POST   /v1/datasets/:datasetId                    (update)
POST   /v1/organizations/:organizationId/datasets (create)
GET    /v1/organizations/:organizationId/datasets
GET    /v1/datasets/user/:userId/bookmarks
DELETE /v1/datasets/:datasetId
POST   /v1/datasets/:datasetId/undelete
GET    /v1/datasets/:datasetId/files/:uid/detail
GET    /v1/datasets/:organizationId/:datasetId/files/bulk
GET    /v1/datasets/:organizationId/:datasetId/files/:uid
POST   /v1/datasets/:datasetId/branch
GET    /v1/datasets/:datasetId/branches
POST   /v1/datasets/:datasetId/bookmark
DELETE /v1/datasets/:datasetId/bookmark
POST   /v1/datasets/:datasetId/submit
POST   /v1/datasets/:datasetId/publish
POST   /v1/datasets/:datasetId/unpublish

POST   /v1/datasets/:datasetId/documents/bulk-delete
POST   /v1/datasets/:datasetId/documents/bulk-download
POST   /v1/datasets/:datasetId/documents/bulk-fetch
POST   /v1/datasets/:datasetId/documents/bulk-upload
GET    /v1/datasets/:datasetId/documents/deleted
GET    /v1/datasets/:datasetId/documents/:documentId
POST   /v1/datasets/:datasetId/documents/:documentId
DELETE /v1/datasets/:datasetId/documents/:documentId
GET    /v1/datasets/:datasetId/documents
GET    /v1/datasets/:datasetId/document-class-counts
POST   /v1/datasets/:datasetId/documents

POST   /v1/datasets/search
POST   /v1/documents/search
POST   /v1/ndiquery
```

**Note:** there is **NO** `/v1/auth/refresh` endpoint — confirmed in both `auth.router.ts` and `auth.controller.ts`. No stubs, no commented-out code. This matches the existing CLAUDE.md gotcha.

---

## Shape of `GET /datasets/:id/document-class-counts` (requested explicitly)

**File:** `api/src/controllers/document.controller.ts:83-92`
**DAL:** `api/src/dal/document.repository.ts:77-108`
**Route:** `document.router.ts:27` — `this.router.get('/:datasetId/document-class-counts', [assignUserIfPresent, userHasAccessToDataset], this.documentController.getDocumentClassCounts);`

**Response schema (from swagger.json:1600-1617 and actual controller):**

```json
{
  "datasetId": "abc123",
  "totalDocuments": 10523,
  "classCounts": {
    "subject": 5,
    "probe": 12,
    "epoch": 240,
    "spikesorting": 1200,
    "ontologyTableRow": 48,
    "unknown": 3
  }
}
```

**Key semantics:**

- Grouped by **`data.document_class.class_name`** (leaf class only, **not rolled up through inheritance**). Swagger explicitly says "Returns counts only; does not return document IDs and does not roll up class inheritance. For class-aware drill-downs or listings, use /ndiquery with the isa operator."
- Documents with missing/empty/null `class_name` bucket under `"unknown"`.
- Excludes deleted documents (`isDeleted: false` or absent).
- Uses hint `{ dataset: 1, 'data.document_class.class_name': 1 }` — indexed.
- Auth: `assignUserIfPresent` + `userHasAccessToDataset`. Public datasets work anonymously.

**Implication for B1:** this is the right primitive for counting subjects/probes/epochs. To get "5 subjects, 12 probes, 240 epochs" for a dataset, you hit this one endpoint and read out the keys you care about.

---

## What `GET /datasets/published` actually returns (requested explicitly)

**File:** `api/src/controllers/dataset.controller.ts:118-133`
**Projection:** `api/src/models/dataset.model.ts:162-185` — `fetchDatasets` uses projection `{ files: 0, documents: 0 }`, so the Mongoose doc includes all OTHER fields.
**Serializer:** `api/src/models/results/dataset.list.result.ts`

**`DatasetListResult` fields (from `dataset.list.result.ts:1-49`):**

```ts
id, name, organizationId, createdAt, updatedAt, uploadedAt,
branchName, isSubscribed, isPublished,
contributors, doi, funding, abstract, license,
correspondingAuthors, totalSize, branchOf, documentCount,
isDeleted, deleteCommandTime, deleteOccurrenceTime
```

**What is explicitly NOT in the result (but IS in the underlying schema at `dataset.model.ts:28-33`):**

- `species: String`
- `neurons: Number`
- `numberOfSubjects: Number`
- `brainRegions: String`
- `pubMedId: String`
- `affiliation: String`
- `associatedPublications: [...]`

These exist in the Mongoose schema and the `IDataset` interface (`interfaces/dataset.interface.ts:31-35`) and are accepted on `DatasetRequest` in the swagger — but `DatasetListResult`'s constructor (`dataset.list.result.ts:27-48`) does **not copy them over**.

**Implication for B2:** The data is captured at upload time and stored, but the catalog endpoint hides it. Two options:
1. Ask Steve to expand `DatasetListResult` to include those fields (trivial 5-line change, additive, no breaking change).
2. For each dataset in the catalog, fire a `GET /datasets/:id` which returns the full dataset including `species`/`brainRegions`. This is N+1 but workable for now given the catalog is typically < 50 datasets.

There is NO `technique` field anywhere in the dataset schema. If Plan B needs technique-per-dataset, it must be derived from the `epoch` or `probe` documents (via class_counts + ndiquery) or added to the dataset schema.

---

## Initiative-by-initiative verdict (detail)

### B1 — Experiment-summary synthesizer — **Doesn't exist (primitives available)**

There is no `GET /datasets/:id/experiment-summary` endpoint. No prose-generation endpoint. The synthesizer must live in the v2 backend.

**Available primitives the v2 backend can stitch together:**

| Need | Primitive |
|---|---|
| Species, brain region, subject count | `GET /datasets/:id` → reads `species`, `brainRegions`, `numberOfSubjects` fields on the dataset. These are only populated if the uploader filled them in; otherwise empty. |
| Subject count (live / per-document) | `GET /datasets/:id/document-class-counts` → read `classCounts.subject` |
| Probe count, epoch count | same endpoint, `classCounts.probe` / `classCounts.epoch` |
| Date range | **No direct primitive.** Would need `POST /ndiquery` with `{scope: datasetId, searchstructure: [{operation: 'isa', param1: 'epoch'}]}` and look at each returned doc's `data.base.datestamp` or similar. Slow for datasets with many epochs. Alternative: query `subject` docs' creation/collection date if present in schema. |
| Technique | **No primitive.** Not on the dataset schema, not tagged per-class. Would need to infer from the `ndi_element_class` field on `element`-isa documents, queried via `/ndiquery`. |

**Recommendation:** build the synthesizer in `backend/services/` as composition of `GET /datasets/:id` + `GET /datasets/:id/document-class-counts` + optional `ndiquery` calls. Cache the result in Redis with short TTL.

---

### B2 — Catalog cards with species/region/technique/N — **Partial ~30%**

- `species` / `brainRegions` / `numberOfSubjects` / `neurons` live on the dataset model (`dataset.model.ts:28-33`) but are **not surfaced** by `GET /datasets/published` (`DatasetListResult` omits them).
- `technique` does not exist as a dataset-level field at all.
- `documentCount` **IS** in the list result (`dataset.list.result.ts:22`) — so "N" is trivial.

**Three viable paths for v2:**

1. **N+1 hydration (now):** catalog calls `/datasets/published`, then for each row `GET /datasets/:id`. Works, but 20+ calls per page.
2. **Call Steve:** ask for `DatasetListResult` to include `species`, `brainRegions`, `numberOfSubjects`, `neurons`, `associatedPublications`. 5-line additive change.
3. **Synthesize technique:** since technique isn't stored, derive it from `document-class-counts` + `ndiquery` on an element's `ndi_element_class` field. Cacheable per dataset.

---

### B3 — Query page with examples / preview / research-vocabulary filters — **Doesn't exist**

There is **no platform-wide facet endpoint**. Nothing returns "which species exist across all datasets", "which brain regions", "which techniques." Nothing returns a `distinct` of any field.

**Closest primitives:**

- `POST /datasets/search` + `POST /documents/search` — accept a `SearchQuery` (see `api/src/models/requests/search.query.ts`) and run a filter; but they don't aggregate.
- `POST /ndiquery` — runs a class-filtered / field-filtered query across `public | private | all | <csv of dataset IDs>` scope and returns a page of matching documents. This is the query primitive but returns matches, not facets.
- No "sample rows" endpoint — no way to ask "show me 5 representative documents". Would need `POST /ndiquery` with limit=5.

**Example queries / curated vocabulary:** nothing in the cloud API. This is entirely a v2-frontend responsibility.

**Recommendation:** v2 frontend provides the curated example queries and research vocabulary (as static JSON). For facet counts ("23 datasets have mouse species"), v2 backend must page through `/datasets/published`, read species/brainRegions from each, and aggregate client-side. Cache aggressively. Not great, but there's no server-side facet pipeline today.

---

### B4 — Extraction affordances (cite + snippet) — **Partial ~60%**

`GET /datasets/:id` (`dataset.controller.ts:183-210`) returns the full dataset including:

- `doi` — dataset.interface.ts:27
- `contributors: [{firstName, lastName, orcid, contact}]` — 26
- `correspondingAuthors: [{firstName, lastName, orcid}]` — 38
- `associatedPublications: [{DOI, title, PMID, PMCID}]` — 39
- `funding: [{source}]` — 28
- `license` — 28
- `abstract` — 29
- `pubMedId` — 35
- `affiliation` — 36

This is enough to format BibTeX, RIS, and plain-text citations entirely client-side. No server-side formatting endpoint.

**What's missing:**

- No `GET /datasets/:id/citation.bib` / `.ris` / `.txt` endpoint. Could be added, but likely unnecessary — client-side formatting is fine and cacheable.
- No canonical API-URL template is published for use in "copy this snippet" UX. The URL is `https://api.ndi-cloud.com/v1/datasets/:datasetId` (or `dev-api.ndi-cloud.com`) per `serverless.yml:654-656`, but this is implicit knowledge, not advertised through any metadata endpoint. v2 can hardcode it.

---

### B5 — Lineage / provenance at dataset scope — **Doesn't exist**

Lineage is entirely **document-level** in ndi-cloud-node:

- Each document has `data.depends_on: [{name, value}, ...]`. `name` is a role (e.g. `"underlying_subject"`), `value` is a target document ID. See `ndi_query_translator.ts:110-131`.
- Each document has top-level `classLineage: string[]` — this is **class-ISA lineage** (e.g. a `spikesorting` doc has `["spikesorting", "element", ...]`) for fast `isa` queries. Populated from `data.document_class.superclasses` on write (see `api/src/dal/class_lineage.ts:17-51`). This is **NOT dataset-derivation lineage** despite the similar name.
- The only exposed query is `POST /ndiquery` with `operation: "depends_on", param1: <name or *>, param2: <value>` which returns documents whose `depends_on` array contains a matching entry (`ndi_query_translator.ts:110-131`).

**There is no rollup anywhere** that says "dataset X has documents whose `depends_on` targets point at documents in dataset Y, therefore X is downstream of Y." No endpoint, no aggregation pipeline, no cache.

**Closest existing primitive:** the `branchOf: ObjectId` field on `IDataset` (`interfaces/dataset.interface.ts:42`) captures parent-branch relationships between datasets. This is a narrow notion of "lineage" limited to dataset branches, and is surfaced via `GET /datasets/:id/branches` (`dataset.controller.ts:290-309`). It does not capture document-level derived-from relationships.

**Recommendation:** v2 backend must aggregate itself. For dataset X:
1. Get all docs in X via `GET /datasets/:id/documents`.
2. For each doc, look at its `data.depends_on` values.
3. Resolve each target-doc-id's `dataset` field.
4. Report unique upstream-dataset IDs.

This is expensive (requires iterating every document). Caching is essential. For "downstream" you'd need an index on `data.depends_on.value` pointing in — which exists (see ndi_query_translator is searchable), but you'd still need to batch through every dataset to ask "does anything here depend on something in dataset X?" This is a perfect candidate for an additive server-side endpoint.

---

## Additional findings (beyond the B1-B5 ask)

### Relevant endpoint noticed: `GET /datasets/:datasetId/document-count`

`dataset.controller.ts:213-231` — returns `{ datasetId, count }`. Computes live count via `documentRepository.getCount`. Supports `?includeDeleted=true`. Different from the persisted `documentCount` field on the dataset (which can drift).

### Relevant endpoint noticed: `POST /datasets/:id/documents/bulk-fetch`

`document.controller.ts:329-384` — already in use by v2. Max 500 docs per call, IDs must be 24-char hex, filters cross-dataset IDs server-side. Hydrates file-download URLs. This is the right primitive for the summary-table batched-detail flow CLAUDE.md already describes.

### Relevant endpoint noticed: `GET /datasets/:datasetId/documents?page=X&pageSize=Y`

`document.controller.ts:47-81` — paginated, metadata-only (no `data` field). pageSize max 1000. Would be useful for B5 when iterating documents to compute lineage.

### Deprecations in progress

**None found.** No `@deprecated` JSDoc, no "DEPRECATED" comments, no `api/v1` vs `v2` divergence hints. The codebase is on a single active major version.

There is ONE "Temporarily keep for transition" comment at `serverless.yml:73` about `FILE_BUCKET` — this is S3 infrastructure, not relevant to Plan B.

### IMPORTANT: Unmerged design doc

`docs/superpowers/specs/2026-04-13-api-performance-additions-design.md` (dated 4 days before now, status **Draft**, branch **TBD**). This doc proposes adding:

1. **Express `compression` middleware** — not yet enabled. Only MongoDB driver compression exists (`initializeMongoConnection.ts:104`).
2. **`GET /v1/datasets/:datasetId/document-summary`** — proposed endpoint with the SAME shape as the current `document-class-counts`. Appears to be a redundant alternative; if merged, plan for both endpoints to coexist or one to be dropped.
3. **`POST /v1/datasets/:datasetId/documents/batch`** — proposed endpoint with the same shape as the existing `bulk-fetch`. Appears redundant.
4. **`POST /v1/datasets/:datasetId/documents/query`** — **new, not redundant.** Class-filtered document query with field projection, scoped to one dataset. This is exactly the primitive that makes B3 "sample rows preview" one API call instead of "download everything, filter client-side."

Status: **not yet merged**, awaiting Steve's review. If/when merged, v2's summary-table service and B3 sample-rows UX both get dramatically simpler. Worth tracking, but don't design Plan B around it.

### HIPAA-found pre-existing bugs that affect us

From the HIPAA review referenced in the spec doc (`manuals/reviews/HIPAA_Review_Results_2026-04-13.md`):

- `POST /datasets/search` and `POST /documents/search` are missing `assignUserIfPresent` middleware — they IGNORE the Authorization header and always behave as public-only searches. If v2 uses these endpoints and expects private datasets to be included when authenticated, **they won't be**. Confirmed by reading `search.router.ts:17-19` — only `/ndiquery` has `assignUserIfPresent`; `/datasets/search` and `/documents/search` have nothing.
- `skipPermissionCheck` flag in `document.repository.ts` is a known hazard. `/ndiquery` uses it correctly; new callers must not.

### No `POST /auth/refresh` anywhere

Confirmed across:
- `api/src/routes/auth.router.ts:15-25` — no refresh route registered
- `api/src/controllers/auth.controller.ts` — no refresh handler
- `api/src/swagger.json:60-370` — `paths` block lists only login/logout/verify/confirmation/password endpoints
- `api/src/middleware/auth.middleware.ts` — no refresh logic

The v1 login response does include `result.getIdToken()` (Cognito ID token), and Cognito itself supports refresh tokens, but ndi-cloud-node exposes no refresh endpoint. The Cognito ID token is what's returned, TTL is 1 hour — matching the v2 CLAUDE.md note. **We should proceed with deleting `backend/auth/token_refresh.py` in v2 — there is no backend-side refresh to integrate with, and Steve has not yet shipped one.**

---

## Files cited (absolute paths)

- `/Users/audribhowmick/Documents/ndi-projects/ndi-cloud-node/api/src/app.ts`
- `/Users/audribhowmick/Documents/ndi-projects/ndi-cloud-node/api/src/routes/auth.router.ts`
- `/Users/audribhowmick/Documents/ndi-projects/ndi-cloud-node/api/src/routes/dataset.router.ts`
- `/Users/audribhowmick/Documents/ndi-projects/ndi-cloud-node/api/src/routes/document.router.ts`
- `/Users/audribhowmick/Documents/ndi-projects/ndi-cloud-node/api/src/routes/search.router.ts`
- `/Users/audribhowmick/Documents/ndi-projects/ndi-cloud-node/api/src/controllers/auth.controller.ts`
- `/Users/audribhowmick/Documents/ndi-projects/ndi-cloud-node/api/src/controllers/dataset.controller.ts`
- `/Users/audribhowmick/Documents/ndi-projects/ndi-cloud-node/api/src/controllers/document.controller.ts`
- `/Users/audribhowmick/Documents/ndi-projects/ndi-cloud-node/api/src/controllers/search.controller.ts`
- `/Users/audribhowmick/Documents/ndi-projects/ndi-cloud-node/api/src/services/search.service.ts`
- `/Users/audribhowmick/Documents/ndi-projects/ndi-cloud-node/api/src/models/dataset.model.ts`
- `/Users/audribhowmick/Documents/ndi-projects/ndi-cloud-node/api/src/models/document.model.ts`
- `/Users/audribhowmick/Documents/ndi-projects/ndi-cloud-node/api/src/models/interfaces/dataset.interface.ts`
- `/Users/audribhowmick/Documents/ndi-projects/ndi-cloud-node/api/src/models/interfaces/document.interface.ts`
- `/Users/audribhowmick/Documents/ndi-projects/ndi-cloud-node/api/src/models/results/dataset.list.result.ts`
- `/Users/audribhowmick/Documents/ndi-projects/ndi-cloud-node/api/src/models/results/dataset.result.ts`
- `/Users/audribhowmick/Documents/ndi-projects/ndi-cloud-node/api/src/models/results/document.list.result.ts`
- `/Users/audribhowmick/Documents/ndi-projects/ndi-cloud-node/api/src/dal/document.repository.ts`
- `/Users/audribhowmick/Documents/ndi-projects/ndi-cloud-node/api/src/dal/ndi_query_translator.ts`
- `/Users/audribhowmick/Documents/ndi-projects/ndi-cloud-node/api/src/dal/class_lineage.ts`
- `/Users/audribhowmick/Documents/ndi-projects/ndi-cloud-node/api/src/swagger.json`
- `/Users/audribhowmick/Documents/ndi-projects/ndi-cloud-node/docs/superpowers/specs/2026-04-13-api-performance-additions-design.md`
