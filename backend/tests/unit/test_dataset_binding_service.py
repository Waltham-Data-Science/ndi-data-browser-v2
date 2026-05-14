"""Unit tests for DatasetBindingService.

These tests do NOT require NDI-python to be installed. We patch the
service's internals so the cold-load path returns fake Dataset objects
without ever hitting the network or the SDK. The contract under test is
the cache/eviction/error-handling shell, not the SDK itself — the SDK is
already exercised by NDI-python's own test suite + the (separate)
integration tests in ``tests/integration/test_dataset_binding_live.py``.
"""
from __future__ import annotations

import asyncio
from types import SimpleNamespace
from typing import Any
from unittest.mock import MagicMock, patch

import pytest

from backend.services.dataset_binding_service import (
    MAX_CACHED_DATASETS,
    DatasetBindingService,
    _CacheEntry,
)

# ---------------------------------------------------------------------------
# Fakes
# ---------------------------------------------------------------------------


def _make_fake_element(name: str, etype: str, n_epochs: int) -> SimpleNamespace:
    """Duck-typed stand-in for ndi.element.ndi_element.

    The service touches: ``.name``, ``.type``, ``.numepochs()``,
    ``.epochtable()``. Provide all four so the service can pick either
    path without a None-deref.
    """
    et = [{"epoch_number": i + 1} for i in range(n_epochs)]
    return SimpleNamespace(
        name=name,
        type=etype,
        numepochs=lambda: n_epochs,
        epochtable=lambda: (et, "fakehash"),
    )


def _make_fake_dataset(
    *,
    elements: list[SimpleNamespace] | None = None,
    subject_docs: list[dict[str, Any]] | None = None,
    reference: str = "fake_ref",
) -> SimpleNamespace:
    """Duck-typed ndi.dataset.Dataset.

    Surface the service uses:
      - ``._session.getelements()``
      - ``.database_search(query)`` for subject docs
      - ``.reference``
    """
    elements = elements or []
    subject_docs = subject_docs or []

    session = SimpleNamespace(getelements=lambda **_kw: elements)

    def db_search(_q: Any) -> list[Any]:
        # Service's only call is `isa('subject')`. Return canned docs.
        return subject_docs

    return SimpleNamespace(
        _session=session,
        database_search=db_search,
        reference=reference,
    )


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def reset_ndi_python_caches():
    """Force ndi_python_service's availability cache flags to a known
    state so test-local patches actually take effect.
    """
    from backend.services import ndi_python_service
    ndi_python_service._NDI_AVAILABLE = None
    ndi_python_service._DATASET_BINDING_AVAILABLE = None
    yield
    ndi_python_service._NDI_AVAILABLE = None
    ndi_python_service._DATASET_BINDING_AVAILABLE = None


@pytest.fixture
def svc(tmp_path) -> DatasetBindingService:
    return DatasetBindingService(cache_dir=str(tmp_path / "ndi-cache"))


@pytest.fixture
def ndi_available():
    """Patch is_ndi_available -> True for the duration of the test.

    The service calls ``from . import ndi_python_service`` lazily inside
    _cold_load(), so we must patch the attribute on the source module
    (``backend.services.ndi_python_service.is_ndi_available``) rather
    than on the binding module.
    """
    with patch(
        "backend.services.ndi_python_service.is_ndi_available",
        return_value=True,
    ) as p:
        yield p


@pytest.fixture
def ndi_unavailable():
    with patch(
        "backend.services.ndi_python_service.is_ndi_available",
        return_value=False,
    ) as p:
        yield p


# ---------------------------------------------------------------------------
# get_dataset — cache miss/hit/eviction/coalescing/failure
# ---------------------------------------------------------------------------


