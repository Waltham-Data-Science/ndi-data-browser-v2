# Spike-0 Subagent D: DID-Schema + ndi-data-browser v1

**Scope:** canonical JSON paths in DID/openMINDS documents; audit of v1 browser for summarizer logic.

---

## Part 1 — DID-Schema

### 1.1 Which folder is authoritative?

Only ONE physical folder exists: `/Users/audribhowmick/Documents/ndi-projects/DID-schema` (capital D, lowercase schema). macOS is a **case-insensitive filesystem**, so `DID-Schema` and `did-schema` resolve to the same inode. `ls -lai` confirms a single entry. Remote: `https://github.com/Waltham-Data-Science/DID-schema.git` (capital D in the GitHub URL). Last commit: `9db99b6 Merge pull request #6 ... Add directory support`. 105 tracked files.

**Conclusion: use `DID-schema` — there is no second folder to de-duplicate.**

### 1.2 How schema files vs document instances are shaped

**Schema files** (`schemas/*/schema.json`) declare types using `_fields` as the universal key. But **wire-format documents** (what the cloud stores and returns) use the **classname as a top-level key**. This is confirmed by `tests/fixtures/valid_probe_location_document.json`:

```json
{
    "document_class": {
        "classname":     "probe_location",
        "superclasses": [{ "classname": "base", ... }]
    },
    "depends_on": [
        { "name": "probe_id", "value": "aabb..._aabb..." }
    ],
    "base": {
        "id":         "aabb..._1122...",
        "session_id": "aabb..._9900...",
        "name":       "left_hemisphere_probe_location",
        "datestamp":  "2024-06-01T12:00:00.000Z"
    },
    "probe_location": {
        "ontology_name": "uberon:0002436",
        "name":          "primary visual cortex"
    }
}
```
(Source: `DID-schema/tests/fixtures/valid_probe_location_document.json`)

Note: REPO_SPEC calls out that `_fields` is the canonical schema key and older class-named keys are deprecated in the **schema** format — but **documents still key their data by classname**, and the superclass fields live at that superclass's classname key (e.g., `base.id`). Any synthesizer needs to read both `base.*` (inherited) and `<classname>.*` (own fields).

### 1.3 Top-level document types (inventory)

Under `DID-schema/schemas/`:

| Dir | Subtypes | What it carries |
|---|---|---|
| `base` | (single `schema.json`) | `id`, `session_id`, `name`, `datestamp`. All docs inherit. |
| `subject` | (single) | `local_identifier`, `description`. Abstract — no species info. |
| `animalsubject` | (single) | **`scientific_name` (NCBITaxon), `genbank_commonname`** |
| `subjectmeasurement` | (single) | Generic `measurement`/`value` pair (superclasses `base` + `subject`). **This is where age/sex/weight/strain/etc are stored as key-value measurements, NOT as dedicated fields.** |
| `subject_group` | (single) | `group_name`, `description`, `subject_ids` CSV |
| `element` | `element/schema`, `position_metadata`, `distance_metadata` | `element_name`, `element_type`, `ndi_element_class`, `reference`. **depends_on: `subject_id`, `underlying_element_id`** |
| `probe/*` | `probe_location`, `probe_geometry`, `electrode_offset_voltage`, `site2channelmap` | There is **no `probe` class** — a probe is an `element` (via `ndi_element_class`). `probe_location` attaches via `depends_on: probe_id`. `probe_geometry` has `probe_type` string (e.g., "Neuropixels 1.0"). |
| `session` | (single) | `reference`, `type` ("acute"/"chronic"), `date`, `purpose` |
| `session_in_a_dataset` | (single) | `dataset_id` (links session to dataset) |
| `dataset_remote` | (single) | `remote_url`, `remote_type`, `dataset_id` |
| `dataset_session_info` | (single) | `dataset_id`, `session_index`, `reference_path` |
| `treatment/*` | `treatment`, `treatment_drug`, `virus_injection` | `drug_name`/`dose`/`route` on treatment_drug; `virus_name`/`serotype`/`injection_site` on virus_injection. All depend on `subject_id`. |
| `stimulus/*` | `stimulus_presentation`, `stimulus_parameter`, `stimulus_response`, `stimulus_tuningcurve`, `vision/*`, ... | `stimulus_presentation.stimulus_type`, `stimulus_parameter` holds parameter map |
| `daq/*` | `daqsystem`, `daqreader`, `daqreader_ndr`, `daqmetadatareader`, `filenavigator`, `syncgraph`, `syncrule` | `daqsystem.ndi_daqsystem_class` identifies acquisition hardware class |
| `metadata/openminds*` | `openminds`, `openminds_element`, `openminds_subject`, `openminds_stimulus` | See 1.4. |
| `epochid`, `epochclocktimes`, `oneepoch`, `element_epoch` | each single | Time-segment bookkeeping |
| `neuron/neuron_extracellular` | single | Sorted neuron with spike waveform stats |
| `sorting/SpikeInterfaceSortingOutputs`, `apps/spikeextractor/*`, `apps/spikesorter/*`, `apps/jrclust/*`, `apps/markgarbage/*`, `apps/vhlab_voltage2firingrate/*`, `apps/calculators/*` | many | Analysis-stage outputs |
| `data/*` | `image`, `imageStack`, `imageStack_parameters`, `imageCollection`, `ontologyTableRow`, `ontologyLabel`, `ontologyImage`, `pyraview`, `ngrid`, `fitcurve`, `filter`, `generic_file`, `binaryseries_parameters`, `directory` | Binary + derived data docs |
| `ingestion/*` | `daqreader_epochdata_ingested`, `daqreader_mfdaq_epochdata_ingested`, `daqmetadatareader_epochdata_ingested`, `epochfiles_ingested`, `metadata_editor`, `syncrule_mapping` | Ingest-pipeline tracking |
| `measurement`, `mock/*`, `app`, `apps`, `ingestion`, `projectvar`, `demoNDI` | various | Infra/testing |

