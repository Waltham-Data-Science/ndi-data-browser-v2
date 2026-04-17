"""Document shape normalizer — reconciles cloud's hoisted-vs-wrapped responses.

The cloud returns:
- Bulk-fetch: body under `data.*`.
- Single-doc GET: body at top-level (no `data.` wrapper).

Downstream code (binary, deps, projection) reads via `doc.data.*` uniformly.
_normalize_document() ensures single-doc responses get rewrapped.
"""
from __future__ import annotations

from backend.services.document_service import _normalize_document


def test_bulk_fetch_shape_passes_through() -> None:
    raw = {
        "id": "m1",
        "ndiId": "ndi-1",
        "className": "subject",
        "data": {
            "base": {"id": "ndi-1", "session_id": "s1"},
            "subject": {"local_identifier": "A"},
        },
    }
    got = _normalize_document(raw)
    assert got is raw or got == raw  # no-op on already-wrapped shape
    assert got["data"]["base"]["id"] == "ndi-1"


def test_single_doc_shape_wraps_under_data() -> None:
    """Single-doc endpoint hoists body fields to top-level."""
    raw = {
        "id": "m1",
        "base": {"id": "ndi-1", "session_id": "s1", "name": "", "datestamp": "2025"},
        "depends_on": {"name": "element_id", "value": "ELEM"},
        "document_class": {"class_name": "element_epoch"},
        "element_epoch": {"epoch_clock": "dev_local_time", "t0_t1": [0, 5]},
        "files": {"file_list": ["a.vhsb"]},
    }
    got = _normalize_document(raw)
    # Top-level preserves id/metadata only.
    assert got["id"] == "m1"
    assert "base" not in got  # hoisted away under data
    # Body moved under data.
    assert got["data"]["base"] == raw["base"]
    assert got["data"]["depends_on"] == raw["depends_on"]
    assert got["data"]["element_epoch"]["epoch_clock"] == "dev_local_time"
    assert got["data"]["document_class"]["class_name"] == "element_epoch"


def test_empty_data_triggers_rewrap() -> None:
    """If cloud returns empty `data: {}` alongside hoisted fields (seen in
    practice with some proxied responses), still rewrap."""
    raw = {
        "id": "m1",
        "data": {},
        "base": {"id": "ndi-1"},
        "depends_on": {"name": "x", "value": "Y"},
    }
    got = _normalize_document(raw)
    assert got["data"]["base"] == {"id": "ndi-1"}
    assert got["data"]["depends_on"]["value"] == "Y"


def test_id_comes_from_underscore_id_fallback() -> None:
    """Cloud detail uses _id not id on some paths."""
    raw = {
        "_id": "m1",
        "base": {"id": "ndi-1"},
    }
    got = _normalize_document(raw)
    assert got["id"] == "m1" or got["_id"] == "m1"
    assert got["data"]["base"]["id"] == "ndi-1"


def test_passthrough_on_non_dict() -> None:
    assert _normalize_document(None) is None  # type: ignore[arg-type]
    assert _normalize_document([]) == []  # type: ignore[arg-type]
