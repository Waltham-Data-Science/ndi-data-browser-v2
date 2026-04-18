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