### 1.4 openMINDS

Schema `metadata/openminds/schema.json` (`DID-schema/schemas/metadata/openminds/schema.json`) defines **three fields on the openminds doc itself**:

```
openminds_type      char   e.g. "core.Subject", "core.DatasetVersion", "controlledTerms.Species"
openminds_data      structure  <-- the entire openMINDS payload lives HERE
openminds_version   char   e.g. "v3"
```

Crucially, `metadata/openminds_subject/schema.json`, `openminds_element/schema.json`, `openminds_stimulus/schema.json` each have **`"_fields": []`** — they carry NO fields of their own. They only exist to create a typed dependency link via `depends_on.subject_id` / `element_id` / `stimulus_id`.

**Key implication:** the authoritative species/strain/sex/etc. data is inside an `openminds` doc's `openminds_data` structure, and you discover *which subject it belongs to* by finding an `openminds_subject` doc whose `depends_on.subject_id` matches and then fetching its id as a sibling. (Or more directly: find `openminds` docs whose `openminds_type == "core.Subject"` and traverse openMINDS' own link graph.)

The DID-schema repo does not define the inner shape of `openminds_data` — it delegates to the openMINDS v3 external spec. The synthesizer must know openMINDS shapes (e.g., `core.Subject.species` is an array of `@id` refs pointing to `controlledTerms.Species` entities).

### 1.5 Canonical JSON paths for each fact

**Remember:** these are paths *inside a single document's JSON body*. To get dataset-wide facts, query the cloud with `isa` filters on classLineage and collect.

| Fact | Primary canonical path | Document class | Notes / alt paths |
|---|---|---|---|
| **Species** | `animalsubject.scientific_name` | `animalsubject` | Latin binomial, NCBITaxon-aligned. `animalsubject` superclasses `base`. Also `animalsubject.genbank_commonname` for common name. |
| | `openminds.openminds_data.species` | `openminds` (where `openminds_type == "core.Subject"`) | openMINDS v3 ref: `[{"@id": "https://openminds.../species/mus-musculus"}]`. Source of `subjects_speciesname` column in v1. |
| **Strain** | **NOT a dedicated field on any schema.** | | Stored as `subjectmeasurement.measurement == "strain"`, `subjectmeasurement.value == "<strain name>"`. Also `openminds.openminds_data.strain` when `openminds_type == "core.Subject"`. |
| **Age** | **Not a dedicated field.** | | `subjectmeasurement.measurement == "age"`, `.value == "<value>"`, `.measurement_datestamp`. Units/range are encoded inside the `value` string — no structured units/range in DID schema. openMINDS version: `openminds.openminds_data.studiedState[*].age` (with `.value`, `.unit`, `.minValue`, `.maxValue`). |
| **Sex** | **Not a dedicated field.** | | `subjectmeasurement.measurement == "sex"`, `.value == "male"\|"female"`. openMINDS: `openminds.openminds_data.biologicalSex` (controlled vocabulary: `Male`/`Female`/`Unknown`). |
| **Brain region** | `probe_location.ontology_name` + `probe_location.name` | `probe_location` | `ontology_name` holds the prefixed ontology ID (e.g., `"uberon:0002436"`), `name` holds the human label ("primary visual cortex"). `probe_location.depends_on.probe_id` links back to the `element` that is the probe. |
| | `element` position (rarely holds region) | `element.position_metadata` | Just x/y/z coords + `coordinate_system`. NOT brain region — spatial only. |
| **Technique / modality** | `element.element_type` | `element` | Free-text string (e.g., `"n-trode"`, `"sharp"`, `"patch"`). Also `element.ndi_element_class`. |
| | `probe_geometry.probe_type` | `probe_geometry` (depends on probe element) | Device model string (e.g., `"Neuropixels 1.0"`, `"tetrode"`). |
| | `daqsystem.ndi_daqsystem_class` | `daqsystem` | DAQ hardware class name. |
| | `stimulus_presentation.stimulus_type` | `stimulus_presentation` | The kind of stimulus, not the recording technique. |

