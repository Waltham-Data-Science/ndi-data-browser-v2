"""Projection of cloud document shapes into summary-table rows.

Exercises `summary_table_service._row_*` and their helpers against the live-
cloud fixtures in `backend/tests/fixtures/openminds/`. The shape of those
fixtures is pinned by `test_openminds_shape.py` — if fixture shape drifts,
that test fails first; this suite then exercises the projection contract.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from backend.services.summary_table_service import (
    SUBJECT_COLUMNS,
    _attach_openminds_enrichment,
    _background_strain_from_strain,
    _clean,
    _clock_indices,
    _depends_on_value_by_name,
    _depends_on_values,
    _element_subject_ndi,
    _epoch_element_ndi,
    _index_by_ndi_id,
    _ndi_id,
    _normalize_t0_t1,
    _openminds_age_at_recording,
    _openminds_name_and_ontology,
    _openminds_ontology_key_for,
    _openminds_type_suffix,
    _parse_epoch_clock,
    _probe_location_split,
    _probe_locations_for,
    _project_for_class,
    _resolve_ndi_ref,
    _row_epoch,
    _row_probe,
    _row_subject,
    _row_treatment,
    _treatment_by_ontology_prefix,
    _treatments_for_subject,
)

FIXTURE_DIR = Path(__file__).parent.parent / "fixtures" / "openminds"


def _load_fixture(name: str) -> dict:
    return json.loads((FIXTURE_DIR / name).read_text())


HALEY_SPECIES    = _load_fixture("haley_openminds_species.json")
HALEY_STRAIN     = _load_fixture("haley_openminds_strain.json")
HALEY_SEX        = _load_fixture("haley_openminds_biologicalsex.json")
HALEY_GST        = _load_fixture("haley_openminds_geneticstraintype.json")
VH_SPECIES       = _load_fixture("vanhooser_openminds_species.json")
VH_SEX           = _load_fixture("vanhooser_openminds_biologicalsex.json")


# ---------------------------------------------------------------------------
# Primitive helpers
# ---------------------------------------------------------------------------

class TestClean:
    def test_none(self) -> None:
        assert _clean(None) is None

    def test_empty_string(self) -> None:
        assert _clean("") is None
        assert _clean("   ") is None

    def test_string(self) -> None:
        assert _clean("  hello ") == "hello"

    def test_passthrough_non_string(self) -> None:
        assert _clean(0) == 0
        assert _clean(False) is False
        assert _clean([]) == []


class TestDependsOn:
    def test_dict_shape(self) -> None:
        """element_epoch uses dict depends_on."""
        d = {"data": {"depends_on": {"name": "element_id", "value": "abc"}}}
        assert _depends_on_values(d) == ["abc"]
        assert _depends_on_value_by_name(d, "element_id") == "abc"
        assert _depends_on_value_by_name(d, "missing") is None

    def test_list_shape(self) -> None:
        """subject/element/treatment use list depends_on."""
        d = {"data": {"depends_on": [
            {"name": "subject_id", "value": "SUBJ"},
            {"name": "openminds", "value": ""},  # empty marker is filtered
        ]}}
        assert _depends_on_values(d) == ["SUBJ"]
        assert _depends_on_value_by_name(d, "subject_id") == "SUBJ"
        assert _depends_on_value_by_name(d, "openminds") is None

    def test_empty_cases(self) -> None:
        assert _depends_on_values({}) == []
        assert _depends_on_values(None) == []
        assert _depends_on_values({"data": {}}) == []


# ---------------------------------------------------------------------------
# Openminds dispatch helpers
# ---------------------------------------------------------------------------

class TestOpenmindsDispatch:
    def test_type_suffix_extracts_uri_tail(self) -> None:
        assert _openminds_type_suffix(HALEY_SPECIES) == "Species"
        assert _openminds_type_suffix(HALEY_STRAIN) == "Strain"
        assert _openminds_type_suffix(HALEY_SEX) == "BiologicalSex"
        assert _openminds_type_suffix(HALEY_GST) == "GeneticStrainType"

    def test_ontology_key_for_strain_uses_ontology_identifier(self) -> None:
        assert _openminds_ontology_key_for("Strain") == "ontologyIdentifier"

    def test_ontology_key_for_schema_a_uses_preferred_identifier(self) -> None:
        for t in ("Species", "BiologicalSex", "GeneticStrainType", "AgeCategory"):
            assert _openminds_ontology_key_for(t) == "preferredOntologyIdentifier"

    def test_dispatch_reads_schema_a_key(self) -> None:
        subject = {"_enriched_openminds": [HALEY_SPECIES]}
        assert _openminds_name_and_ontology(subject, "Species") == (
            "Caenorhabditis elegans", "NCBITaxon:6239",
        )

    def test_dispatch_reads_schema_b_key(self) -> None:
        subject = {"_enriched_openminds": [HALEY_STRAIN]}
        # Critical: reading `preferredOntologyIdentifier` on a Strain doc
        # would silently return None. Dispatch MUST pick `ontologyIdentifier`.
        assert _openminds_name_and_ontology(subject, "Strain") == (
            "N2", "WBStrain:00000001",
        )

    def test_no_match_returns_none_pair(self) -> None:
        subject = {"_enriched_openminds": [HALEY_SPECIES]}
        assert _openminds_name_and_ontology(subject, "Strain") == (None, None)

    def test_empty_ontology_normalizes_to_none(self) -> None:
        """GeneticStrainType has preferredOntologyIdentifier='' in Haley."""
        subject = {"_enriched_openminds": [HALEY_GST]}
        name, ontology = _openminds_name_and_ontology(subject, "GeneticStrainType")
        assert name == "transgenic"
        assert ontology is None  # "" cleaned to None

    def test_unprefixed_ontology_passed_through(self) -> None:
        """Van Hooser species emits bare NCBI taxon '9669'. Projection must
        pass it through unchanged so the frontend popover can normalize.
        """
        subject = {"_enriched_openminds": [VH_SPECIES]}
        name, ontology = _openminds_name_and_ontology(subject, "Species")
        assert name == "Mustela putorius furo"
        assert ontology == "9669"
        assert ":" not in ontology


# ---------------------------------------------------------------------------
# Background strain reference resolution (Schema B nested refs)
# ---------------------------------------------------------------------------

class TestBackgroundStrain:
    def test_empty_background_list_returns_none_pair(self) -> None:
        """Haley N2 has backgroundStrain=[]."""
        subject = {"_enriched_openminds": [HALEY_STRAIN]}
        assert _background_strain_from_strain(subject) == (None, None)

    def test_resolves_ndi_ref_to_sibling_strain_doc(self) -> None:
        """Synthetic: a Strain doc whose backgroundStrain points to another
        Strain-schema companion in the enrichment set."""
        background = {
            "ndiId": "BGX",
            "data": {
                "base": {"id": "BGX"},
                "openminds": {
                    "openminds_type": "https://openminds.om-i.org/types/Strain",
                    "fields": {"name": "SD", "ontologyIdentifier": "RRID:RGD_70508"},
                },
            },
        }
        strain = {
            "ndiId": "STX",
            "data": {
                "base": {"id": "STX"},
                "openminds": {
                    "openminds_type": "https://openminds.om-i.org/types/Strain",
                    "fields": {
                        "name": "TransgenicX",
                        "ontologyIdentifier": "WBStrain:00099999",
                        "backgroundStrain": ["ndi://BGX"],
                    },
                },
            },
        }
        subject = {"_enriched_openminds": [strain, background]}
        assert _background_strain_from_strain(subject) == ("SD", "RRID:RGD_70508")

    def test_unresolvable_ref_returns_none_pair(self) -> None:
        """If the ndi:// ref points to a doc not in the enrichment set, return None
        (plus log — projection must not raise)."""
        strain = {
            "data": {
                "base": {"id": "STX"},
                "openminds": {
                    "openminds_type": "https://openminds.om-i.org/types/Strain",
                    "fields": {
                        "name": "Orphan",
                        "backgroundStrain": ["ndi://UNKNOWN"],
                    },
                },
            },
        }
        subject = {"_enriched_openminds": [strain]}
        assert _background_strain_from_strain(subject) == (None, None)

    def test_resolve_ndi_ref_rejects_non_ndi_scheme(self) -> None:
        assert _resolve_ndi_ref({"X": {}}, "http://X") is None
        assert _resolve_ndi_ref({"X": {}}, "X") is None
        assert _resolve_ndi_ref({"X": {"a": 1}}, "ndi://X") == {"a": 1}


# ---------------------------------------------------------------------------
# Attach enrichment
# ---------------------------------------------------------------------------

class TestAttachOpenmindsEnrichment:
    def test_indexes_companions_by_subject_id(self) -> None:
        subj = {"data": {"base": {"id": "SUBJ1"}}}
        om_for_subj = {
            "data": {
                "depends_on": [{"name": "subject_id", "value": "SUBJ1"}],
                "openminds": {
                    "openminds_type": "https://openminds.om-i.org/types/Species",
                    "fields": {"name": "ferret"},
                },
            },
        }
        om_for_other = {
            "data": {
                "depends_on": [{"name": "subject_id", "value": "OTHER"}],
                "openminds": {"openminds_type": "...Species", "fields": {"name": "mouse"}},
            },
        }
        _attach_openminds_enrichment([subj], [om_for_subj, om_for_other])
        assert subj["_enriched_openminds"] == [om_for_subj]

    def test_subject_without_any_companions_gets_empty_list(self) -> None:
        subj = {"data": {"base": {"id": "SUBJ1"}}}
        _attach_openminds_enrichment([subj], [])
        assert subj["_enriched_openminds"] == []


# ---------------------------------------------------------------------------
# _row_subject end-to-end with real fixtures
# ---------------------------------------------------------------------------

def _subject_with_companions(
    base_id: str, local_id: str, session_id: str,
    companions: list[dict],
) -> dict:
    """Build a minimal subject doc + attach enrichment manually for row tests."""
    subject = {
        "id": "mongo-" + base_id,
        "ndiId": base_id,
        "data": {
            "base": {"id": base_id, "session_id": session_id, "name": ""},
            "subject": {"local_identifier": local_id, "description": ""},
            "document_class": {"class_name": "subject"},
        },
    }
    subject["_enriched_openminds"] = companions
    return subject


class TestRowSubjectFullShape:
    def test_subject_columns_cover_tutorial_15(self) -> None:
        keys = {c["key"] for c in SUBJECT_COLUMNS}
        expected = {
            "subjectIdentifier", "subjectLocalIdentifier", "subjectDocumentIdentifier",
            "sessionDocumentIdentifier", "strainName", "strainOntology",
            "geneticStrainTypeName", "speciesName", "speciesOntology",
            "backgroundStrainName", "backgroundStrainOntology",
            "biologicalSexName", "biologicalSexOntology",
            "ageAtRecording", "description",
        }
        assert keys == expected
        assert len(SUBJECT_COLUMNS) == 15

    def test_haley_full_subject_row(self) -> None:
        """Subject with C. elegans / N2 / transgenic / hermaphrodite.
        Regression catch: strainOntology must be WBStrain:00000001 not None.
        """
        subject = _subject_with_companions(
            "SUBJ_HALEY", "PR811_4144@chalasani-lab.salk.edu", "SESSION_HALEY",
            [HALEY_SPECIES, HALEY_STRAIN, HALEY_SEX, HALEY_GST],
        )
        row = _row_subject(subject)
        assert row["subjectIdentifier"] == "PR811_4144@chalasani-lab.salk.edu"
        assert row["subjectLocalIdentifier"] == "PR811_4144@chalasani-lab.salk.edu"
        assert row["subjectDocumentIdentifier"] == "SUBJ_HALEY"
        assert row["sessionDocumentIdentifier"] == "SESSION_HALEY"
        assert row["speciesName"] == "Caenorhabditis elegans"
        assert row["speciesOntology"] == "NCBITaxon:6239"
        assert row["strainName"] == "N2"
        assert row["strainOntology"] == "WBStrain:00000001"
        assert row["geneticStrainTypeName"] == "transgenic"
        assert row["biologicalSexName"] == "hermaphrodite"
        assert row["biologicalSexOntology"] == "PATO:0001340"
        # Haley N2 has empty backgroundStrain and no Age companion.
        assert row["backgroundStrainName"] is None
        assert row["backgroundStrainOntology"] is None
        assert row["ageAtRecording"] is None
        assert row["description"] is None

    def test_vanhooser_subject_keeps_unprefixed_species_ontology(self) -> None:
        subject = _subject_with_companions(
            "SUBJ_VH", "ferret_395.1664@vhlab.org", "SESSION_VH",
            [VH_SPECIES, VH_SEX],
        )
        row = _row_subject(subject)
        assert row["speciesName"] == "Mustela putorius furo"
        assert row["speciesOntology"] == "9669"  # unprefixed, not normalized
        assert row["biologicalSexName"] == "female"
        assert row["biologicalSexOntology"] == "PATO:0000383"
        # VH has no Strain/GeneticStrainType companion docs
        assert row["strainName"] is None
        assert row["strainOntology"] is None
        assert row["geneticStrainTypeName"] is None

    def test_row_subject_tolerates_no_enrichment(self) -> None:
        subject = _subject_with_companions("s", "worm-42", "sess-x", [])
        row = _row_subject(subject)
        assert row["subjectIdentifier"] == "worm-42"
        for k in (
            "speciesName", "speciesOntology", "strainName", "strainOntology",
            "biologicalSexName", "biologicalSexOntology", "geneticStrainTypeName",
            "backgroundStrainName", "backgroundStrainOntology", "ageAtRecording",
            "description",
        ):
            assert row[k] is None, f"{k} should be None when no enrichment present"

    def test_row_subject_returns_exactly_subject_columns(self) -> None:
        subject = _subject_with_companions("s", "worm-1", "sess", [HALEY_SPECIES])
        row = _row_subject(subject)
        assert set(row.keys()) == {c["key"] for c in SUBJECT_COLUMNS}


# ---------------------------------------------------------------------------
# t0_t1 normalization
# ---------------------------------------------------------------------------

class TestT0T1Normalization:
    def test_parse_epoch_clock_csv(self) -> None:
        assert _parse_epoch_clock("dev_local_time,exp_global_time") == [
            "dev_local_time", "exp_global_time",
        ]
        assert _parse_epoch_clock("dev_local_time") == ["dev_local_time"]
        assert _parse_epoch_clock("") == []
        assert _parse_epoch_clock(None) == []

    def test_clock_indices_finds_dev_and_global(self) -> None:
        dev, glb = _clock_indices(["dev_local_time", "exp_global_time"])
        assert dev == 0 and glb == 1
        dev, glb = _clock_indices(["exp_global_time", "dev_local_time"])
        assert dev == 1 and glb == 0
        dev, glb = _clock_indices(["dev_local_time"])
        assert dev == 0 and glb is None

    def test_haley_dual_clock_nested(self) -> None:
        """Haley: epoch_clock='dev_local_time,exp_global_time' + nested t0_t1.
        Expect {devTime, globalTime} objects for both start and stop.
        """
        doc = {"data": {"element_epoch": {
            "epoch_clock": "dev_local_time,exp_global_time",
            "t0_t1": [[0.0, 739256.7062152778], [3599.449248, 739256.74787557]],
        }}}
        start, stop = _normalize_t0_t1(doc)
        assert start == {"devTime": 0.0, "globalTime": 739256.7062152778}
        assert stop == {"devTime": 3599.449248, "globalTime": 739256.74787557}

    def test_vh_scalar_flat(self) -> None:
        """VH: epoch_clock='dev_local_time' + flat t0_t1.
        Expect globalTime=None.
        """
        doc = {"data": {"element_epoch": {
            "epoch_clock": "dev_local_time",
            "t0_t1": [0, 545.43595],
        }}}
        start, stop = _normalize_t0_t1(doc)
        assert start == {"devTime": 0, "globalTime": None}
        assert stop == {"devTime": 545.43595, "globalTime": None}

    def test_missing_t0_t1_returns_none_pair(self) -> None:
        assert _normalize_t0_t1({"data": {}}) == (None, None)
        assert _normalize_t0_t1(None) == (None, None)

    def test_unknown_clock_order_falls_back_to_index_0(self) -> None:
        """If epoch_clock is missing but t0_t1 is present, dev defaults to
        the first element and global stays None.
        """
        doc = {"data": {"element_epoch": {"t0_t1": [10.5, 20.5]}}}
        start, stop = _normalize_t0_t1(doc)
        assert start == {"devTime": 10.5, "globalTime": None}
        assert stop == {"devTime": 20.5, "globalTime": None}


# ---------------------------------------------------------------------------
# Probe / element row + probe_location enrichment
# ---------------------------------------------------------------------------

class TestProbeRow:
    def test_probe_location_split_uberon_vs_cl(self) -> None:
        locations = [
            {"data": {"probe_location": {
                "name": "right cerebral hemisphere", "ontology_name": "UBERON:0002813"}}},
            {"data": {"probe_location": {
                "name": "pyramidal neuron", "ontology_name": "CL:0000598"}}},
        ]
        loc, cell = _probe_location_split(locations)
        assert loc == ("right cerebral hemisphere", "UBERON:0002813")
        assert cell == ("pyramidal neuron", "CL:0000598")

    def test_probe_locations_for_joins_on_probe_id(self) -> None:
        element = {"data": {"base": {"id": "ELEM1"}}}
        pls = [
            {"data": {"depends_on": {"name": "probe_id", "value": "ELEM1"},
                      "probe_location": {"name": "V1", "ontology_name": "UBERON:0002436"}}},
            {"data": {"depends_on": {"name": "probe_id", "value": "OTHER"},
                      "probe_location": {"name": "elsewhere", "ontology_name": "UBERON:x"}}},
        ]
        matched = _probe_locations_for(element, pls)
        assert len(matched) == 1
        assert matched[0]["data"]["probe_location"]["name"] == "V1"

    def test_row_probe_with_location_enrichment(self) -> None:
        element = {
            "ndiId": "ELEM1",
            "data": {
                "base": {"id": "ELEM1"},
                "depends_on": [{"name": "subject_id", "value": "SUBJ1"}],
                "element": {"name": "righthem_10", "type": "spikes", "reference": 2,
                            "ndi_element_class": "ndi.neuron"},
            },
        }
        pls = [{"data": {
            "depends_on": {"name": "probe_id", "value": "ELEM1"},
            "probe_location": {"name": "right V1", "ontology_name": "UBERON:0002436"}}}]
        row = _row_probe(element, {"probe_location": pls})
        assert row["probeDocumentIdentifier"] == "ELEM1"
        assert row["probeName"] == "righthem_10"
        assert row["probeType"] == "spikes"
        assert row["probeReference"] == 2
        assert row["probeLocationName"] == "right V1"
        assert row["probeLocationOntology"] == "UBERON:0002436"
        assert row["cellTypeName"] is None
        assert row["cellTypeOntology"] is None
        assert row["subjectDocumentIdentifier"] == "SUBJ1"

    def test_row_probe_without_location_enrichment(self) -> None:
        """Haley has no probe_location docs — columns must still be present,
        empty. SummaryTableView auto-hides empty columns client-side.
        """
        element = {
            "ndiId": "ELEM2",
            "data": {
                "base": {"id": "ELEM2"},
                "depends_on": [{"name": "subject_id", "value": "SUBJ_H"}],
                "element": {"name": "midpoint_distance", "type": "distance", "reference": 1},
            },
        }
        row = _row_probe(element, {"probe_location": []})
        assert row["probeName"] == "midpoint_distance"
        assert row["probeLocationName"] is None
        assert row["probeLocationOntology"] is None


# ---------------------------------------------------------------------------
# Epoch row with treatment enrichment
# ---------------------------------------------------------------------------

class TestEpochRow:
    def _haley_epoch(self) -> dict:
        return {
            "ndiId": "EPOCH_H",
            "data": {
                "base": {"id": "EPOCH_H"},
                "depends_on": {"name": "element_id", "value": "ELEM_H"},
                "element_epoch": {
                    "epoch_clock": "dev_local_time,exp_global_time",
                    "t0_t1": [[0, 1000], [3600, 4600]],
                },
                "epochid": {"epochid": "PR811_4144_run01"},
            },
        }

    def _vh_epoch(self) -> dict:
        return {
            "ndiId": "EPOCH_VH",
            "data": {
                "base": {"id": "EPOCH_VH"},
                "depends_on": {"name": "element_id", "value": "ELEM_VH"},
                "element_epoch": {
                    "epoch_clock": "dev_local_time",
                    "t0_t1": [0, 545.43595],
                },
                "epochid": {"epochid": "t00009"},
            },
        }

    def test_haley_epoch_dual_clock(self) -> None:
        element = {"data": {"base": {"id": "ELEM_H"},
                            "depends_on": [{"name": "subject_id", "value": "SUBJ_H"}]}}
        row = _row_epoch(
            self._haley_epoch(),
            {"element": [element], "subject": [], "treatment": []},
            subject=None, element=element,
        )
        assert row["epochNumber"] == "PR811_4144_run01"
        assert row["epochDocumentIdentifier"] == "EPOCH_H"
        assert row["probeDocumentIdentifier"] == "ELEM_H"
        assert row["subjectDocumentIdentifier"] == "SUBJ_H"
        assert row["epochStart"] == {"devTime": 0, "globalTime": 1000}
        assert row["epochStop"] == {"devTime": 3600, "globalTime": 4600}

    def test_vh_epoch_scalar_clock(self) -> None:
        element = {"data": {"base": {"id": "ELEM_VH"},
                            "depends_on": [{"name": "subject_id", "value": "SUBJ_VH"}]}}
        row = _row_epoch(
            self._vh_epoch(),
            {"element": [element], "subject": [], "treatment": []},
            subject=None, element=element,
        )
        assert row["epochStart"] == {"devTime": 0, "globalTime": None}
        assert row["epochStop"] == {"devTime": 545.43595, "globalTime": None}

    def test_treatment_fanout_by_ontology_prefix(self) -> None:
        """Subject's EMPTY: treatment surfaces under Approach;
        CHEBI: treatment surfaces under Mixture.
        """
        element = {"data": {"base": {"id": "ELEM_VH"},
                            "depends_on": [{"name": "subject_id", "value": "SUBJ_VH"}]}}
        treatments = [
            {"data": {
                "depends_on": [{"name": "subject_id", "value": "SUBJ_VH"}],
                "treatment": {
                    "ontologyName": "EMPTY:0000198",
                    "name": "Natural right eye opening",
                    "numeric_value": None,
                    "string_value": "",
                },
            }},
            {"data": {
                "depends_on": [{"name": "subject_id", "value": "SUBJ_VH"}],
                "treatment": {
                    "ontologyName": "CHEBI:73328",
                    "name": "CNO",
                    "numeric_value": 1.0,
                    "string_value": "",
                },
            }},
        ]
        row = _row_epoch(
            self._vh_epoch(),
            {"element": [element], "treatment": treatments, "subject": []},
            subject=None, element=element,
        )
        assert row["approachName"] == "Natural right eye opening"
        assert row["approachOntology"] == "EMPTY:0000198"
        assert row["mixtureName"] == "CNO"
        assert row["mixtureOntology"] == "CHEBI:73328"

    def test_no_treatments_leaves_columns_none(self) -> None:
        element = {"data": {"base": {"id": "ELEM_VH"},
                            "depends_on": [{"name": "subject_id", "value": "SUBJ_VH"}]}}
        row = _row_epoch(
            self._vh_epoch(),
            {"element": [element], "treatment": [], "subject": []},
            subject=None, element=element,
        )
        assert row["approachName"] is None
        assert row["mixtureName"] is None

    def test_treatments_for_subject_filters_by_subject_id(self) -> None:
        treatments = [
            {"data": {"depends_on": [{"name": "subject_id", "value": "A"}],
                       "treatment": {"ontologyName": "EMPTY:1"}}},
            {"data": {"depends_on": [{"name": "subject_id", "value": "B"}],
                       "treatment": {"ontologyName": "EMPTY:2"}}},
        ]
        got = _treatments_for_subject("A", treatments)
        assert len(got) == 1
        assert got[0]["data"]["treatment"]["ontologyName"] == "EMPTY:1"

    def test_treatment_by_ontology_prefix_case_insensitive(self) -> None:
        treatments = [{"data": {"treatment":
            {"ontologyName": "empty:0000001", "name": "low-case prefix"}}}]
        name, ont = _treatment_by_ontology_prefix(treatments, "EMPTY")
        assert name == "low-case prefix"
        assert ont == "empty:0000001"


# ---------------------------------------------------------------------------
# Treatment row
# ---------------------------------------------------------------------------

class TestTreatmentRow:
    def test_basic(self) -> None:
        t = {"data": {
            "depends_on": [{"name": "subject_id", "value": "SUBJ_X"}],
            "treatment": {
                "ontologyName": "EMPTY:0000198",
                "name": "Natural right eye opening",
                "numeric_value": None,
                "string_value": "",
            }}}
        row = _row_treatment(t)
        assert row["treatmentName"] == "Natural right eye opening"
        assert row["treatmentOntology"] == "EMPTY:0000198"
        assert row["numericValue"] is None
        assert row["stringValue"] is None  # "" normalized to None
        assert row["subjectDocumentIdentifier"] == "SUBJ_X"


# ---------------------------------------------------------------------------
# Top-level dispatcher
# ---------------------------------------------------------------------------

class TestProjectForClass:
    def test_subject_dispatch(self) -> None:
        subject = {
            "data": {
                "base": {"id": "S1", "session_id": "sess"},
                "subject": {"local_identifier": "A1"},
            },
        }
        columns, rows = _project_for_class(
            "subject", [subject],
            {"openminds_subject": [], "subject": [subject], "treatment": []},
        )
        assert columns == SUBJECT_COLUMNS
        assert len(rows) == 1
        assert rows[0]["subjectIdentifier"] == "A1"

    def test_unknown_class_falls_back_to_generic(self) -> None:
        doc = {"name": "X", "data": {"base": {"id": "X1", "name": "X"}}}
        columns, rows = _project_for_class("unknown_class", [doc], {})
        assert columns[0]["key"] == "name"
        assert rows[0]["documentIdentifier"] == "X1"

    def test_epoch_dispatch_uses_enrichment(self) -> None:
        epoch = {
            "data": {
                "base": {"id": "E1"},
                "depends_on": {"name": "element_id", "value": "EL1"},
                "element_epoch": {"epoch_clock": "dev_local_time", "t0_t1": [0, 5]},
            },
        }
        element = {"data": {"base": {"id": "EL1"},
                            "depends_on": [{"name": "subject_id", "value": "SU1"}]}}
        columns, rows = _project_for_class(
            "element_epoch", [epoch],
            {"element": [element], "subject": [], "treatment": []},
        )
        assert len(columns) == 10
        assert rows[0]["probeDocumentIdentifier"] == "EL1"
        assert rows[0]["subjectDocumentIdentifier"] == "SU1"
        assert rows[0]["epochStart"] == {"devTime": 0, "globalTime": None}


# ---------------------------------------------------------------------------
# Misc helpers used by combined()
# ---------------------------------------------------------------------------

class TestMiscHelpers:
    def test_ndi_id_from_base(self) -> None:
        assert _ndi_id({"data": {"base": {"id": "NDI-X"}}}) == "NDI-X"

    def test_ndi_id_fallback_to_top_level(self) -> None:
        assert _ndi_id({"ndiId": "FALLBACK"}) == "FALLBACK"

    def test_index_by_ndi_id(self) -> None:
        docs = [
            {"data": {"base": {"id": "A"}}},
            {"data": {"base": {"id": "B"}}},
            {"data": {}},  # skipped
        ]
        idx = _index_by_ndi_id(docs)
        assert set(idx.keys()) == {"A", "B"}

    def test_epoch_element_ndi(self) -> None:
        epoch = {"data": {"depends_on": {"name": "element_id", "value": "ELEM"}}}
        assert _epoch_element_ndi(epoch) == "ELEM"

    def test_element_subject_ndi(self) -> None:
        element = {"data": {"depends_on": [
            {"name": "underlying_element_id", "value": []},
            {"name": "subject_id", "value": "SUBJ"},
        ]}}
        assert _element_subject_ndi(element) == "SUBJ"


class TestAgeFromOpenminds:
    def test_prefers_age_over_age_category(self) -> None:
        age = {"data": {"openminds": {
            "openminds_type": "https://openminds.om-i.org/types/Age",
            "fields": {"value": "P30", "name": "P30"}}}}
        cat = {"data": {"openminds": {
            "openminds_type": "https://openminds.om-i.org/types/AgeCategory",
            "fields": {"name": "adult"}}}}
        subj = {"_enriched_openminds": [cat, age]}
        assert _openminds_age_at_recording(subj) == "P30"

    def test_falls_back_to_age_category(self) -> None:
        cat = {"data": {"openminds": {
            "openminds_type": "https://openminds.om-i.org/types/AgeCategory",
            "fields": {"name": "adult"}}}}
        subj = {"_enriched_openminds": [cat]}
        assert _openminds_age_at_recording(subj) == "adult"


@pytest.mark.parametrize("v,expected", [
    ("", None),
    (None, None),
    ("  x  ", "x"),
    (0, 0),
    ([], []),
])
def test_clean_table(v: object, expected: object) -> None:
    assert _clean(v) == expected
