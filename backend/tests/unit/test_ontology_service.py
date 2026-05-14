"""Unit tests for ``OntologyService`` — specifically the cache-stub
bypass behavior introduced as a fix for the granular-completeness
regression.

Pre-fix bug: when a term like ``WBStrain:00000001`` was looked up
BEFORE Phase A wired ``ndi.ontology.lookup`` as a fallback, the
legacy provider returned a stub (``label=None``, ``definition=None``)
which was cached. ``ONTOLOGY_CACHE_TTL_DAYS`` defaults to 30, so for
~a month after Phase A shipped, every lookup of that term hit the
stale stub and short-circuited the NDI-python fallback. End result:
the data browser kept rendering ``WBStrain:00000001`` raw instead of
"N2 wild-type" even though the NDI-python integration knew the
answer.

The fix: ``OntologyService.lookup`` now treats stubs as cache MISSES,
re-runs the fetch pipeline (legacy providers + NDI-python fallback),
and on success OVERWRITES the stub. So stuck stubs heal on first
use without waiting for the 30-day TTL to roll over.

These tests cover the lookup pipeline's branching directly with
stubbed providers; the NDI-python integration itself has its own
boundary tests.
"""
from __future__ import annotations

import asyncio
from typing import Any
from unittest.mock import patch

import pytest

from backend.services.ontology_cache import OntologyCache, OntologyTerm
from backend.services.ontology_service import OntologyService


@pytest.fixture
def cache(tmp_path) -> OntologyCache:
    return OntologyCache(db_path=str(tmp_path / "ontology_test.db"))


@pytest.fixture
def service(cache: OntologyCache) -> OntologyService:
    return OntologyService(cache=cache)


def _stub(provider: str, term_id: str) -> OntologyTerm:
    """An empty cache entry — what the legacy path returns when a
    provider doesn't know the term."""
    return OntologyTerm(
        provider=provider, term_id=term_id,
        label=None, definition=None, url=None,
    )


def _hit(provider: str, term_id: str, label: str) -> OntologyTerm:
    return OntologyTerm(
        provider=provider, term_id=term_id,
        label=label, definition=f"{label} definition", url=None,
    )


@pytest.mark.asyncio
async def test_lookup_returns_real_cached_hit_without_refetching(service, cache):
    """Real cache hits short-circuit the fetch path — no upstream calls."""
    cache.set(_hit("CL", "0000540", "neuron"))
    with patch.object(service, "_fetch_from_provider") as fetch_mock, \
         patch.object(service, "_try_ndi_fallback") as ndi_mock:
        result = await service.lookup("CL:0000540")
    assert result.label == "neuron"
    fetch_mock.assert_not_called()
    ndi_mock.assert_not_called()


@pytest.mark.asyncio
async def test_lookup_treats_stub_as_cache_miss_and_retries(service, cache):
    """STUB cache entries (label=None AND definition=None) must NOT
    short-circuit. The fetch pipeline must run again so the
    NDI-python fallback can fire."""
    # Seed a stub — simulates a pre-Phase-A cached miss for WBStrain.
    cache.set(_stub("WBStrain", "00000001"))
    async def fake_fetch(_p, _t):
        # Legacy provider still doesn't know WBStrain.
        return _stub("WBStrain", "00000001")
    async def fake_ndi(_term, p, t):
        return _hit(p, t, "N2 wild-type")
    with patch.object(service, "_fetch_from_provider", side_effect=fake_fetch), \
         patch.object(service, "_try_ndi_fallback", side_effect=fake_ndi):
        result = await service.lookup("WBStrain:00000001")
    # NDI-python's result wins, and the cache stub is replaced with
    # the real hit so subsequent lookups don't re-pay the cost.
    assert result.label == "N2 wild-type"
    # Cache now has the real entry.
    cached_after = cache.get("WBStrain", "00000001")
    assert cached_after is not None
    assert cached_after.label == "N2 wild-type"