### 1.6 Multi-path fields (extraction-bug risk)

These facts have **more than one valid source**. A synthesizer that reads only one will silently drop data:

1. **Species** — `animalsubject.scientific_name` OR `openminds` (type=`core.Subject`).`openminds_data.species`. Datasets may populate one, the other, or both. Cloud's pre-aggregated `species` field (see 2.1) is whichever it picked; to be complete, query both.
2. **Strain, sex, age, weight** — can live in (a) `subjectmeasurement` docs (generic key-value), OR (b) inside `openminds` docs as structured fields. NDI's `subject_summary` (v1 uses it) already joins both sources.
3. **Brain region** — `probe_location` is canonical for *where a probe is*, but at the **element** level there's no single region field; some datasets may encode region in `element.element_name` or as an `ontologyTableRow` measurement. Scoping to probe_location alone is safest but will miss non-probe elements (e.g., imaging ROIs).
4. **Technique** — the "technique" concept is diffused across `element.element_type`, `probe_geometry.probe_type`, `daqsystem.ndi_daqsystem_class`, and `stimulus_presentation.stimulus_type`. There is no single "technique" field. The synthesizer must pick a policy (e.g., "report probe_geometry.probe_type if any probe exists, else DAQ class, else element_type").
5. **Subject ID linkage** — `element.depends_on.subject_id`, `treatment.depends_on.subject_id`, `subjectmeasurement` (via inheritance from `subject`), `openminds_subject.depends_on.subject_id`. All four must align to correctly attribute a measurement to a subject.

### 1.7 Dataset-level vs document-level

**DID-schema defines no explicit "dataset metadata" document type.** A dataset in the cloud is a collection of sessions (`session_in_a_dataset` links session_id -> dataset_id) plus a `dataset_remote` doc holding the storage URL.

Instead, **the NDI Cloud API itself already returns pre-aggregated dataset-level fields** on `GET /datasets/published` — see 2.1. Specifically: `species`, `brainRegions`, `abstract`, `license`, `doi`, `numberOfSubjects`, `documentCount`, `neurons`, `contributors`, `correspondingAuthors`, `associatedPublications`, `funding`, `affiliation`, `totalSize`.

So the "dataset-level species" is **set by whoever publishes the dataset to the cloud catalog** — not synthesized from subject docs at read time. This is the same entity in v1's `DatasetSummary.species` (a plain string, often comma-separated). A v2 Plan B synthesizer has two choices:

- **Trust the cloud field** (fast, simple, matches v1 behavior).
- **Recompute by scanning all subject/openminds docs for the dataset** (slower, authoritative, good for "suspicious metadata" callouts).

Per the 2026-04-16 cloud capabilities, `ndiquery` with `scope=<dataset-id>` and `isa animalsubject` / `isa openminds_subject` would return everything a synthesizer needs for brain-region and species aggregation via bulk-fetch of matching docs.

---

## Part 2 — ndi-data-browser (v1)

Located at `/Users/audribhowmick/Documents/ndi-projects/ndi-data-browser`. Stack: FastAPI + NDI-Python + SQLite + React 19/shadcn. Architecture: on-demand SQLite download per dataset via NDI-Python's `Dataset.bulk_add`, then summary tables produced by `ndi.fun.doc_table.*` server functions.

