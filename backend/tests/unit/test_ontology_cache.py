"""Ontology cache round-trip — catches set()/get() encoding mismatches."""
from __future__ import annotations

import tempfile
from pathlib import Path

import pytest

from backend.services.ontology_cache import OntologyCache, OntologyTerm


@pytest.fixture
def cache():
    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
        path = Path(f.name)
    c = OntologyCache(db_path=str(path), ttl_days=30)
    try:
        yield c
    finally:
        path.unlink(missing_ok=True)


def test_roundtrip_preserves_all_fields(cache: OntologyCache) -> None:
    """Regression for pre-M4a bug: to_dict() wrote camelCase `termId` but
    get() did `OntologyTerm(**data)` expecting snake_case `term_id`. Every
    subsequent cache hit 500'd on `TypeError: __init__() got an unexpected
    keyword argument 'termId'`.
    """
    term = OntologyTerm(
        provider="NCBITaxon",
        term_id="6239",
        label="Caenorhabditis elegans",
        definition="A species of nematode.",
        url="https://identifiers.org/taxonomy/6239",
    )
    cache.set(term)
    got = cache.get("NCBITaxon", "6239")
    assert got is not None
    assert got.provider == "NCBITaxon"
    assert got.term_id == "6239"
    assert got.label == "Caenorhabditis elegans"
    assert got.definition.startswith("A species")
    assert got.url.startswith("https://")


def test_returns_none_for_miss(cache: OntologyCache) -> None:
    assert cache.get("NCBITaxon", "nonexistent") is None


def test_roundtrip_with_null_fields(cache: OntologyCache) -> None:
    """Some providers return 404s stored as null-label terms."""
    term = OntologyTerm(
        provider="EMPTY", term_id="9999999",
        label=None, definition=None, url=None,
    )
    cache.set(term)
    got = cache.get("EMPTY", "9999999")
    assert got is not None
    assert got.label is None
    assert got.term_id == "9999999"
