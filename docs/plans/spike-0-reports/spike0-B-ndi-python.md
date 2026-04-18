# Spike-0 / Plan B — NDI-python review

**Date:** 2026-04-17
**Reviewer:** parallel subagent (NDI-python target)
**Local path:** `/Users/audribhowmick/Documents/ndi-projects/NDI-python`
**Fork identity:** `Waltham-Data-Science/NDI-python` (confirmed via `.git/config`; HEAD at `bd3334b`, 2026-04-16). This is the active Python port maintained by the data browser author. The VH-Lab org mirror is older and not the one we have locally.

---

## 0. Canonical entry points

| Area | Entry point | Path |
|---|---|---|
| Cloud HTTP client | `CloudClient` | `src/ndi/cloud/client.py:133` |
| Cloud config | `CloudConfig.from_env()` | `src/ndi/cloud/config.py:52` |
| Auth | `login()`, `authenticate()`, `getActiveToken()` | `src/ndi/cloud/auth.py:119,225,96` |
| Datasets REST | `getPublished`, `getUnpublished`, `getDataset`, `listAllDatasets` | `src/ndi/cloud/api/datasets.py:161,211,40,139` |
| Documents REST | `ndiquery`, `ndiqueryAll`, `listDatasetDocumentsAll`, `getBulkDownloadURL` | `src/ndi/cloud/api/documents.py:258,291,136,213` |
| Files REST (presigned) | `getFile`, `getFileDetails`, `listFiles` | `src/ndi/cloud/api/files.py:139,201,181` |
| Dataset orchestration | `downloadDataset`, `load_dataset_from_json_dir`, `uploadDataset`, `syncDataset` | `src/ndi/cloud/orchestration.py:24,189,278,365` |
| Doc-to-table | `subject`, `probe`, `epoch`, `element`, `openminds` | `src/ndi/fun/doc_table.py:593,233,311,166,450` |
| Summary helper (legacy) | `datasetSummary`, `sessionSummary` | `src/ndi/util/dataset_summary.py:17`, `src/ndi/util/session_summary.py:18` |
| OpenMINDS conversion | `openminds_obj_to_ndi_document`, `find_controlled_instance`, `find_technique_names` | `src/ndi/openminds_convert.py:122,217,257` |
| Subject-doc creation | `makeSpeciesStrainSex`, `probeLocations4probes` | `src/ndi/fun/doc.py:79,245` |
| Doc-type count | `getDocTypes(session)` | `src/ndi/fun/doc.py:471` |

Openminds documents use the schema at `src/ndi/ndi_common/database_documents/metadata/openminds.json` — the canonical shape is `{openminds: {openminds_type, openminds_id, matlab_type, fields: {…}}}`. Subject-scoped variant at `metadata/openminds_subject.json` adds a `depends_on[{name:"subject_id", value:<subject_id>}]`.

---

## 1. Schema-helper modules — how facts are extracted

### 1.1 Species / Strain / Sex (from `openminds_subject` docs)

`src/ndi/fun/doc_table.py:593-857` (`subject()`) is the exact pattern we want. Flow:

1. Query `isa("subject")` → build `subject_id → {SubjectDocumentIdentifier, SessionDocumentIdentifier, SubjectLocalIdentifier}` map (lines 623-637).
2. Query `isa("openminds_subject")` — group each doc under the subject it depends on via `depends_on[name=="subject_id"].value` (lines 643-660). Classify each by `openminds.openminds_type` stripped to short type (`"Strain"`, `"Species"`, `"BiologicalSex"`, `"GeneticStrainType"`).
3. Query `isa("treatment") | isa("treatment_drug") | isa("virus_injection") | isa("measurement")` for per-subject interventions (lines 664-681).
4. For each subject, read from `openminds.fields`:
   - `Species`: `fields.name` → `SpeciesName`, `fields.preferredOntologyIdentifier` → `SpeciesOntology` (lines 733-740).
   - `Strain` (main vs background disambiguation): `fields.name`, `fields.ontologyIdentifier`, `fields.backgroundStrain[]` (lines 690-714).
   - `BiologicalSex`: `fields.name`, `fields.preferredOntologyIdentifier` (lines 742-750).
   - `GeneticStrainType`: `fields.name` (lines 717-730).

So the **JSON-path pattern** for species from a subject is:

```
openminds_subject doc:
  depends_on[name=="subject_id"].value  → subject id
  openminds.openminds_type               → ".../Species"
  openminds.fields.name                  → "Rattus norvegicus"
  openminds.fields.preferredOntologyIdentifier → "NCBITaxon:10116"
```