@pytest.mark.asyncio
async def test_stub_bypass_caches_new_stub_when_both_paths_fail(service, cache):
    """When the stub-miss retry ALSO comes up empty (legacy + NDI-python
    both unknown), we return the empty result without thrashing the
    cache: we already have a stub for this term, no need to write
    another. Subsequent lookups still bypass, but that's OK — the
    extra cost is only when the term genuinely can't be resolved by
    anyone, which is rare."""
    cache.set(_stub("UNKNOWN", "99999"))
    async def fake_fetch(_p, _t):
        return _stub("UNKNOWN", "99999")
    async def fake_ndi(_term, _p, _t):
        return None
    with patch.object(service, "_fetch_from_provider", side_effect=fake_fetch), \
         patch.object(service, "_try_ndi_fallback", side_effect=fake_ndi):
        result = await service.lookup("UNKNOWN:99999")
    assert result.label is None
    assert result.definition is None
    # The pre-existing stub stays in the cache — no double-write.
    cached_after = cache.get("UNKNOWN", "99999")
    assert cached_after is not None
    assert cached_after.label is None


@pytest.mark.asyncio
async def test_fresh_term_with_provider_hit_does_not_call_ndi(service, cache):
    """When the legacy provider returns a REAL hit on first lookup,
    NDI-python is NOT called (it's a fallback, not a co-resolver).
    This is the original behavior; verify the stub fix didn't break it."""
    async def fake_fetch(p, t):
        return _hit(p, t, "frontal cortex")
    with patch.object(service, "_fetch_from_provider", side_effect=fake_fetch), \
         patch.object(service, "_try_ndi_fallback") as ndi_mock:
        result = await service.lookup("UBERON:0001870")
    assert result.label == "frontal cortex"
    ndi_mock.assert_not_called()
    # And the cache now has the real entry.
    assert cache.get("UBERON", "0001870").label == "frontal cortex"


@pytest.mark.asyncio
async def test_fresh_term_falls_through_to_ndi_when_legacy_returns_stub(
    service, cache,
):
    """For terms the legacy providers can't resolve (e.g. NDIC, WBStrain),
    the legacy path returns a stub and we fall through to NDI-python.
    Same as test_lookup_treats_stub_as_cache_miss_and_retries but
    without any prior cache state — covers the cold-start path."""
    async def fake_fetch(p, t):
        return _stub(p, t)
    async def fake_ndi(_term, p, t):
        return _hit(p, t, "Purpose: Assessing spatial frequency tuning")
    with patch.object(service, "_fetch_from_provider", side_effect=fake_fetch), \
         patch.object(service, "_try_ndi_fallback", side_effect=fake_ndi):
        result = await service.lookup("NDIC:1")
    assert result.label == "Purpose: Assessing spatial frequency tuning"
    # And the result is now cached as a real hit.
    assert cache.get("NDIC", "1").label == "Purpose: Assessing spatial frequency tuning"


@pytest.mark.asyncio
async def test_batch_lookup_unblocks_stale_stubs(service, cache):
    """The batch path inherits stub-bypass automatically because it
    delegates to ``self.lookup`` per term. Verify end-to-end so we
    don't regress this."""
    # Seed two stubs (mix of providers) so a batch hits both.
    cache.set(_stub("WBStrain", "00000001"))
    cache.set(_stub("NDIC", "1"))
    async def fake_fetch(p, t):
        return _stub(p, t)
    labels = {
        "WBStrain:00000001": "N2 wild-type",
        "NDIC:1": "Purpose: Assessing spatial frequency tuning",
    }
    async def fake_ndi(term, p, t):
        return _hit(p, t, labels[term])
    with patch.object(service, "_fetch_from_provider", side_effect=fake_fetch), \
         patch.object(service, "_try_ndi_fallback", side_effect=fake_ndi):
        results = await service.batch_lookup(
            ["WBStrain:00000001", "NDIC:1"],
        )
    assert len(results) == 2
    label_by_id = {f"{r.provider}:{r.term_id}": r.label for r in results}
    assert label_by_id["WBStrain:00000001"] == "N2 wild-type"
    assert label_by_id["NDIC:1"] == "Purpose: Assessing spatial frequency tuning"
