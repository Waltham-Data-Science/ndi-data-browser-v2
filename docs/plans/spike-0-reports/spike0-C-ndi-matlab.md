# Spike-0 Review C: NDI-matlab (MATLAB reference implementation)

**Reviewer role:** Parallel subagent C (of 4) — MATLAB researcher day-to-day truth
**Target repo:** `VH-Lab/NDI-matlab` (local clone at `/Users/audribhowmick/Documents/ndi-projects/NDI-matlab`)
**Branch / commit:** `main` @ `2e4f06955b97496bbb678879d5b4ce5bb47e2ec9` ("Merge pull request #739 from VH-Lab/claude/add-neuropixels-ajbpod-anvi9") — authored 2026-04-10 15:04:52 -0400 (latest as of 2026-04-17).
**Origin confirmed:** `[remote "origin"] url = https://github.com/VH-Lab/NDI-matlab.git`
**Date of review:** 2026-04-17

---

## 1. The exact MATLAB functions the user cited

All three exist. They are in the `+ndi/+fun/` package and form a small, tightly coupled family.

### 1.1 `ndi.fun.docTable.subject` — THE canonical subject summary

**Path:** `/Users/audribhowmick/Documents/ndi-projects/NDI-matlab/src/ndi/+ndi/+fun/+docTable/subject.m`

**Signature:**
```matlab
function [subjectTable] = subject(session, options)
arguments
    session {mustBeA(session,{'ndi.session.dir','ndi.dataset.dir'})}
    options.hideMixtureTable (1,1) logical = true;
end
```

**Docstring (verbatim):**
> SUBJECT Creates a summary table of subjects and their associated metadata.
> subjectTable = subject(SESSION)
>
> This function queries an NDI session to find all subject documents. For each
> subject, it then finds and integrates information from associated 'openminds'
> documents related to 'Strain', 'Species', and 'BiologicalSex', as well as
> 'treatment' documents. This is done by performing a minimal number of broad
> queries and passing the results to helper functions for targeted processing.
>
> The function aggregates properties from these dependent documents, such as
> species name, strain, biological sex, and treatment details. It then
> formats this aggregated information into a single summary table. Each row
> in the output table represents a unique subject, and the columns contain
> the subject's identifiers along with details from its linked documents.
> Metadata from associated documents is joined using 'SubjectDocumentIdentifier'.
>
> This function is robust to missing metadata; if a session lacks strain,
> species, or biological sex documents, the function will still run and
> simply omit the corresponding columns from the final table.