### 1.2 Brain region / probe location

`src/ndi/fun/doc_table.py:233-308` (`probe()`). Queries `isa("probe_location")`, indexes by `depends_on[name=="probe_id"].value`, reads `probe_location.name` and `probe_location.ontology_name`. Each `element` doc brings `element.name`, `.type`, `.reference` + `depends_on[name=="subject_id"]`. Cell-type comes from `openminds_element` via `openminds.fields.name` / `.preferredOntologyIdentifier`.

### 1.3 Technique / approach

`src/ndi/fun/doc_table.py:311-447` (`epoch()`). Per-epoch approach comes from `isa("openminds_stimulus")` docs, indexed by `epochid.epochid`, reading `openminds.fields.name` and `openminds.fields.preferredOntologyIdentifier`. Stimulus-bath mixtures come from `isa("stimulus_bath")` — `stimulus_bath.location.name` / `.ontologyNode`.

Controlled-vocab resolver: `openminds_convert.find_technique_names()` (line 257) walks `openminds.controlled_terms.Technique | AnalysisTechnique | StimulationApproach` and returns `"<display_name> (<type>)"` strings. We can reuse this to validate facet values.

### 1.4 Document-type counts

`src/ndi/fun/doc.py:471-502` (`getDocTypes()`). Queries `isa("base")` and counts by `document_class.class_name`. This is MATLAB-style: one network round-trip returning **all docs**. We'd want to port this to use the cloud `/datasets/:id/document-class-counts` endpoint instead (which we already use in v2).

### 1.5 Subject / probe / epoch counts

No dedicated count helper — callers do `len(session.database_search(ndi_query("").isa("subject")))`. In a v2 context this maps cleanly to `POST /ndiquery` with `scope=<datasetId>` and `searchstructure = isa:subject` + returning `number_matches`.

---

## 2. B1-B5 scoring

### B1 — Experiment-summary synthesizer

**Partial-60%.**

- `datasetSummary()` at `src/ndi/util/dataset_summary.py:17` returns `{numSessions, references, sessionIds, sessionSummaries}` but is **symmetry-testing focused** — session summaries list daq system names, probe names, and file listings, **not species/brain-region/technique rollups**. Not a product-grade summary.
- The real facts live in `ndi.fun.doc_table.subject|probe|epoch` — they are **per-document tables**, not rollups. The v2 backend would need to aggregate the tables (distinct species, distinct regions, top-N techniques, subject count, etc.). The extraction logic per row is reusable as-is; aggregation is a thin wrapper.
- **Recommendation:** port the field-access pattern from `doc_table.subject` (lines 684-826) into v2's `services/dataset_summary.py`, then roll up to `{species: [...], regions: [...], techniques: [...], subject_count, probe_count, epoch_count}`. Do **not** reuse `datasetSummary()` verbatim — it requires a local session object and is the wrong shape.

### B2 — Catalog cards ("list published datasets with summary facts")

**Partial-30%.**

- `getPublished()` at `src/ndi/cloud/api/datasets.py:161` just returns raw `GET /datasets/published` — same endpoint v2 already calls.
- No helper exists that hydrates a list of published datasets with species/region/technique facts. Each dataset's summary requires separate queries. The iteration pattern `for ds in getPublished().get("datasets"): summarize(ds["id"])` is the obvious shape but is **not implemented** — it would be net-new code.
- **Recommendation:** build the card-summary concurrency fan-out in v2 backend using `asyncio.gather`. There's no prior art to port.

### B3 — Query-page facets ("all species / regions / techniques across the platform")

**Doesn't-exist.**

- No `facet()` or `listSpeciesInUse()` helper.
- `ontologyTableRowVars()` at `src/ndi/fun/doc.py:408` is the closest — it collects all unique variable-names across a session's `ontologyTableRow` docs. But it's single-session, walks every doc locally, and doesn't cross datasets.
- `find_technique_names()` / `find_controlled_instance()` at `openminds_convert.py:217,257` enumerate **the whole openMINDS vocabulary**, not what's actually in use. They answer "what are valid values?", not "what values appear in the platform?".
- **Recommendation:** build in v2 backend. Use `POST /ndiquery` with `scope=all`, `searchstructure=isa:openminds_subject`, project `openminds.fields.name` + `openminds.openminds_type`, aggregate distinct. Cloud-side aggregation would be 10-100x faster than client-side — worth a feature request to ndi-cloud-node.