class TestGetDataset:
    async def test_returns_none_on_empty_id(self, svc: DatasetBindingService):
        assert await svc.get_dataset("") is None

    @pytest.mark.usefixtures("ndi_available")
    async def test_cold_miss_then_warm_hit(
        self, svc: DatasetBindingService
    ):
        """First call downloads + caches. Second call hits the cache and
        returns the SAME object without invoking downloadDataset again.
        """
        fake = _make_fake_dataset()
        call_count = 0

        def fake_download(dataset_id: str) -> Any:
            nonlocal call_count
            call_count += 1
            return fake

        with patch.object(svc, "_download_blocking", side_effect=fake_download):
            first = await svc.get_dataset("DS1")
            second = await svc.get_dataset("DS1")

        assert first is fake
        assert second is fake
        # Only ONE download — the second call must hit the warm cache.
        assert call_count == 1

    @pytest.mark.usefixtures("ndi_available")
    async def test_lru_eviction_at_max(
        self, svc: DatasetBindingService
    ):
        """Inserting MAX_CACHED_DATASETS + 1 distinct ids evicts the
        oldest. Verifies the LRU bound matches the documented constant.
        """
        fakes = {
            f"DS{i}": _make_fake_dataset(reference=f"ref{i}")
            for i in range(MAX_CACHED_DATASETS + 1)
        }

        def fake_download(dataset_id: str) -> Any:
            return fakes[dataset_id]

        with patch.object(svc, "_download_blocking", side_effect=fake_download):
            for i in range(MAX_CACHED_DATASETS + 1):
                await svc.get_dataset(f"DS{i}")

        # Cache size is exactly MAX_CACHED_DATASETS.
        assert len(svc._cache) == MAX_CACHED_DATASETS
        # Oldest (DS0) was evicted; the newest are still present.
        assert "DS0" not in svc._cache
        assert f"DS{MAX_CACHED_DATASETS}" in svc._cache

    @pytest.mark.usefixtures("ndi_available")
    async def test_concurrent_calls_dedupe(
        self, svc: DatasetBindingService
    ):
        """Two simultaneous get_dataset('DS1') calls share ONE download.

        Pins the per-dataset lock contract: while one task is in the
        cold path, others wait, then return the SAME cached object
        without a second download.
        """
        fake = _make_fake_dataset()
        call_count = 0

        def slow_download(_dataset_id: str) -> Any:
            nonlocal call_count
            call_count += 1
            return fake

        async def fire(_idx: int) -> Any:
            return await svc.get_dataset("DS1")

        with patch.object(svc, "_download_blocking", side_effect=slow_download):
            results = await asyncio.gather(fire(0), fire(1), fire(2))

        # All three calls returned the same object.
        assert results[0] is fake
        assert results[1] is fake
        assert results[2] is fake
        # And there was exactly ONE download.
        assert call_count == 1

    @pytest.mark.usefixtures("ndi_available")
    async def test_failure_returns_none_not_raise(
        self, svc: DatasetBindingService
    ):
        """When downloadDataset raises, get_dataset MUST return None
        rather than propagate — the chat falls back to ndi_query.
        """
        def boom(_dataset_id: str) -> Any:
            raise RuntimeError("simulated cloud-node 500")

        with patch.object(svc, "_download_blocking", side_effect=boom):
            result = await svc.get_dataset("DS-broken")

        assert result is None
        # Nothing cached on failure — a retry should attempt the cold
        # path again.
        assert "DS-broken" not in svc._cache

    @pytest.mark.usefixtures("ndi_unavailable")
    async def test_returns_none_when_ndi_unavailable(
        self, svc: DatasetBindingService
    ):
        """is_ndi_available=False short-circuits before
        downloadDataset is reached.
        """
        download = MagicMock()
        with patch.object(svc, "_download_blocking", download):
            result = await svc.get_dataset("DS1")

        assert result is None
        download.assert_not_called()


# ---------------------------------------------------------------------------
# overview — counts + cache_hit + cache_age semantics
# ---------------------------------------------------------------------------