### 2.1 Does v1 have a "dataset summary" surface?

**YES — but it is NOT synthesized from documents by v1 itself.** v1 reads pre-aggregated catalog fields supplied by the NDI Cloud `/datasets/published` response and passes them through.

Source: `ndi-data-browser/backend/services/dataset_service.py:40-55`:

```python
def _raw_to_summary(ds: dict[str, Any]) -> DatasetSummary:
    return DatasetSummary(
        id=str(ds.get("_id", ds.get("id", ""))),
        name=ds.get("name", ""),
        abstract=ds.get("abstract"),
        species=ds.get("species"),
        brain_regions=ds.get("brainRegions"),
        license=ds.get("license"),
        doi=ds.get("doi"),
        total_size=ds.get("totalSize"),
        document_count=ds.get("documentCount"),
        number_of_subjects=ds.get("numberOfSubjects"),
        is_published=ds.get("isPublished", False),
        created_at=str(ds.get("createdAt", "")),
        updated_at=str(ds.get("updatedAt", "")),
    )
```

And the parallel `_raw_to_detail` at line 58 adds `neurons`, `affiliation`, `contributors`, `corresponding_authors`, `associated_publications`, `funding`.

**Output shape** (`ndi-data-browser/backend/models/datasets.py`):

```
DatasetSummary: id, name, abstract, species, brain_regions, license, doi,
                total_size, document_count, number_of_subjects, is_published,
                created_at, updated_at

DatasetDetail:  DatasetSummary fields + neurons, affiliation, contributors[],
                corresponding_authors[], associated_publications[], funding[]
```

**Where the summary appears in the UI:**
- `DatasetCard.tsx` (`frontend/src/components/datasets/DatasetCard.tsx`): grid catalog cards show `name`, truncated `abstract`, badges for `species`, `brain_regions`, `license`, plus `document_count`, `number_of_subjects`, `total_size`, `created_at`, `doi`.
- `DatasetDetailPage.tsx` (`frontend/src/pages/DatasetDetailPage.tsx:52-441`): hero with title + species/brain_regions/license badges; sections for Abstract, Document Types (bar list with counts), Associated Publications (DOI/PMID/PMC links), Contributors, Corresponding Authors, Funding; right sidebar with counts (docs, subjects, neurons, size, dates, DOI, affiliation).

### 2.2 Where the REAL synthesis happens

v1 **delegates all per-subject / per-probe / per-epoch synthesis to NDI-Python's `ndi.fun.doc_table` module** — it doesn't re-implement the logic. See `ndi-data-browser/backend/services/table_service.py`:

- `get_subject_table()` (L277) -> calls `ndi.fun.doc_table.subject_summary(session)` which "joins subject docs with openminds_subject (strain, species, sex) and treatment docs". Falls back to `subject_table()` if rich summary fails.
- `get_probe_table()` (L333) -> `ndi.fun.doc_table.probe_table(session)`.
- `get_epoch_table()` (L353) -> `ndi.fun.doc_table.epoch_table(session)`.
- `get_element_table()` (L377) -> `ndi.fun.doc_table.element_table(session)`.
- `get_treatment_table()` (L397) -> `ndi.fun.doc_table.treatment_table`.
- `get_openminds_table()` (L429) -> `ndi.fun.doc_table.openminds_table`.
- `get_ontology_table()` (L457) -> `ndi.fun.doc_table.ontology_table_row_doc_to_table`.
- `get_combined_table()` (L527) -> joins subject+probe+epoch DataFrames via `ndi.fun.table.join`.

**The `session` passed in is either NDI's native `Dataset` object (preferred) or v1's `_DirectSQLiteSession` fallback class (L222) that emulates `database_search(query)` by doing a JSON LIKE-query against the SQLite `docs` table.**

**Upshot for v2:** v1 has **no portable synthesizer logic to steal** — the smarts live in NDI-Python (`ndi.fun.doc_table`). The right thing for v2 is either:
1. Keep using `ndi.fun.doc_table.*` (if cloud query results can be wrapped to look like NDI-Python docs); or
2. Re-implement the fact-extraction rules fresh against the schema paths documented in Part 1.

### 2.3 Per-subject facts v1 surfaces (from `table-column-definitions.ts`)

The frontend column definitions file at `ndi-data-browser/frontend/src/data/table-column-definitions.ts` is the best spec of what v1 renders. Key subject columns (driven by `ndi.fun.doc_table.subject_summary`):

