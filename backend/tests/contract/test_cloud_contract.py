"""Nightly contract tests against the real dev cloud.

Skipped if NDI_CLOUD_URL is set to a test URL. In CI, the nightly-contract
workflow sets NDI_CLOUD_URL to the real dev endpoint.
"""
from __future__ import annotations

import os

import httpx
import pytest

pytestmark = pytest.mark.skipif(
    "example.test" in os.environ.get("NDI_CLOUD_URL", ""),
    reason="Contract tests require a real cloud URL",
)


@pytest.fixture
def base_url() -> str:
    return os.environ["NDI_CLOUD_URL"].rstrip("/")


def test_published_datasets_responds(base_url: str) -> None:
    r = httpx.get(f"{base_url}/datasets/published", params={"page": 1, "pageSize": 5}, timeout=30)
    assert r.status_code == 200
    body = r.json()
    assert "datasets" in body
    assert "totalNumber" in body
    if body["datasets"]:
        d = body["datasets"][0]
        # Contract-guaranteed fields.
        for f in ("id", "name"):
            assert f in d, f"expected field {f} in dataset response"


def test_ndiquery_paginates_and_returns_number_matches(base_url: str) -> None:
    r = httpx.post(
        f"{base_url}/ndiquery",
        json={"searchstructure": [{"operation": "isa", "param1": "subject"}], "scope": "public"},
        params={"page": 1, "pageSize": 5},
        timeout=30,
    )
    assert r.status_code == 200
    body = r.json()
    assert "documents" in body
    assert "number_matches" in body, "ndiquery contract uses `number_matches` for total"


def test_document_class_counts_shape(base_url: str) -> None:
    # Find a published dataset with documents.
    r = httpx.get(f"{base_url}/datasets/published", params={"page": 1, "pageSize": 5}, timeout=30)
    datasets = r.json().get("datasets", [])
    target = next((d for d in datasets if d.get("documentCount", 0) > 0), None)
    if not target:
        pytest.skip("No published dataset with documents to test class-counts")
    r2 = httpx.get(f"{base_url}/datasets/{target['id']}/document-class-counts", timeout=30)
    assert r2.status_code == 200
    body = r2.json()
    assert "totalDocuments" in body
    assert "classCounts" in body
    assert isinstance(body["classCounts"], dict)