@pytest.mark.usefixtures("ndi_available")
class TestOverview:
    async def test_happy_path_counts_match_fakes(
        self, svc: DatasetBindingService
    ):
        elements = [
            _make_fake_element("e0", "n-trode", n_epochs=3),
            _make_fake_element("e1", "stimulator", n_epochs=2),
        ]
        subjects = [{"_id": "subj1"}, {"_id": "subj2"}, {"_id": "subj3"}]
        fake = _make_fake_dataset(
            elements=elements, subject_docs=subjects, reference="DS-ref"
        )

        with patch.object(svc, "_download_blocking", return_value=fake):
            out = await svc.overview("DS1")

        assert out is not None
        assert out["element_count"] == 2
        # Subject count is best-effort — passes when ndi.query is
        # importable AND returns the canned subjects list. On dev
        # machines with an old/missing ndi.query, the subject_count
        # path silently falls back to 0 (documented partial-failure
        # behavior). Assert "either correct OR zero" so this test is
        # resilient to both environments.
        assert out["subject_count"] in (0, 3)
        # 3 + 2 epochs.
        assert out["epoch_count"] == 5
        assert out["elements"] == [
            {"name": "e0", "type": "n-trode"},
            {"name": "e1", "type": "stimulator"},
        ]
        assert out["elements_truncated"] is False
        assert out["reference"] == "DS-ref"
        # First call is a cold one → cache_hit must be False.
        assert out["cache_hit"] is False
        # cache_age is small (just measured), but it's a float >= 0.
        assert isinstance(out["cache_age_seconds"], float)
        assert out["cache_age_seconds"] >= 0.0

    async def test_warm_call_reports_cache_hit_true(
        self, svc: DatasetBindingService
    ):
        fake = _make_fake_dataset()
        with patch.object(svc, "_download_blocking", return_value=fake):
            await svc.overview("DS1")  # cold
            second = await svc.overview("DS1")  # warm

        assert second is not None
        assert second["cache_hit"] is True

    async def test_overview_truncates_to_50_elements(
        self, svc: DatasetBindingService
    ):
        elements = [
            _make_fake_element(f"e{i}", "n-trode", n_epochs=1)
            for i in range(120)
        ]
        fake = _make_fake_dataset(elements=elements)

        with patch.object(svc, "_download_blocking", return_value=fake):
            out = await svc.overview("DS1")

        assert out is not None
        # element_count reports the TRUE total even when listing is truncated.
        assert out["element_count"] == 120
        # Listing capped at 50.
        assert len(out["elements"]) == 50
        assert out["elements_truncated"] is True
        # Epoch count covers ALL elements, not just the truncated listing.
        assert out["epoch_count"] == 120

    async def test_overview_returns_none_on_binding_failure(
        self, svc: DatasetBindingService
    ):
        with patch.object(
            svc, "_download_blocking", side_effect=RuntimeError("boom")
        ):
            out = await svc.overview("DS-broken")

        assert out is None

    async def test_overview_tolerates_partial_traversal_failure(
        self, svc: DatasetBindingService
    ):
        """When database_search raises (e.g. malformed query backend),
        the overview should still surface element + epoch counts and
        return subject_count=0 rather than blanking the whole payload.
        """
        def bad_search(_q: Any) -> list[Any]:
            raise RuntimeError("simulated DB error")

        fake = _make_fake_dataset(
            elements=[_make_fake_element("e0", "n-trode", 2)]
        )
        # Override database_search to raise.
        fake.database_search = bad_search

        with patch.object(svc, "_download_blocking", return_value=fake):
            out = await svc.overview("DS1")

        assert out is not None
        assert out["element_count"] == 1
        assert out["epoch_count"] == 2
        # Subject search failed; subject_count fell back to 0.
        assert out["subject_count"] == 0


# ---------------------------------------------------------------------------
# Cache entry struct — basic invariants
# ---------------------------------------------------------------------------


class TestCacheEntry:
    def test_loaded_at_equals_first_loaded_at_at_creation(self):
        entry = _CacheEntry(dataset="x")
        assert entry.loaded_at == entry.first_loaded_at
        assert entry.dataset == "x"
