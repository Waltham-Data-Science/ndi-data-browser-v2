"""DatasetSummaryService — composes four cloud primitives per Plan B B1.

Mocks ndi-cloud-node via respx, wires a fakeredis-backed RedisTableCache,
and exercises:
  - happy path populating every field
  - 0-subject datasets → species/strains/sexes stay `None`
  - Openminds Schema A (Species/BiologicalSex) and Schema B (Strain)
  - extraction warning emitted on label-without-ontology fallback
  - cache miss → compute → write, followed by a cache hit that bypasses
    the cloud
  - user isolation: alice and bob land on different cache keys
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import fakeredis.aioredis
import httpx
import pytest
import respx
from cryptography.fernet import Fernet

from backend.auth.session import SessionData, user_scope_for
from backend.cache.redis_table import RedisTableCache
from backend.clients.ndi_cloud import NdiCloudClient
from backend.services.dataset_summary_service import (
    SUMMARY_CACHE_TTL_SECONDS,
    DatasetSummary,
    DatasetSummaryService,
    OntologyTerm,
    summary_cache_key,
)
from backend.services.ontology_cache import OntologyCache
from backend.services.ontology_service import OntologyService

FIXTURE_DIR = Path(__file__).parent.parent / "fixtures" / "openminds"


def _load_fixture(name: str) -> dict[str, Any]:
    return json.loads((FIXTURE_DIR / name).read_text())


HALEY_SPECIES = _load_fixture("haley_openminds_species.json")
HALEY_STRAIN = _load_fixture("haley_openminds_strain.json")
HALEY_SEX = _load_fixture("haley_openminds_biologicalsex.json")
HALEY_GST = _load_fixture("haley_openminds_geneticstraintype.json")
VH_SPECIES = _load_fixture("vanhooser_openminds_species.json")


# ---------------------------------------------------------------------------
# Fixture helpers
# ---------------------------------------------------------------------------

def _dataset_raw(**overrides: Any) -> dict[str, Any]:
    base = {
        "_id": "DSX",
        "name": "A Testing Dataset",
        "abstract": "Some abstract",
        "license": "CC-BY-4.0",
        "doi": "https://doi.org/10.63884/abc",
        "totalSize": 12345678,
        "createdAt": "2025-09-01T00:00:00.000Z",
        "updatedAt": "2026-01-01T00:00:00.000Z",
        "contributors": [
            {"firstName": "Ada", "lastName": "Lovelace", "orcid": "https://orcid.org/0000-0001"},
            {"firstName": "Grace", "lastName": "Hopper"},
        ],
        "associatedPublications": [
            {"title": "Paper A", "DOI": "https://doi.org/10.1/abc"},
        ],
    }
    base.update(overrides)
    return base


def _counts_raw(**class_counts: int) -> dict[str, Any]:
    total = sum(class_counts.values())
    return {
        "datasetId": "DSX",
        "totalDocuments": total,
        "classCounts": class_counts,
    }


def _ndiquery_body_for(ids: list[str]) -> dict[str, Any]:
    return {
        "number_matches": len(ids),
        "pageSize": 1000,
        "page": 1,
        "documents": [{"id": i} for i in ids],
    }


def _bulk_fetch_body(docs: list[dict[str, Any]]) -> dict[str, Any]:
    return {"documents": docs}


def _minimal_session(user_id: str) -> SessionData:
    return SessionData(
        session_id="sid-" + user_id,
        user_id=user_id,
        user_email_hash="h",
        access_token="tok-" + user_id,
        access_token_expires_at=9_999_999_999,
        issued_at=0,
        last_active=0,
        ip_addr_hash="iph",
        user_agent_hash="uah",
    )


@pytest.fixture
async def cloud() -> NdiCloudClient:
    import os
    os.environ.setdefault("SESSION_ENCRYPTION_KEY", Fernet.generate_key().decode())
    client = NdiCloudClient()
    await client.start()
    try:
        yield client
    finally:
        await client.close()


@pytest.fixture
def ontology_service(tmp_path) -> OntologyService:  # type: ignore[no-untyped-def]
    """OntologyService with an offline cache. No real HTTP. Label enrichment
    reads pre-seeded entries; unknown IDs resolve to `None` via the
    `_safe_lookup` fallback.
    """
    cache = OntologyCache(db_path=str(tmp_path / "ont.sqlite"), ttl_days=30)
    svc = OntologyService(cache)

    # Stub the internal fetcher so the service never leaves the process.
    async def _fake_fetch(provider: str, term_id: str):  # type: ignore[no-untyped-def]
        from backend.services.ontology_cache import OntologyTerm as CacheTerm
        labels = {
            ("NCBITaxon", "6239"): "Caenorhabditis elegans",
            ("NCBITaxon", "10116"): "Rattus norvegicus",
            ("WBStrain", "00000001"): "N2",
            ("PATO", "0001340"): "hermaphrodite",
            ("PATO", "0000383"): "female",
            ("UBERON", "0002436"): "primary visual cortex",
        }
        label = labels.get((provider, term_id))
        return CacheTerm(
            provider=provider, term_id=term_id, label=label,
            definition=None, url=None,
        )

    svc._fetch_from_provider = _fake_fetch  # type: ignore[method-assign]
    return svc


# ---------------------------------------------------------------------------
# Happy path
# ---------------------------------------------------------------------------

def _rekey_om_to(doc: dict[str, Any], subject_id: str) -> dict[str, Any]:
    """Deep-copy an openminds_subject fixture and point its subject_id
    depends_on edge at the given ID so several fixtures roll up under a
    single synthetic subject.
    """
    new = json.loads(json.dumps(doc))
    deps = new["data"].get("depends_on")
    if isinstance(deps, list):
        for d in deps:
            if d.get("name") == "subject_id":
                d["value"] = subject_id
    return new


def _probe_location_doc() -> dict[str, Any]:
    return {
        "id": "pl1", "ndiId": "ndi-pl1",
        "data": {
            "base": {"id": "ndi-pl1"},
            "depends_on": {"name": "probe_id", "value": "ndi-el1"},
            "probe_location": {
                "name": "primary visual cortex",
                "ontology_name": "uberon:0002436",
            },
        },
    }


def _element_doc() -> dict[str, Any]:
    return {
        "id": "el1", "ndiId": "ndi-el1",
        "data": {
            "base": {"id": "ndi-el1"},
            "depends_on": [{"name": "subject_id", "value": "ndi-sub1"}],
            "element": {"name": "probe-1", "type": "n-trode", "reference": 1},
        },
    }


def _install_happy_path_routes(router: respx.MockRouter, dataset_id: str) -> None:
    router.get(f"/datasets/{dataset_id}").respond(
        200, json=_dataset_raw(_id=dataset_id),
    )
    router.get(f"/datasets/{dataset_id}/document-class-counts").respond(
        200,
        json=_counts_raw(
            subject=1, session=1, probe=1, element=1, element_epoch=4,
            openminds_subject=4, probe_location=1,
        ),
    )

    def _ndiquery(request: httpx.Request, route: Any) -> httpx.Response:
        body = json.loads(request.content.decode())
        param1 = body["searchstructure"][0]["param1"]
        ids = {
            "openminds_subject": ["om-sp", "om-st", "om-sx", "om-gst"],
            "probe_location": ["pl1"],
            "element": ["el1"],
        }.get(param1, [])
        return httpx.Response(200, json=_ndiquery_body_for(ids))

    router.post("/ndiquery").mock(side_effect=_ndiquery)

    def _bulk(request: httpx.Request, route: Any) -> httpx.Response:
        body = json.loads(request.content.decode())
        requested = body["documentIds"]
        fixtures = {
            "om-sp": _rekey_om_to(HALEY_SPECIES, "ndi-sub1"),
            "om-st": _rekey_om_to(HALEY_STRAIN, "ndi-sub1"),
            "om-sx": _rekey_om_to(HALEY_SEX, "ndi-sub1"),
            "om-gst": _rekey_om_to(HALEY_GST, "ndi-sub1"),
            "pl1": _probe_location_doc(),
            "el1": _element_doc(),
        }
        docs = [fixtures[i] for i in requested if i in fixtures]
        return httpx.Response(200, json=_bulk_fetch_body(docs))

    router.post(f"/datasets/{dataset_id}/documents/bulk-fetch").mock(
        side_effect=_bulk,
    )


@pytest.mark.asyncio
async def test_happy_path_populates_every_field(
    cloud: NdiCloudClient,
    ontology_service: OntologyService,
) -> None:
    dataset_id = "DSX"
    async with respx.mock(base_url="https://api.example.test/v1") as router:
        _install_happy_path_routes(router, dataset_id)
        svc = DatasetSummaryService(cloud, ontology_service)
        summary = await svc.build_summary(dataset_id, session=None)

    assert isinstance(summary, DatasetSummary)
    assert summary.datasetId == dataset_id
    assert summary.schemaVersion == "summary:v1"
    assert summary.counts.subjects == 1
    assert summary.counts.sessions == 1
    assert summary.counts.probes == 1
    assert summary.counts.elements == 1
    assert summary.counts.epochs == 4
    assert summary.counts.totalDocuments == 13  # sum of class counts

    assert summary.species is not None and len(summary.species) == 1
    assert summary.species[0].ontologyId == "NCBITaxon:6239"
    # Ontology resolver stub returns the canonical label.
    assert summary.species[0].label == "Caenorhabditis elegans"

    assert summary.strains is not None and len(summary.strains) == 1
    assert summary.strains[0].ontologyId == "WBStrain:00000001"
    # Schema B (Strain) lookup — ontology stub returns "N2".
    assert summary.strains[0].label == "N2"

    assert summary.sexes is not None and len(summary.sexes) == 1
    assert summary.sexes[0].ontologyId == "PATO:0001340"
    assert summary.sexes[0].label == "hermaphrodite"

    assert summary.brainRegions is not None and len(summary.brainRegions) == 1
    # probe_location ontology_name arrives as `uberon:0002436`; the service
    # normalizes provider prefix to uppercase for dedupe.
    assert summary.brainRegions[0].ontologyId == "UBERON:0002436"
    assert summary.brainRegions[0].label == "primary visual cortex"

    assert summary.probeTypes == ["n-trode"]
    assert summary.dateRange.earliest == "2025-09-01T00:00:00.000Z"
    assert summary.dateRange.latest == "2026-01-01T00:00:00.000Z"
    assert summary.totalSizeBytes == 12345678

    # Citation.
    assert summary.citation.title == "A Testing Dataset"
    assert summary.citation.license == "CC-BY-4.0"
    assert summary.citation.datasetDoi == "https://doi.org/10.63884/abc"
    assert summary.citation.paperDois == ["https://doi.org/10.1/abc"]
    assert summary.citation.year == 2025
    assert len(summary.citation.contributors) == 2
    assert summary.citation.contributors[0].orcid == "https://orcid.org/0000-0001"
    assert summary.citation.contributors[1].orcid is None


# ---------------------------------------------------------------------------
# Zero subjects → species/strains/sexes stay `None`
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_zero_subject_dataset_yields_null_extractions(
    cloud: NdiCloudClient, ontology_service: OntologyService,
) -> None:
    dataset_id = "DSX"
    async with respx.mock(
        base_url="https://api.example.test/v1", assert_all_called=False,
    ) as router:
        router.get(f"/datasets/{dataset_id}").respond(
            200, json=_dataset_raw(_id=dataset_id),
        )
        router.get(f"/datasets/{dataset_id}/document-class-counts").respond(
            200, json=_counts_raw(session=3),
        )
        # No subjects, no probes -> service must not even touch ndiquery.
        ndiquery = router.post("/ndiquery").respond(
            200, json={"documents": []},
        )
        bulk = router.post(
            f"/datasets/{dataset_id}/documents/bulk-fetch",
        ).respond(200, json={"documents": []})

        svc = DatasetSummaryService(cloud, ontology_service)
        summary = await svc.build_summary(dataset_id, session=None)

    assert summary.species is None
    assert summary.strains is None
    assert summary.sexes is None
    assert summary.brainRegions is None
    assert summary.probeTypes is None
    assert summary.counts.subjects == 0
    assert summary.counts.sessions == 3
    assert summary.extractionWarnings == []
    assert ndiquery.call_count == 0
    assert bulk.call_count == 0


# ---------------------------------------------------------------------------
# Schema A + Schema B dispatch
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_schema_a_and_b_both_extract(
    cloud: NdiCloudClient, ontology_service: OntologyService,
) -> None:
    """Species is Schema A (preferredOntologyIdentifier); Strain is Schema B
    (ontologyIdentifier). Both fixtures must surface with their canonical
    ontology IDs, not None.
    """
    dataset_id = "DSX"
    async with respx.mock(base_url="https://api.example.test/v1") as router:
        router.get(f"/datasets/{dataset_id}").respond(
            200, json=_dataset_raw(_id=dataset_id),
        )
        router.get(f"/datasets/{dataset_id}/document-class-counts").respond(
            200, json=_counts_raw(subject=1, openminds_subject=2),
        )
        router.post("/ndiquery").respond(
            200, json=_ndiquery_body_for(["om-sp", "om-st"]),
        )

        def _bulk(request: httpx.Request, route: Any) -> httpx.Response:
            body = json.loads(request.content.decode())
            fixtures = {"om-sp": HALEY_SPECIES, "om-st": HALEY_STRAIN}
            return httpx.Response(
                200, json=_bulk_fetch_body(
                    [fixtures[i] for i in body["documentIds"] if i in fixtures],
                ),
            )

        router.post(f"/datasets/{dataset_id}/documents/bulk-fetch").mock(
            side_effect=_bulk,
        )

        svc = DatasetSummaryService(cloud, ontology_service)
        summary = await svc.build_summary(dataset_id, session=None)

    assert summary.species is not None
    assert summary.species[0].ontologyId == "NCBITaxon:6239"  # Schema A
    assert summary.strains is not None
    assert summary.strains[0].ontologyId == "WBStrain:00000001"  # Schema B


# ---------------------------------------------------------------------------
# Extraction warning on label-only fallback
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_warning_emitted_on_label_without_ontology(
    cloud: NdiCloudClient, ontology_service: OntologyService,
) -> None:
    """A subject whose GeneticStrainType carries a label but no
    preferredOntologyIdentifier must be included AND record a warning so the
    frontend can explain the fallback via the debug tooltip.
    """
    dataset_id = "DSX"
    # Use the Haley GeneticStrainType fixture directly — its
    # `preferredOntologyIdentifier` is `""`.
    gst_only = HALEY_GST

    async with respx.mock(base_url="https://api.example.test/v1") as router:
        router.get(f"/datasets/{dataset_id}").respond(
            200, json=_dataset_raw(_id=dataset_id),
        )
        router.get(f"/datasets/{dataset_id}/document-class-counts").respond(
            200, json=_counts_raw(subject=1, openminds_subject=1),
        )
        # We intercept only openminds_subject — the service must not fall
        # over if probe_location/element queries return nothing.
        router.post("/ndiquery").respond(
            200, json=_ndiquery_body_for(["om-gst"]),
        )
        router.post(f"/datasets/{dataset_id}/documents/bulk-fetch").respond(
            200, json=_bulk_fetch_body([gst_only]),
        )

        # Use a distinct type label by temporarily patching the fixture's
        # openminds_type to Species; label stays intact so the warning is
        # triggered on the Species path.
        patched = json.loads(json.dumps(gst_only))
        patched["data"]["openminds"]["openminds_type"] = (
            "https://openminds.om-i.org/types/Species"
        )
        patched["data"]["openminds"]["fields"]["preferredOntologyIdentifier"] = ""
        patched["data"]["openminds"]["fields"]["name"] = "an unknown species"

        router.post(f"/datasets/{dataset_id}/documents/bulk-fetch").respond(
            200, json=_bulk_fetch_body([patched]),
        )

        svc = DatasetSummaryService(cloud, ontology_service)
        summary = await svc.build_summary(dataset_id, session=None)

    assert summary.species is not None
    # Label-only entry is preserved — never truncated or dropped.
    assert summary.species[0].label == "an unknown species"
    assert summary.species[0].ontologyId is None
    assert any("species" in w.lower() for w in summary.extractionWarnings)


# ---------------------------------------------------------------------------
# Cache miss -> compute -> hit (zero extra cloud calls)
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_cache_miss_then_hit() -> None:
    dataset_id = "DSX"
    redis = fakeredis.aioredis.FakeRedis(decode_responses=True)
    cache = RedisTableCache(redis=redis, ttl_seconds=SUMMARY_CACHE_TTL_SECONDS)

    async with respx.mock(
        base_url="https://api.example.test/v1", assert_all_called=False,
    ) as router:
        dataset_route = router.get(f"/datasets/{dataset_id}").respond(
            200, json=_dataset_raw(_id=dataset_id),
        )
        counts_route = router.get(
            f"/datasets/{dataset_id}/document-class-counts",
        ).respond(200, json=_counts_raw(session=1))
        ndiquery_route = router.post("/ndiquery").respond(
            200, json={"documents": []},
        )

        client = NdiCloudClient()
        await client.start()
        try:
            # Use a no-op ontology service — it's not exercised here.
            cache_dir = Path("/tmp")
            ont = OntologyService(OntologyCache(
                db_path=str(cache_dir / "ont-hit.sqlite"),
                ttl_days=1,
            ))
            svc = DatasetSummaryService(client, ont, cache=cache)
            s1 = await svc.build_summary(dataset_id, session=None)
            first_dataset = dataset_route.call_count
            first_counts = counts_route.call_count
            s2 = await svc.build_summary(dataset_id, session=None)
        finally:
            await client.close()
            await ont.close()
            await redis.aclose()

    assert dataset_route.call_count == first_dataset, "dataset should not be re-fetched"
    assert counts_route.call_count == first_counts, "counts should not be re-fetched"
    assert ndiquery_route.call_count == 0
    # Second result loads from the cache — computedAt frozen from the
    # first compute. Serialized JSON is byte-identical, not just equal.
    assert s1.model_dump(mode="json") == s2.model_dump(mode="json")


# ---------------------------------------------------------------------------
# Alice and Bob get different cache keys
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_user_cache_keys_are_isolated() -> None:
    dataset_id = "DSX"
    alice = _minimal_session("alice")
    bob = _minimal_session("bob")
    assert summary_cache_key(dataset_id, alice) != summary_cache_key(dataset_id, bob)
    assert summary_cache_key(dataset_id, None) == (
        f"summary:v1:{dataset_id}:public"
    )
    assert summary_cache_key(dataset_id, alice) == (
        f"summary:v1:{dataset_id}:{user_scope_for(alice)}"
    )


# ---------------------------------------------------------------------------
# Pydantic contract: unknown fields rejected
# ---------------------------------------------------------------------------

def test_ontology_term_is_strict() -> None:
    with pytest.raises(Exception):  # noqa: B017 — any pydantic ValidationError variant  # pydantic.ValidationError
        OntologyTerm(label="x", ontologyId="NCBITaxon:10090", extra="bad")  # type: ignore[call-arg]


# ---------------------------------------------------------------------------
# Per-class deadline degradation (Phase 6.7 follow-up)
#
# Smoke-test pass after the Phase-6.7 cutover-readiness work found the
# /summary endpoint returning 504 on the largest published datasets
# (101k+ docs) — the cloud's per-class ndiquery for openminds_subject
# alone took 60s+, blowing past Railway's 88s function ceiling. This
# pin asserts the new PER_CLASS_FETCH_TIMEOUT_SECONDS deadline degrades
# gracefully: the synthesis returns a partial DatasetSummary (counts +
# citation intact, per-class facts None) plus a typed extraction
# warning, instead of bubbling the timeout up to the route handler.
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_per_class_timeout_degrades_to_partial_summary(
    cloud: NdiCloudClient,
    ontology_service: OntologyService,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """When ndiquery for a class exceeds PER_CLASS_FETCH_TIMEOUT_SECONDS,
    the synthesis must complete with that class's facts set to ``None``
    and a class-naming warning in ``extractionWarnings``. counts +
    citation + dateRange + totalSizeBytes (which come from the cheap
    stage-1 calls) must still render.

    We patch the deadline to 0.05s and stall ndiquery with a deferred
    response so the wait_for in `_fetch_class_bounded` fires
    deterministically — far cheaper than a real-time 25s wait.
    """
    import asyncio as _asyncio

    import backend.services.dataset_summary_service as svc_module

    monkeypatch.setattr(svc_module, "PER_CLASS_FETCH_TIMEOUT_SECONDS", 0.05)

    dataset_id = "DSX"
    async with respx.mock(
        base_url="https://api.example.test/v1", assert_all_called=False,
    ) as router:
        router.get(f"/datasets/{dataset_id}").respond(
            200, json=_dataset_raw(_id=dataset_id),
        )
        router.get(f"/datasets/{dataset_id}/document-class-counts").respond(
            200,
            json=_counts_raw(
                subject=1, session=1, probe=1, element=1, element_epoch=4,
                openminds_subject=1, probe_location=1,
            ),
        )

        async def _slow_ndiquery(
            request: httpx.Request, route: Any,
        ) -> httpx.Response:
            # Stall longer than the patched deadline to deterministically
            # fire the wait_for. The 0.5s sleep is comfortably above the
            # 0.05s deadline yet keeps the test wall-clock cheap.
            await _asyncio.sleep(0.5)
            return httpx.Response(200, json={"documents": []})

        router.post("/ndiquery").mock(side_effect=_slow_ndiquery)
        # bulk_fetch never reached because ndiquery times out first; mock
        # anyway so respx's strict-routing mode doesn't 404 if the wait_for
        # races and a single ndiquery completes.
        router.post(f"/datasets/{dataset_id}/documents/bulk-fetch").respond(
            200, json={"documents": []},
        )

        summary_svc = DatasetSummaryService(cloud, ontology_service)
        summary = await summary_svc.build_summary(dataset_id, session=None)

    # Stage 1 facts (cheap path) intact:
    assert summary.counts.subjects == 1
    # 1 subject + 1 session + 1 probe + 1 element + 4 element_epoch
    # + 1 openminds_subject + 1 probe_location = 10. The class-counts
    # endpoint sums all classes including the ones whose ndiqueries
    # subsequently time out.
    assert summary.counts.totalDocuments == 10
    assert summary.citation.title == "A Testing Dataset"
    assert summary.citation.license == "CC-BY-4.0"
    assert summary.dateRange.earliest == "2025-09-01T00:00:00.000Z"
    assert summary.totalSizeBytes == 12345678
    # Per-class facts degrade to empty lists (not None). The
    # `_result_or_warn` helper turns the timeout exception into `[]` so
    # downstream extractors run on a known-empty input — semantically
    # "we queried but got 0 successful extractions", with the typed
    # extraction warning explaining WHY the count is 0. The frontend
    # renders `[]` as an em-dash and surfaces the warning count via the
    # SummaryFooter info icon.
    #
    # We deliberately don't bubble TimeoutError up as `None` because
    # `None` already has a distinct semantic meaning in this contract
    # ("the underlying class isn't applicable here, e.g. zero subjects
    # → no species lookup attempted") that the timeout case doesn't
    # match — the lookup WAS attempted, it just didn't return data.
    assert summary.species == []
    assert summary.strains == []
    assert summary.sexes == []
    assert summary.brainRegions == []
    assert summary.probeTypes == []
    # All three classes hit the deadline, surfacing one warning each.
    # The exact text comes from `_result_or_warn`'s
    # f"{what} query failed: {result!s}" — message bodies name the class
    # so operators reading logs can identify which fetch stalled.
    warnings_text = "\n".join(summary.extractionWarnings)
    assert "openminds_subject query failed" in warnings_text
    assert "probe_location query failed" in warnings_text
    assert "element query failed" in warnings_text
    assert "exceeded 0.05s" in warnings_text


# ---------------------------------------------------------------------------
# Stage-1 deadline degradation (Phase 6.7 follow-up to PR #101)
#
# Smoke-test pass after PR #101 (per-class 25s deadline) shipped found
# the production /summary endpoint still 504-ing on 101k-doc datasets.
# Root cause: the cloud's `/document-class-counts` endpoint also takes
# 60s+ on those datasets, exhausting Railway's 88s ceiling DURING
# stage 1 — before the per-class deadlines in stage 2 ever get a
# chance to fire. PR #102 adds a STAGE_1_FETCH_TIMEOUT_SECONDS deadline
# around the two stage-1 cloud calls (`get_dataset` +
# `get_document_class_counts`), so a slow stage 1 degrades to a
# synthetic minimum payload + extraction warning instead of bubbling
# the timeout up.
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_stage_1_counts_timeout_degrades_to_zero_counts(
    cloud: NdiCloudClient,
    ontology_service: OntologyService,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """When ``/document-class-counts`` exceeds STAGE_1_FETCH_TIMEOUT_SECONDS,
    the synthesizer must substitute zero counts + emit a warning rather
    than 504-ing. The synthetic zero-counts then makes
    ``subjects_present`` and ``probe_present`` both False, so stage 2
    short-circuits to ``_empty_list()`` and the synthesis completes
    in microseconds.

    Patches the deadline to 50ms and stalls the counts endpoint with a
    0.5s sleep so the wait_for fires deterministically.
    """
    import asyncio as _asyncio

    import backend.services.dataset_summary_service as svc_module

    monkeypatch.setattr(svc_module, "STAGE_1_FETCH_TIMEOUT_SECONDS", 0.05)

    dataset_id = "DSX"
    async with respx.mock(
        base_url="https://api.example.test/v1", assert_all_called=False,
    ) as router:
        router.get(f"/datasets/{dataset_id}").respond(
            200, json=_dataset_raw(_id=dataset_id),
        )

        async def _slow_counts(
            request: httpx.Request, route: Any,
        ) -> httpx.Response:
            await _asyncio.sleep(0.5)
            return httpx.Response(
                200, json=_counts_raw(subject=99, openminds_subject=99),
            )

        router.get(
            f"/datasets/{dataset_id}/document-class-counts",
        ).mock(side_effect=_slow_counts)
        # Stage 2 should NOT be reached when counts says 0; mock anyway
        # so respx's strict mode doesn't 404 if a single call slips
        # through during the race.
        router.post("/ndiquery").respond(200, json={"documents": []})

        summary_svc = DatasetSummaryService(cloud, ontology_service)
        summary = await summary_svc.build_summary(dataset_id, session=None)

    # Counts degraded to all-zero (stage-1 timeout substituted the
    # zero-counts payload). The dataset metadata fetch also fired in
    # parallel; if it raced and won within the 50ms patched deadline
    # we keep the citation; if it lost, that's also OK and a second
    # warning surfaces. Pin only the counts-side contract — the
    # parallel dataset_task is a separate test below.
    assert summary.counts.subjects == 0
    assert summary.counts.totalDocuments == 0
    # Per-class facts come back None because subjects_present + probe_present
    # are both False (no fanout fired).
    assert summary.species is None
    assert summary.strains is None
    assert summary.sexes is None
    assert summary.brainRegions is None
    assert summary.probeTypes is None
    # Counts-timeout warning surfaces with a "exceeded 0.05s" suffix so
    # operators can correlate with logs / patched constants.
    warnings_text = "\n".join(summary.extractionWarnings)
    assert "class counts query failed" in warnings_text
    assert "exceeded 0.05s" in warnings_text


# ---------------------------------------------------------------------------
# Record-fallback gating for stage-2 fanout
#
# Smoke-test follow-up: when stage-1 /document-class-counts times out,
# stage 2 was short-circuiting entirely (subjects_present=False,
# probe_present=False) — even when the dataset record itself reports
# `numberOfSubjects > 0` and `documentCount > 0`. Net effect: a degraded
# summary with null per-class facts despite the dataset record having
# enough information to gate the fanout. Fix uses record fields as
# fallback when counts is degraded so stage 2 still attempts the
# openminds_subject + probe_location + element ndiqueries.
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_stage_1_counts_timeout_still_runs_stage_2_via_record_fields(
    cloud: NdiCloudClient,
    ontology_service: OntologyService,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """When `/document-class-counts` times out but the dataset record
    has `numberOfSubjects > 0`, stage 2 must still attempt the
    openminds_subject + probe_location + element fanout. Without this
    fallback the summary degrades to null facts even on datasets where
    the record-level signal is clear.
    """
    import asyncio as _asyncio

    import backend.services.dataset_summary_service as svc_module

    monkeypatch.setattr(svc_module, "STAGE_1_FETCH_TIMEOUT_SECONDS", 0.05)

    dataset_id = "DSX"
    async with respx.mock(
        base_url="https://api.example.test/v1", assert_all_called=False,
    ) as router:
        # Dataset record returns fast with numberOfSubjects + documentCount
        # populated — the FALLBACK signals stage 2 should run.
        router.get(f"/datasets/{dataset_id}").respond(
            200,
            json=_dataset_raw(
                _id=dataset_id, numberOfSubjects=2, documentCount=12,
            ),
        )

        # Counts endpoint is slow → triggers stage-1 timeout.
        async def _slow_counts(
            request: httpx.Request, route: Any,
        ) -> httpx.Response:
            await _asyncio.sleep(0.5)
            return httpx.Response(200, json=_counts_raw(subject=99))

        router.get(
            f"/datasets/{dataset_id}/document-class-counts",
        ).mock(side_effect=_slow_counts)

        # Stage-2 ndiqueries succeed (fast) and bulk-fetch returns the
        # openminds species fixture. Demonstrates that the fallback
        # gate kept stage 2 running and we got real per-class data
        # despite the counts timeout.
        router.post("/ndiquery").respond(
            200, json=_ndiquery_body_for(["om-sp"]),
        )
        router.post(f"/datasets/{dataset_id}/documents/bulk-fetch").respond(
            200, json=_bulk_fetch_body([HALEY_SPECIES]),
        )

        summary_svc = DatasetSummaryService(cloud, ontology_service)
        summary = await summary_svc.build_summary(dataset_id, session=None)

    # Counts came from the record fallback (subjects from
    # numberOfSubjects, totalDocuments from documentCount):
    assert summary.counts.subjects == 2
    assert summary.counts.totalDocuments == 12
    # Stage 2 RAN despite the counts timeout — species was extracted
    # from the openminds_subject ndiquery+bulk-fetch path. This is
    # the win this PR introduces.
    assert summary.species is not None
    assert len(summary.species) > 0
    assert summary.species[0].ontologyId == "NCBITaxon:6239"
    # Counts-timeout warning is still surfaced for operator awareness.
    warnings_text = "\n".join(summary.extractionWarnings)
    assert "class counts query failed" in warnings_text


def test_safe_record_int_handles_all_input_shapes() -> None:
    """Pin the helper that powers stage-2 record-fallback gating: it
    must accept any input shape and return a non-negative int (0 on
    anything other than a positive integer field value).
    """
    from backend.services.dataset_summary_service import _safe_record_int

    # Happy path:
    assert _safe_record_int({"numberOfSubjects": 42}, "numberOfSubjects") == 42
    # Field present but null (Sophie Griswold's record):
    assert _safe_record_int({"numberOfSubjects": None}, "numberOfSubjects") == 0
    # Field missing entirely:
    assert _safe_record_int({}, "numberOfSubjects") == 0
    # Wrong type (string):
    assert _safe_record_int({"numberOfSubjects": "1656"}, "numberOfSubjects") == 0
    # Negative value (defensive):
    assert _safe_record_int({"numberOfSubjects": -1}, "numberOfSubjects") == 0
    # Non-dict input (e.g. dataset_raw was an exception, not a dict):
    assert _safe_record_int(None, "numberOfSubjects") == 0
    assert _safe_record_int("garbage", "numberOfSubjects") == 0


@pytest.mark.asyncio
async def test_stage_1_dataset_timeout_does_not_block_summary(
    cloud: NdiCloudClient,
    ontology_service: OntologyService,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """When ``/datasets/:id`` exceeds STAGE_1_FETCH_TIMEOUT_SECONDS, the
    synthesizer must substitute an empty dict so citation/dateRange/
    totalSize default to their null/empty branches and the summary
    still renders. The counts call (parallel) keeps its own timeout
    independently.
    """
    import asyncio as _asyncio

    import backend.services.dataset_summary_service as svc_module

    monkeypatch.setattr(svc_module, "STAGE_1_FETCH_TIMEOUT_SECONDS", 0.05)

    dataset_id = "DSX"
    async with respx.mock(
        base_url="https://api.example.test/v1", assert_all_called=False,
    ) as router:

        async def _slow_dataset(
            request: httpx.Request, route: Any,
        ) -> httpx.Response:
            await _asyncio.sleep(0.5)
            return httpx.Response(200, json=_dataset_raw(_id=dataset_id))

        router.get(f"/datasets/{dataset_id}").mock(side_effect=_slow_dataset)
        # Counts returns fast — no fanout (zero subjects/probes).
        router.get(
            f"/datasets/{dataset_id}/document-class-counts",
        ).respond(200, json=_counts_raw(session=2))

        summary_svc = DatasetSummaryService(cloud, ontology_service)
        summary = await summary_svc.build_summary(dataset_id, session=None)

    # Counts came through fine.
    assert summary.counts.sessions == 2
    # Dataset metadata defaulted (empty dict), so:
    # - citation gets the dataset_id-as-title fallback (already-tested
    #   via _citation_from_raw on missing-name input)
    # - dateRange both None
    # - totalSizeBytes None
    # The exact citation title is whatever `_citation_from_raw({})`
    # returns; we just assert the citation block exists (envelope-shape).
    assert summary.citation is not None
    assert summary.dateRange.earliest is None
    assert summary.dateRange.latest is None
    assert summary.totalSizeBytes is None
    warnings_text = "\n".join(summary.extractionWarnings)
    assert "dataset metadata query failed" in warnings_text
    assert "exceeded 0.05s" in warnings_text


# ---------------------------------------------------------------------------
# Differential cache TTL based on synthesis quality
#
# Smoke-test pass after PRs #101/#102 found the production cache holding
# DEGRADED summaries (counts=0, warnings present) for the full 5-minute
# blanket TTL on large datasets — meaning every viewer for those 5 min
# saw the partial data even when the underlying cloud state had warmed
# enough for a full synthesis to succeed. PR introduces per-call TTL:
# 24h on full-success entries, 5min on degraded entries — so the cron
# (every 5min) gets frequent retry chances on degraded cache while
# full-success entries ride out a whole day.
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_full_success_summary_caches_24h() -> None:
    """A summary with empty `extractionWarnings` and non-zero counts is
    a full success — it should cache for 24h so subsequent viewers
    don't re-synthesize unnecessarily.
    """
    from backend.services.dataset_summary_service import (
        SUMMARY_CACHE_TTL_FULL_SECONDS,
        _summary_cache_ttl,
    )

    full_payload = {
        "datasetId": "d1",
        "counts": {
            "sessions": 1, "subjects": 1, "probes": 1, "elements": 1,
            "epochs": 4, "totalDocuments": 8,
        },
        "extractionWarnings": [],
    }
    assert _summary_cache_ttl(full_payload) == SUMMARY_CACHE_TTL_FULL_SECONDS
    assert SUMMARY_CACHE_TTL_FULL_SECONDS == 24 * 60 * 60


@pytest.mark.asyncio
async def test_degraded_summary_caches_5min() -> None:
    """A summary with non-empty `extractionWarnings` is degraded — it
    caches for only 5 minutes so the cron's next tick can retry sooner
    in case the cloud's working set has since warmed.
    """
    from backend.services.dataset_summary_service import (
        SUMMARY_CACHE_TTL_DEGRADED_SECONDS,
        _summary_cache_ttl,
    )

    degraded_payload = {
        "datasetId": "d1",
        "counts": {
            "sessions": 0, "subjects": 0, "probes": 0, "elements": 0,
            "epochs": 0, "totalDocuments": 0,
        },
        "extractionWarnings": [
            "class counts query failed: counts fetch exceeded 20.0s",
        ],
    }
    assert _summary_cache_ttl(degraded_payload) == SUMMARY_CACHE_TTL_DEGRADED_SECONDS
    assert SUMMARY_CACHE_TTL_DEGRADED_SECONDS == 5 * 60


@pytest.mark.asyncio
async def test_empty_dataset_caches_24h_too() -> None:
    """A genuinely empty dataset (zero counts, no warnings — synthesis
    ran cleanly and found nothing) is also a full success. No reason
    to short-TTL it.
    """
    from backend.services.dataset_summary_service import (
        SUMMARY_CACHE_TTL_FULL_SECONDS,
        _summary_cache_ttl,
    )

    empty_payload = {
        "datasetId": "d1",
        "counts": {
            "sessions": 0, "subjects": 0, "probes": 0, "elements": 0,
            "epochs": 0, "totalDocuments": 0,
        },
        "extractionWarnings": [],
    }
    assert _summary_cache_ttl(empty_payload) == SUMMARY_CACHE_TTL_FULL_SECONDS


@pytest.mark.asyncio
async def test_cache_uses_differential_ttl_on_compute() -> None:
    """End-to-end: cache miss → producer runs → result inspected →
    appropriate TTL written to Redis. We assert the SET ex= value to
    pin the wire-level contract.
    """
    from unittest.mock import AsyncMock

    from backend.cache.redis_table import RedisTableCache
    from backend.services.dataset_summary_service import _summary_cache_ttl

    redis = AsyncMock()
    redis.get = AsyncMock(return_value=None)  # cache miss
    redis.set = AsyncMock(return_value=True)
    cache = RedisTableCache(redis=redis, ttl_seconds=300)

    full_payload = {
        "datasetId": "d1",
        "counts": {
            "sessions": 1, "subjects": 1, "probes": 1, "elements": 1,
            "epochs": 4, "totalDocuments": 8,
        },
        "extractionWarnings": [],
    }

    async def producer() -> dict[str, Any]:
        return full_payload

    out = await cache.get_or_compute(
        "test:full", producer, ttl_for=_summary_cache_ttl,
    )
    assert out == full_payload
    redis.set.assert_called_once()
    # Pin the actual TTL: 24h for full success.
    _, kwargs = redis.set.call_args
    assert kwargs["ex"] == 24 * 60 * 60


def test_summary_schema_version_literal() -> None:
    with pytest.raises(Exception):  # noqa: B017 — any pydantic ValidationError variant
        DatasetSummary.model_validate({
            "datasetId": "x",
            "counts": {
                "sessions": 0, "subjects": 0, "probes": 0,
                "elements": 0, "epochs": 0, "totalDocuments": 0,
            },
            "species": None, "strains": None, "sexes": None,
            "brainRegions": None, "probeTypes": None,
            "dateRange": {"earliest": None, "latest": None},
            "totalSizeBytes": None,
            "citation": {
                "title": "x", "license": None, "datasetDoi": None,
                "paperDois": [], "contributors": [], "year": None,
            },
            "computedAt": "2026-01-01T00:00:00Z",
            "schemaVersion": "summary:v99",  # wrong
            "extractionWarnings": [],
        })
