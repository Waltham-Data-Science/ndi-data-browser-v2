"""Shape contract for `openminds_subject` documents.

Gates the v2 summary-table projection against the live cloud doc shape
discovered during the M4a Day-1 audit (2026-04-16). If the cloud adds,
renames, or drops fields on this class, this suite fails and forces a
review of `backend/services/summary_table_service.py::_openminds_*`
before the projection silently emits wrong data.

Fixtures live at `backend/tests/fixtures/openminds/` and were pulled
directly from the prod cloud via ndiquery + bulk-fetch for Haley
(682e7772cdf3f24938176fac) and Van Hooser (68839b1fbf243809c0800a01).
See the sibling `README.md` for data-variance notes.

Key finding from the audit: `openminds_subject` is a polymorphic class.
Controlled-vocabulary types (Species, BiologicalSex, GeneticStrainType)
use schema A with `preferredOntologyIdentifier`. Strain uses schema B
with `ontologyIdentifier` (different key) plus nested reference fields.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

FIXTURE_DIR = Path(__file__).parent.parent / "fixtures" / "openminds"

HALEY_DATASET = "682e7772cdf3f24938176fac"
VANHOOSER_DATASET = "68839b1fbf243809c0800a01"

# Schema A: controlled-vocab types. Ontology ID in `preferredOntologyIdentifier`.
SCHEMA_A_TYPES = {"Species", "BiologicalSex", "GeneticStrainType"}
SCHEMA_A_FIELD_KEYS = {
    "name",
    "preferredOntologyIdentifier",
    "synonym",
    "definition",
    "description",
    "interlexIdentifier",
    "knowledgeSpaceLink",
}

# Schema B: compound type (Strain). Ontology ID in `ontologyIdentifier` (different key).
# Additional nested fields carry ndi:// references to OTHER openminds_subject docs
# for the same subject (species, geneticStrainType, backgroundStrain).
SCHEMA_B_TYPES = {"Strain"}
SCHEMA_B_FIELD_KEYS = {
    "name",
    "ontologyIdentifier",
    "synonym",
    "alternateIdentifier",
    "backgroundStrain",
    "breedingType",
    "description",
    "digitalIdentifier",
    "diseaseModel",
    "geneticStrainType",
    "laboratoryCode",
    "phenotype",
    "species",
    "stockNumber",
}

# Fixture file → (dataset_id, expected openminds type suffix, expected name,
# expected ontology value — PRESENT_EMPTY if the ontology key is present but "",
# KEY_ABSENT if the key is missing entirely).
PRESENT_EMPTY = object()
KEY_ABSENT = object()

FIXTURES: dict[str, tuple[str, str, str, object]] = {
    "haley_openminds_species.json":
        (HALEY_DATASET, "Species", "Caenorhabditis elegans", "NCBITaxon:6239"),
    "haley_openminds_strain.json":
        (HALEY_DATASET, "Strain", "N2", "WBStrain:00000001"),
    "haley_openminds_biologicalsex.json":
        (HALEY_DATASET, "BiologicalSex", "hermaphrodite", "PATO:0001340"),
    "haley_openminds_geneticstraintype.json":
        (HALEY_DATASET, "GeneticStrainType", "transgenic", PRESENT_EMPTY),
    "vanhooser_openminds_species.json":
        (VANHOOSER_DATASET, "Species", "Mustela putorius furo", "9669"),
    "vanhooser_openminds_biologicalsex.json":
        (VANHOOSER_DATASET, "BiologicalSex", "female", "PATO:0000383"),
    "vanhooser_openminds_species_alt.json":
        (VANHOOSER_DATASET, "Species", "Mustela putorius furo", "9669"),
}

REQUIRED_TOP_KEYS = {"id", "ndiId", "name", "className", "datasetId", "data"}
REQUIRED_DATA_KEYS = {"base", "depends_on", "document_class", "openminds", "openminds_subject"}
REQUIRED_OPENMINDS_KEYS = {"openminds_type", "fields"}


def _load(name: str) -> dict:
    return json.loads((FIXTURE_DIR / name).read_text())


def _ontology_key_for(type_suffix: str) -> str:
    if type_suffix in SCHEMA_B_TYPES:
        return "ontologyIdentifier"
    return "preferredOntologyIdentifier"


@pytest.mark.parametrize("fixture_name", list(FIXTURES))
def test_fixture_has_required_top_level_shape(fixture_name: str) -> None:
    doc = _load(fixture_name)
    assert REQUIRED_TOP_KEYS.issubset(doc.keys()), (
        f"{fixture_name}: missing top-level keys {REQUIRED_TOP_KEYS - doc.keys()}"
    )
    dataset_id, _, _, _ = FIXTURES[fixture_name]
    assert doc["datasetId"] == dataset_id


@pytest.mark.parametrize("fixture_name", list(FIXTURES))
def test_fixture_has_required_data_shape(fixture_name: str) -> None:
    doc = _load(fixture_name)
    data = doc["data"]
    assert REQUIRED_DATA_KEYS.issubset(data.keys()), (
        f"{fixture_name}: missing data keys {REQUIRED_DATA_KEYS - data.keys()}"
    )
    assert data["document_class"]["class_name"] == "openminds_subject"


@pytest.mark.parametrize("fixture_name", list(FIXTURES))
def test_fixture_openminds_fields_match_type_schema(fixture_name: str) -> None:
    """Schema A (Species/BiologicalSex/GeneticStrainType) vs Schema B (Strain)
    have DIFFERENT field-key sets. The projection code must dispatch by type.
    This test locks in the set of required keys per schema so silent cloud-side
    schema drift is caught.
    """
    doc = _load(fixture_name)
    _, type_suffix, _, _ = FIXTURES[fixture_name]
    om = doc["data"]["openminds"]
    assert REQUIRED_OPENMINDS_KEYS.issubset(om.keys())
    fields = om["fields"]
    if type_suffix in SCHEMA_A_TYPES:
        assert SCHEMA_A_FIELD_KEYS.issubset(fields.keys()), (
            f"{fixture_name} [{type_suffix}]: missing Schema A keys "
            f"{SCHEMA_A_FIELD_KEYS - fields.keys()}"
        )
        assert "ontologyIdentifier" not in fields, (
            f"{fixture_name}: Schema A type unexpectedly has ontologyIdentifier key"
        )
    elif type_suffix in SCHEMA_B_TYPES:
        assert SCHEMA_B_FIELD_KEYS.issubset(fields.keys()), (
            f"{fixture_name} [{type_suffix}]: missing Schema B keys "
            f"{SCHEMA_B_FIELD_KEYS - fields.keys()}"
        )
        assert "preferredOntologyIdentifier" not in fields, (
            f"{fixture_name}: Schema B type unexpectedly has preferredOntologyIdentifier key"
        )
    else:
        pytest.fail(f"{fixture_name}: unhandled openminds_type {type_suffix!r}")


@pytest.mark.parametrize("fixture_name", list(FIXTURES))
def test_fixture_openminds_type_is_fully_qualified_uri(fixture_name: str) -> None:
    """`openminds_type` is a URI like `https://openminds.om-i.org/types/Species`.
    The projection code uses `endswith(suffix)` matching, which depends on the
    terminal segment being stable.
    """
    doc = _load(fixture_name)
    _, expected_suffix, _, _ = FIXTURES[fixture_name]
    om_type = doc["data"]["openminds"]["openminds_type"]
    assert om_type.startswith("https://openminds.om-i.org/types/"), (
        f"{fixture_name}: openminds_type URI changed shape: {om_type!r}"
    )
    assert om_type.endswith(expected_suffix), (
        f"{fixture_name}: expected suffix {expected_suffix!r}, got {om_type!r}"
    )


@pytest.mark.parametrize("fixture_name", list(FIXTURES))
def test_fixture_name_and_ontology_match_expected(fixture_name: str) -> None:
    """Locks in the exact (name, ontology-id) pairing the projection code will
    emit per-type. Catches cloud-side data drift that would silently break the
    tutorial-parity table.
    """
    doc = _load(fixture_name)
    _, type_suffix, expected_name, expected_pref = FIXTURES[fixture_name]
    fields = doc["data"]["openminds"]["fields"]
    assert fields["name"] == expected_name
    key = _ontology_key_for(type_suffix)
    if expected_pref is KEY_ABSENT:
        assert key not in fields, (
            f"{fixture_name}: expected {key} to be absent, but got {fields.get(key)!r}"
        )
    elif expected_pref is PRESENT_EMPTY:
        assert fields.get(key) in ("", None), (
            f"{fixture_name}: expected empty/null {key}, got {fields.get(key)!r}"
        )
    else:
        assert fields.get(key) == expected_pref


def test_strain_has_nested_reference_lists() -> None:
    """Strain's fields include list-valued references to other openminds_subject
    docs (species, geneticStrainType, backgroundStrain) encoded as `ndi://<ndiId>`
    URIs. Those references point to companion docs that ALSO depend directly on
    the parent subject — so they're reachable by the existing subject_id
    enrichment join without a second hop. The test pins that encoding so any
    future change to the reference format is flagged.
    """
    strain = _load("haley_openminds_strain.json")
    fields = strain["data"]["openminds"]["fields"]
    for key in ("species", "geneticStrainType", "backgroundStrain"):
        assert key in fields, f"Strain missing nested-reference field {key}"
        assert isinstance(fields[key], list), f"Strain.{key} not a list"
    # For N2 the observed non-empty references follow `ndi://<ndiId>` shape.
    for ref in fields["species"]:
        assert isinstance(ref, str) and ref.startswith("ndi://"), (
            f"species reference not ndi:// URI: {ref!r}"
        )
    for ref in fields["geneticStrainType"]:
        assert isinstance(ref, str) and ref.startswith("ndi://"), (
            f"geneticStrainType reference not ndi:// URI: {ref!r}"
        )


def test_depends_on_shape_for_schema_a_types() -> None:
    """Schema A docs observed with depends_on = [{name:'subject_id', value:<ndi>},
    {name:'openminds', value:''}]. The second entry is an empty categorization
    marker that v2's `_depends_on_values` already filters. Schema A docs have
    exactly ONE non-empty depends_on value — the parent subject's ndiId.
    """
    for fname, (_, type_suffix, *_) in FIXTURES.items():
        if type_suffix not in SCHEMA_A_TYPES:
            continue
        doc = _load(fname)
        deps = doc["data"]["depends_on"]
        assert isinstance(deps, list) and deps, f"{fname}: depends_on empty/not a list"
        for d in deps:
            assert set(d.keys()) == {"name", "value"}, f"{fname}: depends_on shape drift: {d}"
        non_empty = [d["value"] for d in deps if d.get("value")]
        assert len(non_empty) == 1, (
            f"{fname} [{type_suffix}]: expected exactly one non-empty depends_on value, "
            f"got {non_empty}"
        )


def test_depends_on_shape_for_strain_has_cross_references() -> None:
    """Strain docs have MORE than one non-empty depends_on value: subject_id +
    `openminds_N` entries that cross-reference the other openminds_subject docs
    for the same subject (species, geneticStrainType). Any depends_on walker
    that assumes a single edge per doc will break on Strain.
    """
    strain = _load("haley_openminds_strain.json")
    deps = strain["data"]["depends_on"]
    names = [d.get("name") for d in deps]
    assert "subject_id" in names, "Strain depends_on missing subject_id edge"
    openminds_edges = [d for d in deps if d["name"].startswith("openminds_")]
    assert len(openminds_edges) >= 1, (
        f"Strain depends_on missing cross-reference edges (openminds_N): {deps}"
    )
    for d in openminds_edges:
        assert d["value"], f"openminds_N edge has empty value: {d}"


def test_preferred_ontology_identifier_value_variance() -> None:
    """Three observed encodings for 'no ontology ID available' on Schema A types:
    present + empty string, absent (key missing), and bare-unprefixed (still a
    valid ID that the lookup code must normalize). Projection + popover code
    must handle all three.
    """
    # Empty string (GeneticStrainType in Haley)
    gst = _load("haley_openminds_geneticstraintype.json")
    assert gst["data"]["openminds"]["fields"]["preferredOntologyIdentifier"] == ""

    # Bare unprefixed numeric (Species in Van Hooser) — a valid NCBI taxon ID
    # without the required `NCBITaxon:` prefix. Still needs lookup with
    # normalization.
    vh = _load("vanhooser_openminds_species.json")
    pref = vh["data"]["openminds"]["fields"]["preferredOntologyIdentifier"]
    assert pref == "9669"
    assert ":" not in pref, "Van Hooser species ontology unexpectedly gained a prefix"


def test_fixture_matrix_covers_both_live_datasets() -> None:
    datasets = {meta[0] for meta in FIXTURES.values()}
    assert datasets == {HALEY_DATASET, VANHOOSER_DATASET}


def test_fixture_matrix_covers_both_schemas() -> None:
    types = {meta[1] for meta in FIXTURES.values()}
    assert types & SCHEMA_A_TYPES, "No Schema A fixture"
    assert types & SCHEMA_B_TYPES, "No Schema B (Strain) fixture"


def test_all_fixture_files_are_registered() -> None:
    on_disk = {p.name for p in FIXTURE_DIR.glob("*.json")}
    assert on_disk == set(FIXTURES), (
        f"fixture file/FIXTURES mapping drift: on-disk={on_disk}, registered={set(FIXTURES)}"
    )