### B4 — Extraction affordances ("code snippet to load dataset X")

**Exists.** Canonical snippet below. Tutorial `tutorials/tutorial_67f723d574f5f79c6062389d.py:352-372` is the reference.

Canonical literal snippet for the Python tab of v2's snippet UI:

```python
import os
import ndi.dataset
from ndi.cloud import downloadDataset
from ndi.cloud.auth import login
from ndi.cloud.client import CloudClient

cloud_dataset_id = "<DATASET_ID>"
data_path = os.path.expanduser("~/ndi-datasets")
dataset_path = os.path.join(data_path, cloud_dataset_id)

# Set env vars NDI_CLOUD_USERNAME / NDI_CLOUD_PASSWORD first,
# or pass credentials directly to login().
config = login(
    os.environ["NDI_CLOUD_USERNAME"],
    os.environ["NDI_CLOUD_PASSWORD"],
)
client = CloudClient(config)

# First run: download (may take minutes). Subsequent runs: just reopen.
if os.path.exists(dataset_path):
    dataset = ndi.dataset.Dataset(dataset_path)
else:
    dataset = downloadDataset(
        cloud_dataset_id, data_path, verbose=True, client=client
    )

# Quick summary tables:
from ndi.fun.doc_table import subject, probe, epoch
subject_df = subject(dataset)   # species, strain, sex, treatments
probe_df   = probe(dataset)     # probe locations, cell types
epoch_df   = epoch(dataset)     # per-epoch stimulus/approach
```

Two compact variants we could offer as tabs:
- "Browse-only" (no local download) — use `ndi.cloud.api.documents.ndiqueryAll(scope=cloud_dataset_id, search_structure=ndi_query("").isa("subject"))` directly.
- "Single doc" — `ndi.cloud.api.documents.getDocument(dataset_id, document_id)`.

### B5 — Dataset lineage (walk `depends_on` / `classLineage` at dataset level)

**Doesn't-exist.**

- No occurrences of `classLineage` anywhere in the repo.
- `depends_on` is walked **within a single document/element** — see `src/ndi/fun/probe/location.py:46-53` which follows `underlying_element` chains to find the root probe.
- `src/ndi/cloud/sync/operations.py` does dependency resolution during sync but only for upload/download ordering, not for exposing lineage.
- No dataset-level "which datasets contributed to this one" or "what upstream docs were used" traversal.
- **Recommendation:** v2 frontend can build this on top of the indexed `depends_on` field the cloud already exposes (per CLAUDE.md). No port target here.

---

## 3. Cross-cutting findings

### HTTP client shape (for PR-6 host allowlist cross-reference)

NDI-python uses `requests.Session` with plain `Authorization: Bearer <JWT>` header (`cloud/client.py:156,240-252`). Token flow:
- `login()` → `POST /auth/login` with `{email, password}`, stores `token` and `user.organizations[0].id` (`cloud/auth.py:155-187`).
- JWT decoded locally (unverified — see the warning at `cloud/auth.py:30`) only for expiration checks, via `decodeJwt()`.
- No refresh flow — when `verifyToken()` fails, caller must re-login. Matches our ADR 005 note.

Regarding **PR-6 `download_file` host allowlist**: `cloud/api/files.py:139` (`getFile(url, target_path)`) **does not validate the URL host** — it blindly GETs any presigned S3 URL handed to it. In NDI-python context that's fine because the URL comes from our trusted `getFileDetails` / bulk-download response. But if we expose an equivalent in v2 backend (e.g., the "fetch binary" feature), we must enforce the v2 host allowlist because the URL is user-facing.

`cloud/download.py:24-78` (`_download_chunk_zip`) also fetches from presigned S3 URLs with no host check. Same caveat.

### Fact-extraction helpers worth porting verbatim to v2 backend

| NDI-python symbol | v2 backend home | Reason |
|---|---|---|
| `doc_table.subject()` (extraction block lines 684-826) | `services/dataset_summary.py` | Already production-hardened: main-vs-background-strain disambiguation, wildtype-preference for GeneticStrainType, dynamic treatment-column generation with ontology resolution. Reinventing this is weeks of work. |
| `doc_table.probe()` (lines 233-308) | `services/probe_summary.py` | Captures probe-location + cell-type join. |
| `doc_table.epoch()` (lines 311-447) | `services/epoch_summary.py` | Captures epochprobemap TSV parsing, stimulus-bath mixture resolution, per-epoch approach. |
| `find_technique_names()` (`openminds_convert.py:257`) | `services/ontology.py` (validation layer) | Enumerates openMINDS Technique / AnalysisTechnique / StimulationApproach controlled vocab — useful for facet validation. |
| `openminds_convert._get_depends_on()`-style pattern | helpers in `services/projection.py` | Used 10+ times across doc_table; a tiny reusable helper. |

