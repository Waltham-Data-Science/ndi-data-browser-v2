"""Projection of cloud document shapes into summary-table rows."""
from __future__ import annotations

from backend.services.summary_table_service import (
    _depends_on_values,
    _openminds_attr,
    _row_epoch,
    _row_probe,
    _row_subject,
    _t0_t1,
)


def test_depends_on_single_object() -> None:
    d = {"data": {"depends_on": {"name": "element_id", "value": "abc"}}}
    assert _depends_on_values(d) == ["abc"]


def test_depends_on_list() -> None:
    d = {"data": {"depends_on": [{"name": "x", "value": "A"}, {"name": "y", "value": "B"}]}}
    assert _depends_on_values(d) == ["A", "B"]


def test_depends_on_missing() -> None:
    assert _depends_on_values({"data": {}}) == []
    assert _depends_on_values({}) == []
    assert _depends_on_values(None) == []


def test_openminds_attr_species() -> None:
    subject = {
        "data": {"base": {"id": "s1"}, "subject": {"local_identifier": "sub"}},
        "_enriched_list": [
            {"data": {
                "openminds": {
                    "openminds_type": "https://openminds.om-i.org/types/Species",
                    "fields": {"name": "Mustela putorius furo"},
                },
            }},
        ],
    }
    assert _openminds_attr(subject, "Species") == "Mustela putorius furo"
    assert _openminds_attr(subject, "BiologicalSex") is None


def test_row_subject_with_enrichment() -> None:
    subject = {
        "data": {"base": {"id": "s1"}, "subject": {"local_identifier": "ferret-A"}},
        "_enriched_list": [
            {"data": {"openminds": {
                "openminds_type": "https://openminds.om-i.org/types/Species",
                "fields": {"name": "ferret"}}}},
            {"data": {"openminds": {
                "openminds_type": "https://openminds.om-i.org/types/BiologicalSex",
                "fields": {"name": "female"}}}},
        ],
    }
    row = _row_subject(subject)
    assert row["name"] == "ferret-A"
    assert row["species"] == "ferret"
    assert row["sex"] == "female"


def test_row_probe_element_shape() -> None:
    doc = {"data": {"element": {"name": "leftcortex_10", "type": "spikes", "reference": 2}}}
    row = _row_probe(doc)
    assert row["name"] == "leftcortex_10"
    assert row["type"] == "spikes"
    assert row["reference"] == 2


def test_row_epoch_t0_t1() -> None:
    doc = {"data": {
        "epochid": {"epochid": "t00011"},
        "element_epoch": {"t0_t1": [0.0, 545.43]},
    }}
    row = _row_epoch(doc)
    assert row["name"] == "t00011"
    assert row["epoch_start"] == 0.0
    assert row["epoch_stop"] == 545.43


def test_t0_t1_missing_returns_none_pair() -> None:
    assert _t0_t1({"data": {}}) == (None, None)
