"""`ontologyTableRow` grouping — plan §M4a step 2."""
from __future__ import annotations

from backend.services.summary_table_service import _split_csv


def test_split_csv_pads_to_expected_len() -> None:
    assert _split_csv("a,b", 4) == ["a", "b", "", ""]


def test_split_csv_truncates_to_expected_len() -> None:
    assert _split_csv("a,b,c,d,e", 3) == ["a", "b", "c"]


def test_split_csv_non_string_yields_empty_pad() -> None:
    assert _split_csv(None, 3) == ["", "", ""]
    assert _split_csv(42, 2) == ["", ""]


def test_split_csv_trims_whitespace() -> None:
    assert _split_csv("  a ,b   ,  c ", 3) == ["a", "b", "c"]


def test_split_csv_empty_entries_preserved_as_empty() -> None:
    assert _split_csv("a,,c", 3) == ["a", "", "c"]