**Key facts for us:**
- Row grain = **one row per subject document**.
- It is purely a table — **NO prose sentence is ever produced**. There is no "sentence/paragraph" synthesizer function anywhere in the codebase. All "summary" means in MATLAB-land is a tabular aggregation.
- It always includes these core columns, guaranteed: `SessionIdentifier`, `SubjectDocumentIdentifier`, `SubjectLocalIdentifier`.
- It then outer-joins (via MATLAB's built-in `outerjoin(..., 'MergeKeys', true)`) against:
  - Strain (openminds) → adds `StrainName`, `StrainOntology`, and cascaded dependents like `SpeciesName`, `SpeciesOntology`, `BackgroundStrainName`, `BackgroundStrainOntology`, `GeneticStrainTypeName`.
  - BiologicalSex (openminds) → adds `BiologicalSexName`, `BiologicalSexOntology`.
  - Treatment (from `ndi.fun.docTable.treatment`) → adds a dynamic set of columns named by the ontology-resolved data type of the treatment measurement (e.g. `OptogeneticTetanusStimulationTargetLocationName`/`Ontology`, `DrugTreatmentLocationName`/`Ontology`, `DrugTreatmentOnsetTime`, `DrugTreatmentDuration`, etc.).
- Falls back gracefully: if the session has no strain docs, it tries Species; if no openminds at all, it just returns the core three columns.
- If the row count after all joins != number of subject documents, it emits warning `NDIFUNDOCTABLESUBJECT:SubjectMismatch`.

**Core body (paraphrased, key lines):**
```matlab
% Step 1: Get all subject documents
query = ndi.query('','isa','subject');
subjectDocs = session.database_search(query);
doc_ids = cellfun(@(d) d.document_properties.base.id, subjectDocs, 'UniformOutput', false);
local_ids = cellfun(@(d) d.document_properties.subject.local_identifier, subjectDocs, 'UniformOutput', false);
session_ids = cellfun(@(d) d.document_properties.base.session_id, subjectDocs, 'UniformOutput', false);
subjectTable = table(session_ids(:), doc_ids(:), local_ids(:), ...
    'VariableNames', {'SessionIdentifier', 'SubjectDocumentIdentifier', 'SubjectLocalIdentifier'});

% Step 2: One broad query for ALL openminds docs (expensive, do once)
query = ndi.query('','isa','openminds');
allOpenMindsDocs = session.database_search(query);

% Step 3: Process and outer-join
[strainTable, ~, strainSubjects] = ndi.fun.docTable.openminds(session, 'Strain', ...
    'depends_on','subject_id','depends_on_docs',subjectDocs, ...
    'allOpenMindsDocs',allOpenMindsDocs);
if ~isempty(strainTable)
    strainTable.SubjectDocumentIdentifier = strainSubjects;
    subjectTable = outerjoin(subjectTable, strainTable, 'MergeKeys', true);
end
% ... repeats for BiologicalSex and treatment ...
```

### 1.2 `ndi.fun.table.join` — the canonical join pattern

**Path:** `/Users/audribhowmick/Documents/ndi-projects/NDI-matlab/src/ndi/+ndi/+fun/+table/join.m`

**Signature:**
```matlab
function combinedTable = join(tables, options)
arguments
    tables (1,:) cell {mustBeNonempty, mustContainTables}
    options.uniqueVariables (1,:) {mustBeText} = ''
end
```

**Docstring summary (verbatim):**
> JOIN Combines two or more tables using common variables as keys, with custom aggregation.
>
> Combines a cell array of tables (TABLES) into a single table. The tables
> are joined using all common variables as keys.
>
> An optional 'uniqueVariables' parameter can be used to specify column
> names for which only unique values should be kept per aggregated row.
> Any duplicate rows (based on the 'uniqueVariables') are combined by
> aggregating the values of other columns into comma-separated strings.
> Numeric values are converted to strings for aggregation unless they
> result in a single unique numeric value, in which case the number is retained.

**Algorithm:** Step 1 does MATLAB's built-in `innerjoin` pairwise on common variable names (no user-provided key — it just uses whatever variables are in common). Step 2, if `uniqueVariables` is given, collapses to one row per unique-key combination and for every other column aggregates values via a helper: numeric → single number if unique, else CSV; strings/chars → CSV of unique values; mixed → CSV of string representations.

**Canonical tutorial usage (see §2):**
```matlab
combinedSummary = ndi.fun.table.join({subjectSummary, probeSummary, epochSummary}, ...
    'uniqueVariables', 'EpochDocumentIdentifier');
```
Key implication: researchers routinely think of the "summary table" at **three possible grains**: per-subject, per-probe, per-epoch — and `ndi.fun.table.join` is the glue that denormalizes them into whichever grain they want (typically per-epoch, then filtered).

### 1.3 Sibling docTable functions (discovered)

All share the same row-per-entity, denormalize-metadata-into-columns pattern. Together they form the MATLAB researcher's default mental model:

| Function | Row grain | Core columns |
|---|---|---|
| `ndi.fun.docTable.subject` | 1 per subject | `SessionIdentifier`, `SubjectDocumentIdentifier`, `SubjectLocalIdentifier` + dynamic (see §3) |
| `ndi.fun.docTable.probe` | 1 per probe | `SubjectDocumentIdentifier`, `ProbeDocumentIdentifier`, `ProbeName`, `ProbeType`, `ProbeReference`, `ProbeLocationName`, `ProbeLocationOntology`, `CellTypeName`, `CellTypeOntology` |
| `ndi.fun.docTable.epoch` | 1 per stimulus epoch | `EpochNumber`, `EpochDocumentIdentifier`, `ProbeDocumentIdentifier`, `SubjectDocumentIdentifier`, `local_t0`, `local_t1`, `global_t0`, `global_t1`, `MixtureName`, `MixtureOntology`, `ApproachName`, `ApproachOntology` |
| `ndi.fun.docTable.element` | 1 per element (probes + inferred) | `subject_id`, `element_id`, `element_name`, `element_type`, `element_reference` + dynamic metadata |
| `ndi.fun.docTable.treatment` | 1 per unique dependency | dynamic based on ontology dataType |
| `ndi.fun.docTable.openminds` | 1 per openminds doc of the given type | `{TypeName}Name`, `{TypeName}Ontology` + cascaded dependents |

---

## 2. Tutorials that demonstrate the synthesis shape

The **flagship tutorial** is the Francesconi et al. 2025 getting-started guide, at:
`/Users/audribhowmick/Documents/ndi-projects/NDI-matlab/src/ndi/docs/NDI-matlab/tutorials/datasets/Francesconi_et_al_2025/1_getting_started.md`

This is also published as `tutorial_67f723d574f5f79c6062389d.html` (dabrowska dir) and linked as a downloadable MATLAB Live Script `.mlx`. An almost identical shape is used in the Haley tutorial at `src/ndi/+ndi/+setup/+conv/+haley/tutorial_682e7772cdf3f24938176fac.html` and in its companion driver `nansen_demo.m`.

### 2.1 Literal tutorial output shapes (copy-paste-ready for B3)

**Call:** `subjectSummary = ndi.fun.docTable.subject(dataset)`

**Preview (literal from tutorial, first 2 rows — 13 columns, this is the ground-truth default shape):**

| SubjectDocumentIdentifier | SubjectLocalIdentifier | StrainName | StrainOntology | BackgroundStrainName | BackgroundStrainOntology | GeneticStrainTypeName | SpeciesName | SpeciesOntology | BiologicalSexName | BiologicalSexOntology | OptogeneticTetanusStimulationTargetLocationName | OptogeneticTetanusStimulationTargetLocationOntology |
| :--- | :--- | :--- | :--- | :--- | :--- | :--- | :--- | :--- | :--- | :--- | :--- | :--- |
| `412693bb0b2a75c8_c0dc4139300a673e` | `wi_rat_CRFCre_210818_BNST@dabrowska-lab.rosalindfranklin.edu` | CRF-Cre | | WI | RRID:RGD_13508588 | knockin | Rattus norvegicus | NCBITaxon:10116 | male | PATO:0000384 | | |
| `412693bb0b2b7e0f_40d1f45f9e51dc8b` | `sd_rat_OTRCre_220214_BNST@dabrowska-lab.rosalindfranklin.edu` | OTR-IRES-Cre | | SD | RRID:RGD_70508 | knockin | Rattus norvegicus | NCBITaxon:10116 | male | PATO:0000384 | | |

**Call:** `probeSummary = ndi.fun.docTable.probe(dataset)`

**Preview (literal from tutorial — 9 columns):**

| SubjectDocumentIdentifier | ProbeDocumentIdentifier | ProbeName | ProbeType | ProbeReference | ProbeLocationName | ProbeLocationOntology | CellTypeName | CellTypeOntology |
| :--- | :--- | :--- | :--- | :--- | :--- | :--- | :--- | :--- |
| `412693bb0b2cf772_c0d06cadbb168eb5` | `412693bb0bf98cde_40ce5a2a60a82dd2` | bath_210401_BNSTIII_a | stimulator | [1] | | | | |
| `412693bb0b2cf772_c0d06cadbb168eb5` | `412693bb0bf99bbe_c0cb88b37570afba` | Vm_210401_BNSTIII_a | patch-Vm | [1] | bed nucleus of stria terminalis (BNST) | UBERON:0001880 | Type III BNST neuron | EMPTY:0000073 |

**Call:** `epochSummary = ndi.fun.docTable.epoch(session)` — *"this will take several minutes"*

**Preview (literal from tutorial — 12 columns):**

| EpochNumber | EpochDocumentIdentifier | ProbeDocumentIdentifier | SubjectDocumentIdentifier | local_t0 | local_t1 | global_t0 | global_t1 | MixtureName | MixtureOntology | ApproachName | ApproachOntology |
| :--- | :--- | :--- | :--- | :--- | :--- | :--- | :--- | :--- | :--- | :--- | :--- |
| 1 | `epoch_412693bb00b3b7b2_4087375d5b7ef613` | `412693bb0bf4b173_40d91734313482e2` | `412693bb0b2a75c8_c0dc4139300a673e` | 0 | 76.9805 | 18-Aug-2021 15:29:59 | 18-Aug-2021 15:31:16 | arginine-vasopressin | NCIm:C1098706 | | |

**Combined summary call (B1's closest literal analog):**
```matlab
combinedSummary = ndi.fun.table.join({subjectSummary, probeSummary, epochSummary}, ...
    'uniqueVariables','EpochDocumentIdentifier');
combinedSummary = ndi.fun.table.moveColumnsLeft(combinedSummary, ...
    {'SubjectLocalIdentifier','EpochNumber'})
```
This produces a 29-column wide "one row per epoch, all denormalized" table — the most complete shape a researcher ever looks at. For our purposes, this is **the** answer to "what does a dataset summary actually look like in the MATLAB workflow."

### 2.2 Filtering patterns (literal copy from tutorial, useful for B1/B6)

The tutorial teaches researchers **two filter moves**, both using `identifyMatchingRows`:

```matlab
% Filter subjects by strain
columnName = 'StrainName';          % e.g. 'AVP-Cre' or 'SD'
dataValue = 'AVP-Cre';
rowInd = ndi.fun.table.identifyMatchingRows(subjectSummary, ...
    columnName, dataValue, 'stringMatch','contains');
filteredSubjects = subjectSummary(rowInd,:)

% Filter epochs by multiple criteria
% Examples (literal from tutorial):
%   columnName='ApproachName',    dataValue='optogenetic',         stringMatch='contains'
%   columnName='MixtureName',     dataValue='FE201874',            stringMatch='contains'
%   columnName='CellTypeName',    dataValue='Type I BNST neuron',  stringMatch='identical'
%   columnName='global_t0',       dataValue='Jun-2023',            stringMatch='contains'
```

`stringMatch` takes `{'identical','ignoreCase','contains'}`; `numericMatch` takes `{'eq','ne','lt','le','gt','ge'}`.

### 2.3 Download / cite / export patterns (for B4)

**How researchers get data today (literal from every tutorial):**
```matlab
dataPath = [userpath filesep 'Datasets'];
cloudDatasetId = '67f723d574f5f79c6062389d';
datasetPath = fullfile(dataPath, cloudDatasetId);
if isfolder(datasetPath)
    dataset = ndi.dataset.dir(datasetPath);           % load from local
else
    dataset = ndi.cloud.downloadDataset(cloudDatasetId, dataPath);  % download whole thing
end
```
Note the pattern: the user's mental model is **"I download the dataset once, then work with it locally forever."** This is the v1 workflow we are explicitly moving *away* from — but it's what researchers expect.

**Citation format in tutorials:** hand-written in Markdown at the top of each dataset tutorial:
```
> Francesconi W, ... Dabrowska J (2025). Vasopressin and oxytocin excite BNST
> neurons via oxytocin receptors, which reduce anxious arousal.
> Cell Reports 44(6): 115768. DOI: 10.1016/j.celrep.2025.115768.
>
> Francesconi W, ... Dabrowska J (2025). Dataset: vasopressin and oxytocin
> excite BNST neurons via oxytocin receptors, which reduce anxious arousal.
> NDI Cloud. DOI: 10.63884/ndic.2025.jyxfer8m.
```
Two DOIs per dataset: one for the paper, one for the dataset. NDI-cloud mints dataset DOIs with prefix `10.63884/` (see `src/ndi/+ndi/+cloud/+admin/createNewDOI.m`: `doiSuffix = sprintf('ndic.%d.%s', year(datetime("now")), randomSuffix);`).

**Export pattern (literal from working.m):**
```matlab
exportPath = fullfile(userpath,'data','Dabrowska','subjectSummary_250912.xls');
writetable(cellCompound, exportPath);
```
Plain `writetable()` to XLS/CSV — no fancy machinery. Whatever B4 ships should match **XLS/CSV** as first-class export formats.

---

## 3. Canonical column set for B6a (sensible default columns)

The **ordered, literal default column set** that emerges from `ndi.fun.docTable.subject` on the flagship tutorial dataset (the one the user sees first):

1. `SubjectDocumentIdentifier` (always, guaranteed)
2. `SubjectLocalIdentifier` (always, guaranteed — this is the human-readable ID like `sd_rat_OTRCre_220819_175@dabrowska-lab.rosalindfranklin.edu`)
3. `StrainName`
4. `StrainOntology`
5. `BackgroundStrainName`
6. `BackgroundStrainOntology`
7. `GeneticStrainTypeName`
8. `SpeciesName`
9. `SpeciesOntology`
10. `BiologicalSexName`
11. `BiologicalSexOntology`
12. `{Treatment*}LocationName` (e.g. `OptogeneticTetanusStimulationTargetLocationName`, `DrugTreatmentLocationName`) — dynamic, one pair per treatment type found
13. `{Treatment*}LocationOntology` — dynamic

Note that `SessionIdentifier` is added first in code but is typically the leftmost column the function creates. It does NOT appear in the preview table in the tutorial, suggesting researchers use `moveColumnsLeft` or column selection to hide it.

**Missing from the canonical set but researchers often want (from the combined summary):** `ProbeName`, `ProbeType`, `CellTypeName`, `CellTypeOntology`, `MixtureName`, `ApproachName`. These live in `probeSummary` and `epochSummary` and only get folded in via `ndi.fun.table.join`. **This is a strong hint:** B6a's "sensible defaults" probably shouldn't be just subject columns — it should be the **combined** per-subject shape with probe and epoch counts (or an aggregated version).

**What researchers do NOT include by default:** `age`, `weight` — neither appears in any sample data. The `openminds`-backed vocabulary doesn't have first-class weight/age fields surfaced here; they may exist as `measurement` docs but they're not in the flagship tutorial's default view. If you include age/weight in B6a's defaults, researchers will notice, but it's not matching what MATLAB shows by default.

---

## 4. Plan B B1–B5 exists/partial/doesn't-exist matrix

**Note:** The subagent did not have a copy of the Plan B document. The analysis below interprets B1–B5 from the subagent prompt:
- B1 = experiment-summary synthesizer
- B2 = (not explicitly described; inferred as "search/filter within a dataset")
- B3 = output-shape preview
- B4 = cite/download/export
- B5 = (not explicitly described)
- B6a = sensible column defaults

### B1 (experiment-summary synthesizer) — **Exists as MATLAB pattern**

The exact deliverable shape exists. It is called `subjectSummary` (or the wider `combinedSummary`). It is **a table, not a sentence**. The one-line literal recipe:

```matlab
subjectSummary = ndi.fun.docTable.subject(dataset);
probeSummary = ndi.fun.docTable.probe(dataset);
epochSummary = ndi.fun.docTable.epoch(session);
combinedSummary = ndi.fun.table.join({subjectSummary, probeSummary, epochSummary}, ...
    'uniqueVariables', 'EpochDocumentIdentifier');
```

**To port to Python/TypeScript:**
- Hit the `POST /ndiquery` with `isa=subject` scope = dataset's sessions.
- For each subject doc, resolve `depends_on` to openminds docs typed `Strain`, `Species`, `BiologicalSex`, and to `treatment`/`treatment_drug`/`virus_injection`/`measurement` docs.
- Left-join them on `SubjectDocumentIdentifier`.
- Column naming convention: `{TypeName}Name` + `{TypeName}Ontology` for every openminds type that comes through.
- For treatments, the column name derives from `ndi.ontology.lookup(docProp.treatment.ontologyName)` — i.e. the data-type label.

The shape is **one row per subject, columns denormalized from its dependency chain**. The explicit doc even says "Metadata from associated documents is joined using 'SubjectDocumentIdentifier'."

### B2 (search/filter within a dataset) — **Exists as MATLAB pattern**

`ndi.fun.table.identifyMatchingRows` is the canonical filter primitive. See §2.2 for four literal example filters researchers type. The pattern is `(column, value, stringMatch/numericMatch)` — which maps cleanly to TanStack Table's column-filter model.

### B3 (output-shape preview) — **Exists as MATLAB pattern** (as literal tables in tutorials)

The tutorial files *show the exact preview tables*, row by row, as Markdown. Copy those literal tables verbatim into the v2 browser docs for B3. See §2.1 — use the Francesconi tutorial's preview rows. They're already beautifully concrete. Three canonical preview shapes: subject (13 cols), probe (9 cols), epoch (12 cols), combined (29 cols).

### B4 (cite/download/export) — **Partial**

- **Citation:** hand-typed Markdown in tutorials — there's no programmatic `ndi.fun.cite(dataset)` or similar. We'll need to construct a citation ourselves from the dataset metadata. DOI is mintable via `ndi.cloud.admin.createNewDOI` with prefix `10.63884/`.
- **Download:** `ndi.cloud.downloadDataset(cloudDatasetId, dataPath)` is the one-shot full download. v2 explicitly does NOT do bulk downloads (ADR 004). We should surface per-table CSV/XLS export instead.
- **Export:** researchers use MATLAB's built-in `writetable(T, path.xls)`. Match with a **CSV + XLS** export button on every summary table. `nansen_demo.m` and `working.m` show `.xls` as the default extension.

### B5 — **Cannot assess without Plan B document.** Flagging for PM to re-check.

### B6a (sensible column defaults) — **Exists as MATLAB pattern**

See §3 for the literal 13-column ordered list. Strong recommendation: make the default shape the **combined (subject + probe + epoch)** view, because the tutorial spends its time there, not in pure `subjectSummary`.

---

## 5. Vocabulary ground truth

From `src/ndi/docs/NDI-matlab/tutorials/ndimodel/2_ndimodel_vocabulary.md` and the Francesconi tutorial:

| Term | NDI-matlab definition | Implication for v2 |
|---|---|---|
| **dataset** | A published, DOI-bearing collection. Contains one or more sessions. The object is `ndi.dataset.dir`, loaded from a cloud download. A dataset *has* sessions. | This is our top-level unit; matches v2 architecture. |
| **session** | An experimental session. `ndi.session.dir`. You `dataset.open_session(session_list{1})` to get one. "A dataset can have multiple sessions, but this dataset has only one." | v2 should expose sessions as a middle tier — NOT flatten dataset→subject directly. When researchers think of a dataset they've seen before, they think in sessions. |
| **subject** | An individual experimental animal/thing. Has `SubjectDocumentIdentifier` (opaque hash) and `SubjectLocalIdentifier` (human-readable, like `sd_rat_OTRCre_220819_175@dabrowska-lab.rosalindfranklin.edu`). "a subject must be named and given an identifier in NDI." | Subjects cluster by SESSION, not dataset directly. The pattern `dataset → sessions → subjects → probes/epochs` is the ground truth tree. |
| **probe** | "an instrument that makes a measurement of or produces a stimulus for a subject." e.g. electrode, camera, stimulator. Has `ProbeDocumentIdentifier`, `ProbeName`, `ProbeType`, `ProbeReference`. | Probes are child of subject; `ProbeType` is a small controlled vocabulary. |
| **element** | A broader class — "experiment items" — that includes probes, *inferred* neurons, simulations, filtered signals. Probes ⊂ Elements. | Don't assume "probe count" alone; "element count" is the more complete count. |
| **epoch** | "an interval of time during which a DAQ system is switched on and then off to make a recording." Per-probe, per-recording. | This is the finest grain and what researchers end up filtering on. |
| **DAQ system** | The data acquisition hardware-or-logical-entity that stores probe measurements. | Likely not user-visible in v2. |

**Surprises worth flagging to the PM:**

1. **"Subject" in NDI-matlab has a REQUIRED structured local identifier** that carries semantic content: the Dabrowska dataset's `SubjectLocalIdentifier` is `{strain}_{species}_{genotype}_{date}_{region}@{lab-email}`. This is a user-facing string researchers parse visually. If v2 truncates this or replaces it with a hash, researchers will lose orientation fast. Preserve it and display it wide.

2. **There is NO "experiment" concept in NDI vocabulary.** Researchers talk about datasets, sessions, subjects, probes, epochs. "Experiment" is colloquial at best. If we say "experiment summary" in v2 UI copy, it will not match terminology. Consider "session summary" or "dataset summary" as the researcher-aligned label.

3. **`SessionIdentifier` in subject summary → ties subjects back to sessions, not datasets.** The canonical subject table's first column is a session id, because a dataset in NDI-matlab is conceptually a container-of-sessions. v2 must preserve this — do NOT flatten subjects directly to dataset.

4. **No prose summary exists anywhere.** Every "summary" in NDI-matlab is literally a MATLAB `table`. If B1 wants to produce a sentence like *"This dataset contains 7 subjects (Rattus norvegicus, male, 3 strains) recorded with 21 patch-Vm probes across 160 epochs"*, that is a **v2 invention**, not a port. That's fine — it may be a strictly better UX — but it's not "matching what researchers already know." What researchers know is the table.

5. **The combined summary is a 29-column-wide denormalized per-epoch table.** Researchers do not complain about width. They filter it. Any attempt in v2 to hide/reduce columns by default may hurt more than help; offer a power-user "show all columns" mode plus well-chosen defaults.

6. **`stringMatch: contains` is the researcher default.** The tutorial examples overwhelmingly use `contains` rather than `identical`. TanStack Table's column filter should default to substring matching, not exact.

7. **`CellTypeName` and `MixtureName` are CSV-joined inside a single table cell.** The MATLAB aggregation pattern is: when multiple values apply to one entity, pack them into a comma-joined string inside the cell. v2 will need to decide whether to preserve this (easy, matches expectations) or split into multi-valued arrays (harder to display, more correct). Strong recommendation: CSV strings on first render, optional split into chips on column config.

---

## 6. Where the report is saved

This report: `/tmp/ndb-reviews/spike0-C-ndi-matlab.md`

---

## 7. Files consulted (absolute paths for traceability)

Primary sources:
- `/Users/audribhowmick/Documents/ndi-projects/NDI-matlab/src/ndi/+ndi/+fun/+docTable/subject.m`
- `/Users/audribhowmick/Documents/ndi-projects/NDI-matlab/src/ndi/+ndi/+fun/+docTable/probe.m`
- `/Users/audribhowmick/Documents/ndi-projects/NDI-matlab/src/ndi/+ndi/+fun/+docTable/epoch.m`
- `/Users/audribhowmick/Documents/ndi-projects/NDI-matlab/src/ndi/+ndi/+fun/+docTable/element.m`
- `/Users/audribhowmick/Documents/ndi-projects/NDI-matlab/src/ndi/+ndi/+fun/+docTable/openminds.m`
- `/Users/audribhowmick/Documents/ndi-projects/NDI-matlab/src/ndi/+ndi/+fun/+docTable/treatment.m`
- `/Users/audribhowmick/Documents/ndi-projects/NDI-matlab/src/ndi/+ndi/+fun/+table/join.m`
- `/Users/audribhowmick/Documents/ndi-projects/NDI-matlab/src/ndi/+ndi/+fun/+table/identifyMatchingRows.m`
- `/Users/audribhowmick/Documents/ndi-projects/NDI-matlab/src/ndi/+ndi/+fun/+table/identifyValidRows.m`
- `/Users/audribhowmick/Documents/ndi-projects/NDI-matlab/src/ndi/+ndi/+fun/+table/moveColumnsLeft.m`
- `/Users/audribhowmick/Documents/ndi-projects/NDI-matlab/src/ndi/+ndi/+fun/+table/vstack.m`

Tutorials:
- `/Users/audribhowmick/Documents/ndi-projects/NDI-matlab/src/ndi/docs/NDI-matlab/tutorials/datasets/Francesconi_et_al_2025/1_getting_started.md` ← **the** canonical reference for B1/B3
- `/Users/audribhowmick/Documents/ndi-projects/NDI-matlab/src/ndi/docs/NDI-matlab/tutorials/ndimodel/1_intro.md`
- `/Users/audribhowmick/Documents/ndi-projects/NDI-matlab/src/ndi/docs/NDI-matlab/tutorials/ndimodel/2_ndimodel_vocabulary.md`
- `/Users/audribhowmick/Documents/ndi-projects/NDI-matlab/src/ndi/docs/NDI-matlab/tutorials/analyzing_first_physiology_experiment/1_example_dataset.md`

Real-world researcher workflow examples:
- `/Users/audribhowmick/Documents/ndi-projects/NDI-matlab/src/ndi/+ndi/+setup/+conv/+dabrowska/download_data.m`
- `/Users/audribhowmick/Documents/ndi-projects/NDI-matlab/src/ndi/+ndi/+setup/+conv/+dabrowska/working.m`
- `/Users/audribhowmick/Documents/ndi-projects/NDI-matlab/src/ndi/+ndi/+setup/+conv/+haley/nansen_demo.m`

Infrastructure:
- `/Users/audribhowmick/Documents/ndi-projects/NDI-matlab/src/ndi/+ndi/dataset.m`
- `/Users/audribhowmick/Documents/ndi-projects/NDI-matlab/src/ndi/+ndi/+cloud/+admin/createNewDOI.m`
