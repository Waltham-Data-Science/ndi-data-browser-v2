"""Validation boundary tests for the ``/api/visualize/distribution`` body.

Audit 2026-04-23 (#54): ``DistributionBody.datasetId`` previously had only
a length bound. The value flows into an f-string URL against
``ndi-cloud-node``, which the cloud trusts the proxy to validate. A body
like ``{"datasetId": "foo/bar"}`` would pivot into sibling resources.
These tests pin the pattern validation so the next refactor can't
regress it.
"""
from __future__ import annotations

import pytest
from pydantic import ValidationError

from backend.routers.visualize import DistributionBody


def _valid(**overrides: str) -> dict:
    base = {
        "datasetId": "ds1",
        "className": "subject",
        "field": "studyMetadata.species",
        "groupBy": None,
    }
    base.update(overrides)  # type: ignore[arg-type]
    return base


def test_accepts_a_realistic_body() -> None:
    body = DistributionBody.model_validate(_valid())
    assert body.datasetId == "ds1"
    assert body.className == "subject"
    assert body.field == "studyMetadata.species"


def test_accepts_mongo_oid_dataset_id() -> None:
    body = DistributionBody.model_validate(_valid(datasetId="507f1f77bcf86cd799439011"))
    assert len(body.datasetId) == 24


def test_accepts_optional_group_by() -> None:
    body = DistributionBody.model_validate(_valid(groupBy="studyMetadata.strain"))
    assert body.groupBy == "studyMetadata.strain"


@pytest.mark.parametrize(
    "bad_id",
    [
        "ds/1",            # path separator
        "ds..1",           # traversal-ish (caught by pattern, not length)
        "foo/bar",         # canonical injection the audit called out
        "../users",        # traversal variant
        "dataset with spaces",
        "dataset?q=1",     # query-string injection
        "dataset#frag",    # fragment injection
        "",                # empty
        "a" * 129,         # over length
    ],
)
def test_rejects_injection_in_datasetId(bad_id: str) -> None:  # noqa: N802 — datasetId mirrors JSON body field
    with pytest.raises(ValidationError):
        DistributionBody.model_validate(_valid(datasetId=bad_id))


@pytest.mark.parametrize(
    "bad_cls",
    [
        "subject.session",        # dot isn't allowed in class names
        "subject/session",
        "subject session",
        "subject?x=1",
        "",
        "a" * 65,
    ],
)
def test_rejects_injection_in_className(bad_cls: str) -> None:  # noqa: N802 — className mirrors JSON body field
    with pytest.raises(ValidationError):
        DistributionBody.model_validate(_valid(className=bad_cls))


@pytest.mark.parametrize(
    "bad_field",
    [
        "studyMetadata/species",  # slash
        "studyMetadata species",  # space
        "studyMetadata;drop",     # semicolon
        "studyMetadata['x']",     # brackets
        "",
        "a" * 129,
    ],
)
def test_rejects_injection_in_field(bad_field: str) -> None:
    with pytest.raises(ValidationError):
        DistributionBody.model_validate(_valid(field=bad_field))


def test_rejects_injection_in_groupBy() -> None:  # noqa: N802 — groupBy mirrors JSON body field
    with pytest.raises(ValidationError):
        DistributionBody.model_validate(_valid(groupBy="studyMetadata/strain"))
    with pytest.raises(ValidationError):
        DistributionBody.model_validate(_valid(groupBy="a" * 129))