**Suggested ports strategy:** create `backend/services/openminds_shapes.py` that exposes **pure functions** taking raw `document_properties` dicts and returning typed records (`SubjectRow`, `ProbeRow`, `EpochRow`). No session, no requests, no pandas — just dict→dataclass translation. This keeps v2's `services/` pure per CLAUDE.md rule 3. The pandas-based `doc_table` versions in NDI-python are the reference; our port is the "dict-in, pydantic-out" variant.

### Test fixtures with openMINDS shapes (for PR-11 binary OOM test)

Best test fixtures for openMINDS subject/probe/epoch documents:
- `tests/test_phase1_gaps.py:645-667` — exercises `openminds_subject` docs with `depends_on.subject_id` and checks `openminds.openminds_type` contains "Species". Mocked, clean, reusable.
- `tests/test_phase2_gaps.py` — similar patterns for phase-2 features.
- `tests/matlab_tests/test_dabrowska.py` — uses the real Dabrowska dataset (ID `67f723d574f5f79c6062389d`), downloads from cloud. Heavy; good for end-to-end but not unit-friendly.
- `tests/test_cloud_api_datasets.py` — cloud API unit tests with mocks.

For PR-11 binary OOM: NDI-python doesn't have a fixture representing a large multi-epoch binary payload. The epochfiles TSV parsing in `doc_table.epoch()` (lines 388-394) is the closest — if we want a deterministic OOM test, we'd need to synthesize one ourselves. None of NDI-python's existing test fixtures would trigger OOM; their largest ones are a few kB of JSON.

### No-ports / intentionally-different decisions

- **Do NOT port `CloudClient`.** v2's `backend/clients/ndi_cloud.py` already does httpx HTTP/2, circuit-breaker, and structlog integration. NDI-python's client is plain `requests.Session` without any of those — strictly worse for a multi-tenant proxy.
- **Do NOT port `downloadDataset`.** It downloads every doc into local SQLite — directly violates v2's ADR 004 (no SQLite dataset storage). Our v2 equivalent is "issue `ndiquery` with scope=`<datasetId>` and return JSON directly to the browser".
- **Do NOT port the environment-variable credential flow.** v2 has Redis-encrypted per-user sessions; NDI-python's `NDI_CLOUD_USERNAME`/`NDI_CLOUD_PASSWORD` env-var pattern is for single-user CLI usage.

---

## 4. Appendix — openMINDS JSON path reference (for v2 summary service)

For `isa("openminds_subject")` docs, the full-path extraction for the five main facts:

```text
SpeciesName:       openminds.fields.name                       (when openminds_type endswith "Species")
SpeciesOntology:   openminds.fields.preferredOntologyIdentifier
StrainName:        openminds.fields.name                       (when openminds_type endswith "Strain" AND backgroundStrain!=[])
StrainOntology:    openminds.fields.ontologyIdentifier
BioSex:            openminds.fields.name                       (when openminds_type endswith "BiologicalSex")
BioSexOntology:    openminds.fields.preferredOntologyIdentifier
GeneticStrainType: openminds.fields.name                       (when openminds_type endswith "GeneticStrainType"; prefer non-"wildtype" when ≥2)
SubjectLink:       depends_on[name=="subject_id"].value
```

For `isa("probe_location")` docs (linked to an element via `depends_on.probe_id`):

```text
RegionName:        probe_location.name
RegionOntology:    probe_location.ontology_name
```

For `isa("openminds_stimulus")` docs (linked by `epochid.epochid`):

```text
TechniqueName:     openminds.fields.name
TechniqueOntology: openminds.fields.preferredOntologyIdentifier
```

For `isa("element")` docs (subject/probe linkage):

```text
SubjectLink:   depends_on[name=="subject_id"].value
ProbeName:     element.name
ProbeType:     element.type
ProbeRef:      element.reference
```

These paths are exactly what v2 backend's `/datasets/:id/summary` endpoint should project from `POST /ndiquery`. Because cloud auto-injects `isa` (per CLAUDE.md), we can issue one query per type and join client-side in the proxy — or push the join server-side if we add a `/datasets/:id/openminds-summary` endpoint to ndi-cloud-node.

---

**End of report.**
