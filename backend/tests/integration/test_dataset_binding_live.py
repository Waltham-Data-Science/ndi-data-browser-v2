"""LIVE integration test for DatasetBindingService.

Hits the real ndi-cloud-node + downloads a real (small) dataset.
SKIPPED in CI by default — set ``LIVE_NDI_TESTS=1`` locally to run.

Why this is gated:
  - Requires NDI-python installed (vlt, ndicompress, ndi.cloud)
  - Requires reachable cloud-node API + S3
  - Cold load is ~10-30s wall clock — adds significant CI runtime
  - Cloud-side data drift could flake the test in unrelated PRs

The intent is just to prove the pipe works end-to-end. We don't assert
exact element counts (cloud data may change) — only that the service
returned a non-None dict with the documented keys.

Run locally:
  LIVE_NDI_TESTS=1 uv run pytest backend/tests/integration/test_dataset_binding_live.py -v
"""
from __future__ import annotations

import os

import pytest

# Bhar dataset — small + stable, used as the demo elsewhere in the
# repo. Switch to a different ID if it ever goes away.
DEMO_DATASET_ID = "69bc5ca11d547b1f6d083761"


@pytest.mark.skipif(
    os.environ.get("LIVE_NDI_TESTS", "") not in ("1", "true", "yes"),
    reason="LIVE_NDI_TESTS not set — skipping cloud-hitting integration test",
)
async def test_overview_against_real_cloud():
    """End-to-end smoke. Downloads a real dataset, computes overview,
    asserts the response shape.
    """
    from backend.services.dataset_binding_service import DatasetBindingService

    svc = DatasetBindingService()
    overview = await svc.overview(DEMO_DATASET_ID)

    assert overview is not None, (
        "binding returned None — check NDI-python install + cloud-node auth"
    )
    # Documented keys.
    for k in (
        "element_count",
        "subject_count",
        "epoch_count",
        "elements",
        "elements_truncated",
        "reference",
        "cache_hit",
        "cache_age_seconds",
    ):
        assert k in overview, f"missing key: {k}"

    # First call is cold; cache_hit MUST be False.
    assert overview["cache_hit"] is False
    # Type sanity.
    assert isinstance(overview["element_count"], int)
    assert isinstance(overview["elements"], list)


@pytest.mark.skipif(
    os.environ.get("LIVE_NDI_TESTS", "") not in ("1", "true", "yes"),
    reason="LIVE_NDI_TESTS not set",
)
async def test_warm_call_after_cold_load():
    """Second call on the same service instance reports cache_hit=True
    and a positive cache_age_seconds. Pins the LRU bookkeeping against
    a real download.
    """
    from backend.services.dataset_binding_service import DatasetBindingService

    svc = DatasetBindingService()
    cold = await svc.overview(DEMO_DATASET_ID)
    assert cold is not None
    warm = await svc.overview(DEMO_DATASET_ID)
    assert warm is not None
    assert warm["cache_hit"] is True
    assert warm["cache_age_seconds"] > 0.0
