"""Query DSL validation + ~or rejection."""
from __future__ import annotations

import pytest
from pydantic import ValidationError

from backend.services.query_service import QueryNode, QueryRequest


def test_accepts_known_operations() -> None:
    for op in ["isa", "contains_string", "depends_on", "hasfield", "exact_string"]:
        QueryNode(operation=op, param1="x")


def test_accepts_negated_operations() -> None:
    for op in ["~isa", "~contains_string", "~hasfield", "~exact_string"]:
        QueryNode(operation=op, param1="x")


def test_rejects_unknown_operation() -> None:
    with pytest.raises(ValidationError):
        QueryNode(operation="definitely_not_real", param1="x")


def test_rejects_negated_or() -> None:
    with pytest.raises(ValidationError):
        QueryNode(operation="~or")


def test_scope_keywords_allowed() -> None:
    for scope in ["public", "private", "all"]:
        QueryRequest(searchstructure=[QueryNode(operation="isa", param1="subject")], scope=scope)


def test_scope_single_id_allowed() -> None:
    QueryRequest(
        searchstructure=[QueryNode(operation="isa", param1="subject")],
        scope="507f1f77bcf86cd799439011",
    )


def test_scope_csv_ids_allowed() -> None:
    QueryRequest(
        searchstructure=[QueryNode(operation="isa", param1="subject")],
        scope="507f1f77bcf86cd799439011, 507f1f77bcf86cd799439022",
    )


def test_scope_invalid_id_rejected() -> None:
    with pytest.raises(ValidationError):
        QueryRequest(
            searchstructure=[QueryNode(operation="isa", param1="subject")],
            scope="not-a-hex-id",
        )
