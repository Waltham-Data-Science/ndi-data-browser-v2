# openminds_subject fixtures

Committed 2026-04-16 as part of the M4a Day-1 openminds shape audit
(`docs/v0-v1-v2-audit.md`, plan `M4a` step 0). Gated by
`backend/tests/unit/test_openminds_shape.py`.

## Provenance

All files pulled live from `https://api.ndi-cloud.com/v1` via
`POST /ndiquery` + `POST /datasets/:id/documents/bulk-fetch`. Both live
test datasets are public; no authentication was used.

- Haley (C. elegans, WBStrain): `682e7772cdf3f24938176fac` — 9,032 openminds_subject docs
- Van Hooser (ferret V1): `68839b1fbf243809c0800a01` — 64 openminds_subject docs

## Headline finding: `openminds_subject` is a polymorphic class

Every doc has this outer shape:

```
{ id, ndiId, name: "", className: "", datasetId,
  data: { base, depends_on, document_class, openminds: {...}, openminds_subject: {} } }
```

But `data.openminds.fields` has **two different schemas** depending on
`data.openminds.openminds_type`. The projection code MUST dispatch by type.

### Schema A — controlled vocabulary types

Applies to `Species`, `BiologicalSex`, `GeneticStrainType` (and by inference
the other simple types we haven't observed like `AgeCategory`).

Field keys:
```
name, preferredOntologyIdentifier, synonym,
definition, description, interlexIdentifier, knowledgeSpaceLink
```

Ontology ID lives in **`preferredOntologyIdentifier`**. Observed encodings
for "no ontology ID":
- Empty string `""` (Haley GeneticStrainType)
- **Unprefixed bare value** like `"9669"` for NCBI taxon — still a valid
  ID but missing its `NCBITaxon:` prefix. Van Hooser species.
- Absent entirely (not observed in fixtures but the Strain case proves keys
  can be absent, so projection must tolerate `.get()` returning None).

### Schema B — compound types

Applies to `Strain`.

Field keys:
```
name, ontologyIdentifier, synonym,
alternateIdentifier, backgroundStrain, breedingType, description,
digitalIdentifier, diseaseModel, geneticStrainType, laboratoryCode,
phenotype, species, stockNumber
```

**Ontology ID lives in `ontologyIdentifier`**, NOT `preferredOntologyIdentifier`.
This is the biggest landmine: v1's projection code assumes one ontology
key name everywhere and will emit null for Strain ontology IDs (which are
the WBStrain IDs the Haley tutorial centers around).

The nested-reference fields (`species`, `geneticStrainType`, `backgroundStrain`)
are list-valued ndi:// URIs pointing to OTHER openminds_subject docs for
the same subject. Those companion docs are also reachable via the
`depends_on -> subject_id` index, so the existing enrichment join recovers
them without a second hop — but for `backgroundStrain` specifically, we
have no observed non-empty case in either live dataset to confirm.

## Type coverage in the two live test datasets

| openminds_type    | Schema | Haley | Van Hooser |
|-------------------|--------|-------|------------|
| Species           | A      | yes (`NCBITaxon:6239`)            | yes (bare `9669`) |
| BiologicalSex     | A      | yes (`PATO:0001340`)              | yes (`PATO:0000383`) |
| GeneticStrainType | A      | yes (empty string `""`)           | absent |
| Strain            | B      | yes (`ontologyIdentifier=WBStrain:00000001`) | absent |
| BackgroundStrain  | n/a    | not observed as its own type (likely a reference-role on Strain only) | absent |

Van Hooser's openminds_subject set is lean (64 docs: 32 Species + 32 BiologicalSex).
Haley's set is rich (9032 docs spanning all four observed types).

## depends_on shape per schema

Schema A docs: exactly one non-empty edge pointing to the parent subject
(`{name: "subject_id", value: <parent ndiId>}`) plus one empty marker
(`{name: "openminds", value: ""}`). v2's `_depends_on_values()` already
filters the empty-value entry.

Schema B (Strain) docs: the parent-subject edge PLUS numbered cross-
reference edges (`{name: "openminds_1", value: <ndiId of a companion>}`,
`{name: "openminds_2", ...}`) that point to other openminds_subject docs
for the same subject. The numbering is generic and carries no role info —
use `fields.species` / `fields.geneticStrainType` / `fields.backgroundStrain`
to recover roles.

## Canonical projection contract (authoritative)

This is the contract `backend/services/summary_table_service.py::_openminds_name_and_ontology`
MUST honor. `test_openminds_shape.py` locks these invariants; anything the
projection relies on beyond what's in this contract is a bug in the test suite,
not a license to the projection code.

### Dispatch by openminds type

```
_openminds_name_and_ontology(doc, type_suffix) -> (name, ontology_id)
```

Where `type_suffix` is the terminal segment of `data.openminds.openminds_type`
(e.g. `"Species"`, `"Strain"`).

| type_suffix (observed)                                  | ontology key                       |
|---------------------------------------------------------|------------------------------------|
| Species, BiologicalSex, GeneticStrainType (Schema A)    | `fields.preferredOntologyIdentifier` |
| Strain (Schema B)                                       | `fields.ontologyIdentifier`        |
| BackgroundStrain (reference role on a Strain doc)       | *follow ref — see below*           |

For both schemas: `name` is always in `fields.name`. Treat any of `""`, `null`,
or missing-key as "no ontology ID available" and return `None`.

### BackgroundStrain resolution

`BackgroundStrain` is NOT its own openminds_type in the live data. The v1
subject-table columns `backgroundStrainName` / `backgroundStrainOntology` must
be resolved via:

1. Find the subject's Strain companion doc (openminds_type = `...Strain`, already
   in the enrichment list via the `subject_id` depends_on join).
2. Read `fields.backgroundStrain` — a list of `ndi://<ndiId>` URI strings.
3. For each ndi:// ref, look up the sibling openminds_subject doc by its ndiId
   in the enrichment set. Those companion docs are already fetched because
   Strain's `depends_on` back-references them via `openminds_N` edges, but the
   role mapping lives in `fields.backgroundStrain` not in depends_on names.
4. Read the referenced doc's `name` + `fields.ontologyIdentifier` (the ref
   points to another Strain-schema doc).

Observed in Haley/VH: all strain `backgroundStrain` arrays are empty. Projection
must still emit the columns (SummaryTableView's auto-hide-empty-column handles
display). When Dabrowska publishes, those columns will populate with
`BackgroundStrainName=SD` + `BackgroundStrainOntology=RRID:RGD_70508`.

### Value normalization

- **Do NOT normalize unprefixed ontology IDs at the projection layer.** Pass
  `"9669"` through to the frontend as-is. The ontology popover (frontend
  `ontology-utils.isOntologyTerm()`) is the single point of truth for deciding
  whether a bare numeric ID should be treated as a `NCBITaxon:<id>` lookup.
- Treat `data.openminds_subject` as always empty; never read from it.
- `openminds_type` is a full URI — keep `endswith(suffix)` matching (current
  v2 code is correct here).

### Tolerant `.get()` chains

Schema A and B do NOT share their non-overlapping keys. A helper reading
`fields["ontologyIdentifier"]` on a Species doc will KeyError. Every read
must go through `fields.get("ontologyIdentifier")` / `fields.get("name")`
and treat missing and empty as equivalent.