- `subjects_species` — **NCBITaxon ontology ID** (e.g., `NCBITaxon:10090`)
- `subjects_speciesname` — **human-readable species name from OpenMINDS** (e.g., "Mus musculus")
- `subjects_strain` — genetic strain/line
- `subjects_strainname` — OpenMINDS readable strain name (e.g., "C57BL/6J")
- `subjects_sex`, `subjects_biologicalsex` — both columns exist
- `subjects_age`, `subjects_weight`
- `subjects_geneticstraintype` — OpenMINDS (wildtype/knockin/knockout)
- `subjects_treatment`, `subjects_treatmentname` — linked from treatment docs

Probe columns:
- `probes_location` — **UBERON ontology ID** (e.g., `UBERON:0002436`)
- `probes_area` — brain area label
- `probes_type` — device type
- `probes_cell_type` — **CL (Cell Ontology) ID**

Epoch columns include `stimulusname` and `stimulusparameters`. OpenMINDS columns mirror subject columns (species, strain, biologicalsex, etc.) with NCBITaxon ontologyPrefix hints.

### 2.4 Cite / "use this data" affordances

**None.** No components match `cite|citation|use this|how to cite`. The only external link affordances are per-publication DOI/PMID/PMC badges on `DatasetDetailPage.tsx` (L162-193) and ORCID links on contributors (L219). For Plan B B4, v2 would be the first to add a first-class "How to cite this dataset" block; the raw materials are already in `DatasetDetail.doi`, `.contributors`, `.corresponding_authors`, `.associated_publications`, `.affiliation`, `.created_at`, `.updated_at`.

### 2.5 Features to preserve in v2's redesign

From the v1 catalog + detail surface:
- Catalog card: name, short abstract, species/brain_regions/license badges, doc count, subject count, size, created_at, DOI (`DatasetCard.tsx`).
- Client-side search that matches name, abstract, species, brain_regions (`DatasetsPage.tsx:17-27`).
- Detail page "Document Types" breakdown (`DatasetDetailPage.tsx:98-143`) — v2 already has `/datasets/:id/document-class-counts`.
- Publications, contributors, corresponding authors, funding sections — all present in v1 and useful for a citation block.
- Summary tables: Subjects (rich), Subjects Basic, Probes, Epochs, Elements, Treatment, OpenMINDS, Ontology, and the **Combined** (subject+probe+epoch joined) table — the last one is flagged as "the primary table researchers use (see Dabrowska tutorial)" in the code (L528-530).

---

## Bottom line for Plan B B1

**Authoritative paths the v2 synthesizer should read:**

- **Species:** `animalsubject.scientific_name` AND openMINDS `core.Subject` docs' `openminds.openminds_data.species`. Fall back to the cloud's pre-aggregated `species` string when both are empty.
- **Strain/sex/age/weight:** query `subjectmeasurement` docs (`measurement` + `value`) OR openMINDS `core.Subject` structured fields (`strain`, `biologicalSex`, `studiedState[*].age`, etc.). No dedicated DID-schema fields exist.
- **Brain region:** `probe_location.ontology_name` (prefix form, e.g., `uberon:0002436`) + `probe_location.name`. This is the only schema field that names brain regions directly. Map to elements via `probe_location.depends_on.probe_id -> element.id -> element.depends_on.subject_id`.
- **Technique/modality:** there is no single field. Policy: aggregate `probe_geometry.probe_type` (device model) + `element.element_type` (e.g., n-trode/sharp/patch) + `daqsystem.ndi_daqsystem_class`. Stimulus paradigm comes from `stimulus_presentation.stimulus_type`.

**Dataset-level note:** the cloud already returns `species` and `brainRegions` strings at dataset granularity. v1 trusts them directly. The synthesizer is really about **per-subject and per-probe breakdown** (e.g., "3 C57BL/6J mice, 1 Long-Evans rat; 14 probes in V1, 8 in VM"), not about re-deriving the flat catalog badge.

**v1 has no summarizer worth porting** — the display layer is good (use as ux reference), but all fact-extraction is delegated to `ndi.fun.doc_table.subject_summary`, which runs against a downloaded SQLite dataset. v2's cloud-first synthesizer must re-implement that join (subject + openminds_subject + treatment + probe_location + element) on top of `POST /ndiquery` results, not against local SQLite.
