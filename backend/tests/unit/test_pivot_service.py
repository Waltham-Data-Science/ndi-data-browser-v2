"""PivotService — Plan B B6e grain-selectable pivot v1.

Exercises:
  - happy path per grain (subject, session, element)
  - invalid grain → ValidationFailed
  - grain with zero docs on a dataset → NotFound
  - cache miss → compute → hit (zero extra cloud calls)
  - user isolation on cache keys
  - grain-specific column population (species via Schema A,
    strain via Schema B, probe_location → anatomy + cell-type split)
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
from backend.errors import NotFound, ValidationFailed
from backend.services.pivot_service import (
    PIVOT_CACHE_TTL_SECONDS,
    PIVOT_KEY_PREFIX,
    SUPPORTED_GRAINS,
    PivotResponse,
    PivotService,
    pivot_cache_key,
)

FIXTURE_DIR = Path(__file__).parent.parent / "fixtures" / "openminds"


def _load_fixture(name: str) -> dict[str, Any]:
    return json.loads((FIXTURE_DIR / name).read_text())


HALEY_SPECIES = _load_fixture("haley_openminds_species.json")
HALEY_STRAIN = _load_fixture("haley_openminds_strain.json")
HALEY_SEX = _load_fixture("haley_openminds_biologicalsex.json")


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _counts_raw(**class_counts: int) -> dict[str, Any]:
    total = sum(class_counts.values())
    return {
        "datasetId": "DSX",
        "totalDocuments": total,
        "classCounts": class_counts,
    }


def _ndiquery_body(ids: list[str]) -> dict[str, Any]:
    return {
        "number_matches": len(ids),
        "pageSize": 1000,
        "page": 1,
        "documents": [{"id": i} for i in ids],
    }


def _bulk_body(docs: list[dict[str, Any]]) -> dict[str, Any]:
    return {"documents": docs}


def _subject_doc(
    subject_ndi: str,
    session_id: str,
    local_identifier: str | None = None,
) -> dict[str, Any]:
    return {
        "id": f"sub-{subject_ndi}",
        "ndiId": subject_ndi,
        "data": {
            "base": {
                "id": subject_ndi,
                "session_id": session_id,
                "name": local_identifier or f"subject-{subject_ndi}",
            },
            "subject": {
                "local_identifier": local_identifier,
            } if local_identifier else {},
        },
    }


def _element_doc(
    element_ndi: str, subject_ndi: str,
    *, name: str = "probe-1", probe_type: str = "n-trode",
) -> dict[str, Any]:
    return {
        "id": f"el-{element_ndi}",
        "ndiId": element_ndi,
        "data": {
            "base": {"id": element_ndi, "name": name},
            "depends_on": [{"name": "subject_id", "value": subject_ndi}],
            "element": {
                "name": name,
                "type": probe_type,
                "reference": 1,
            },
        },
    }


def _probe_location_doc(
    element_ndi: str,
    *, name: str = "primary visual cortex",
    ontology: str = "uberon:0002436",
) -> dict[str, Any]:
    return {
        "id": f"pl-{element_ndi}",
        "ndiId": f"ndi-pl-{element_ndi}",
        "data": {
            "base": {"id": f"ndi-pl-{element_ndi}"},
            "depends_on": {"name": "probe_id", "value": element_ndi},
            "probe_location": {"name": name, "ontology_name": ontology},
        },
    }


def _cell_type_doc(element_ndi: str) -> dict[str, Any]:
    """A probe_location whose ontology_name is a CL:... prefix — splits
    into the cellType columns rather than probeLocation."""
    return {
        "id": f"cl-{element_ndi}",
        "ndiId": f"ndi-cl-{element_ndi}",
        "data": {
            "base": {"id": f"ndi-cl-{element_ndi}"},
            "depends_on": {"name": "probe_id", "value": element_ndi},
            "probe_location": {
                "name": "Type III BNST neuron",
                "ontology_name": "CL:0000598",
            },
        },
    }


def _rekey_om_to(doc: dict[str, Any], subject_ndi: str) -> dict[str, Any]:
    """Deep-copy an openminds_subject fixture and point its subject_id
    depends_on edge at the given ndiId."""
    new = json.loads(json.dumps(doc))
    deps = new["data"].get("depends_on")
    if isinstance(deps, list):
        for d in deps:
            if d.get("name") == "subject_id":
                d["value"] = subject_ndi
    return new


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
async def cloud() -> NdiCloudClient:  # type: ignore[no-untyped-def]
    import os
    os.environ.setdefault("SESSION_ENCRYPTION_KEY", Fernet.generate_key().decode())
    client = NdiCloudClient()
    await client.start()
    try:
        yield client
    finally:
        await client.close()


# ---------------------------------------------------------------------------
# Happy path — subject grain
# ---------------------------------------------------------------------------

def _install_subject_grain_routes(
    router: respx.MockRouter, dataset_id: str,
) -> None:
    router.get(f"/datasets/{dataset_id}/document-class-counts").respond(
        200,
        json=_counts_raw(
            subject=2, session=1, element=1,
            openminds_subject=6, probe_location=1,
        ),
    )

    def _ndiquery(request: httpx.Request, route: Any) -> httpx.Response:
        body = json.loads(request.content.decode())
        param1 = body["searchstructure"][0]["param1"]
        ids = {
            "subject": ["sub-A", "sub-B"],
            "openminds_subject": [
                "om-A-sp", "om-A-st", "om-A-sx",
                "om-B-sp", "om-B-st", "om-B-sx",
            ],
        }.get(param1, [])
        return httpx.Response(200, json=_ndiquery_body(ids))

    router.post("/ndiquery").mock(side_effect=_ndiquery)

    def _bulk(request: httpx.Request, route: Any) -> httpx.Response:
        body = json.loads(request.content.decode())
        requested = body["documentIds"]
        fixtures: dict[str, dict[str, Any]] = {
            "sub-A": _subject_doc("ndi-sub-A", "sess-1", "A@lab.edu"),
            "sub-B": _subject_doc("ndi-sub-B", "sess-1", "B@lab.edu"),
            "om-A-sp": _rekey_om_to(HALEY_SPECIES, "ndi-sub-A"),
            "om-A-st": _rekey_om_to(HALEY_STRAIN, "ndi-sub-A"),
            "om-A-sx": _rekey_om_to(HALEY_SEX, "ndi-sub-A"),
            "om-B-sp": _rekey_om_to(HALEY_SPECIES, "ndi-sub-B"),
            "om-B-st": _rekey_om_to(HALEY_STRAIN, "ndi-sub-B"),
            "om-B-sx": _rekey_om_to(HALEY_SEX, "ndi-sub-B"),
        }
        docs = [fixtures[i] for i in requested if i in fixtures]
        return httpx.Response(200, json=_bulk_body(docs))

    router.post(f"/datasets/{dataset_id}/documents/bulk-fetch").mock(
        side_effect=_bulk,
    )


@pytest.mark.asyncio
async def test_subject_grain_happy_path(cloud: NdiCloudClient) -> None:
    dataset_id = "DSX"
    async with respx.mock(base_url="https://api.example.test/v1") as router:
        _install_subject_grain_routes(router, dataset_id)
        svc = PivotService(cloud)
        body = await svc.pivot_by_grain(dataset_id, "subject", session=None)

    envelope = PivotResponse.model_validate(body)
    assert envelope.datasetId == dataset_id
    assert envelope.grain == "subject"
    assert envelope.schemaVersion == "pivot:v1"
    assert envelope.totalRows == 2

    col_keys = {c.key for c in envelope.columns}
    assert "subjectDocumentIdentifier" in col_keys
    assert "subjectLocalIdentifier" in col_keys
    assert "speciesName" in col_keys
    assert "strainName" in col_keys

    # Per-row: species (Schema A) and strain (Schema B) both populate.
    row_a = next(r for r in envelope.rows if r["subjectLocalIdentifier"] == "A@lab.edu")
    assert row_a["speciesName"] == "Caenorhabditis elegans"
    assert row_a["speciesOntology"] == "NCBITaxon:6239"
    assert row_a["strainName"] == "N2"
    assert row_a["strainOntology"] == "WBStrain:00000001"
    assert row_a["biologicalSexName"] == "hermaphrodite"
    assert row_a["biologicalSexOntology"] == "PATO:0001340"
    assert row_a["sessionDocumentIdentifier"] == "sess-1"


# ---------------------------------------------------------------------------
# Session grain — aggregation across subjects sharing a session
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_session_grain_aggregates_subjects(
    cloud: NdiCloudClient,
) -> None:
    dataset_id = "DSX"
    async with respx.mock(base_url="https://api.example.test/v1") as router:
        router.get(f"/datasets/{dataset_id}/document-class-counts").respond(
            200, json=_counts_raw(subject=3, session=2, openminds_subject=6),
        )

        def _ndiquery(request: httpx.Request, route: Any) -> httpx.Response:
            body = json.loads(request.content.decode())
            param1 = body["searchstructure"][0]["param1"]
            ids = {
                "subject": ["sub-A", "sub-B", "sub-C"],
                "openminds_subject": [
                    "om-A-sp", "om-A-st",
                    "om-B-sp", "om-B-st",
                    "om-C-sp", "om-C-st",
                ],
            }.get(param1, [])
            return httpx.Response(200, json=_ndiquery_body(ids))

        router.post("/ndiquery").mock(side_effect=_ndiquery)

        def _bulk(request: httpx.Request, route: Any) -> httpx.Response:
            body = json.loads(request.content.decode())
            requested = body["documentIds"]
            fixtures: dict[str, dict[str, Any]] = {
                # Session-1 has two subjects; session-2 has one.
                "sub-A": _subject_doc("ndi-sub-A", "sess-1"),
                "sub-B": _subject_doc("ndi-sub-B", "sess-1"),
                "sub-C": _subject_doc("ndi-sub-C", "sess-2"),
                "om-A-sp": _rekey_om_to(HALEY_SPECIES, "ndi-sub-A"),
                "om-A-st": _rekey_om_to(HALEY_STRAIN, "ndi-sub-A"),
                "om-B-sp": _rekey_om_to(HALEY_SPECIES, "ndi-sub-B"),
                "om-B-st": _rekey_om_to(HALEY_STRAIN, "ndi-sub-B"),
                "om-C-sp": _rekey_om_to(HALEY_SPECIES, "ndi-sub-C"),
                "om-C-st": _rekey_om_to(HALEY_STRAIN, "ndi-sub-C"),
            }
            docs = [fixtures[i] for i in requested if i in fixtures]
            return httpx.Response(200, json=_bulk_body(docs))

        router.post(f"/datasets/{dataset_id}/documents/bulk-fetch").mock(
            side_effect=_bulk,
        )

        svc = PivotService(cloud)
        body = await svc.pivot_by_grain(dataset_id, "session", session=None)

    envelope = PivotResponse.model_validate(body)
    assert envelope.grain == "session"
    # Two sessions — largest-first ordering (session-1 has 2 subjects).
    assert envelope.totalRows == 2
    assert envelope.rows[0]["sessionDocumentIdentifier"] == "sess-1"
    assert envelope.rows[0]["subjectCount"] == 2
    assert envelope.rows[0]["speciesName"] == "Caenorhabditis elegans"
    # Strains are joined ", " — all three subjects are N2 so dedupe to one.
    assert envelope.rows[0]["strainName"] == "N2"
    assert envelope.rows[1]["sessionDocumentIdentifier"] == "sess-2"
    assert envelope.rows[1]["subjectCount"] == 1


# ---------------------------------------------------------------------------
# Element grain — probe + location + cell-type split
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_element_grain_projects_location_and_cell_type(
    cloud: NdiCloudClient,
) -> None:
    dataset_id = "DSX"
    async with respx.mock(base_url="https://api.example.test/v1") as router:
        router.get(f"/datasets/{dataset_id}/document-class-counts").respond(
            200,
            json=_counts_raw(
                subject=1, element=1, probe_location=2, openminds_subject=1,
            ),
        )

        def _ndiquery(request: httpx.Request, route: Any) -> httpx.Response:
            body = json.loads(request.content.decode())
            param1 = body["searchstructure"][0]["param1"]
            ids = {
                "element": ["el-1"],
                "subject": ["sub-A"],
                "probe_location": ["pl-uberon", "pl-cl"],
                "openminds_subject": [],
            }.get(param1, [])
            return httpx.Response(200, json=_ndiquery_body(ids))

        router.post("/ndiquery").mock(side_effect=_ndiquery)

        def _bulk(request: httpx.Request, route: Any) -> httpx.Response:
            body = json.loads(request.content.decode())
            requested = body["documentIds"]
            fixtures: dict[str, dict[str, Any]] = {
                "el-1": _element_doc("ndi-el-1", "ndi-sub-A"),
                "sub-A": _subject_doc("ndi-sub-A", "sess-1"),
                "pl-uberon": _probe_location_doc("ndi-el-1"),
                "pl-cl": _cell_type_doc("ndi-el-1"),
            }
            docs = [fixtures[i] for i in requested if i in fixtures]
            return httpx.Response(200, json=_bulk_body(docs))

        router.post(f"/datasets/{dataset_id}/documents/bulk-fetch").mock(
            side_effect=_bulk,
        )

        svc = PivotService(cloud)
        body = await svc.pivot_by_grain(dataset_id, "element", session=None)

    envelope = PivotResponse.model_validate(body)
    assert envelope.grain == "element"
    assert envelope.totalRows == 1
    row = envelope.rows[0]
    assert row["probeDocumentIdentifier"] == "ndi-el-1"
    assert row["probeName"] == "probe-1"
    assert row["probeType"] == "n-trode"
    assert row["probeLocationName"] == "primary visual cortex"
    assert row["probeLocationOntology"] == "uberon:0002436"
    # Cell-type splits out of the probe_location list via CL: prefix.
    assert row["cellTypeName"] == "Type III BNST neuron"
    assert row["cellTypeOntology"] == "CL:0000598"
    assert row["subjectDocumentIdentifier"] == "ndi-sub-A"


# ---------------------------------------------------------------------------
# Invalid grain → typed error
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
@pytest.mark.parametrize(
    "bad_grain",
    [
        "quark",          # Obvious nonsense
        "experiment",     # The deliberately-rejected term (amendment §4.B6e)
        "",               # Empty string — falsy in Python but must still reject
        "SUBJECT",        # Case sensitivity — the Literal is lowercase-only
        "probe",          # A real class name but not a pivot grain
        "session ",       # Trailing whitespace — no implicit normalization
    ],
)
async def test_invalid_grain_raises_validation_failed(
    bad_grain: str, cloud: NdiCloudClient,
) -> None:
    """SUPPORTED_GRAINS is a closed Literal — anything outside it (including
    empty string, case variants, whitespace-padded, and rejected vocabulary
    like ``'experiment'``) must surface as ValidationFailed with the bad
    grain echoed in details. The router uses this to return a typed 400.
    """
    svc = PivotService(cloud)
    with pytest.raises(ValidationFailed) as exc:
        await svc.pivot_by_grain("DSX", bad_grain, session=None)
    assert "Unsupported pivot grain" in exc.value.final_message
    assert exc.value.details is not None
    assert exc.value.details["grain"] == bad_grain


# ---------------------------------------------------------------------------
# Grain with zero docs → 404
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_empty_grain_returns_not_found(cloud: NdiCloudClient) -> None:
    dataset_id = "DSX"
    async with respx.mock(
        base_url="https://api.example.test/v1", assert_all_called=False,
    ) as router:
        # No subjects present.
        router.get(f"/datasets/{dataset_id}/document-class-counts").respond(
            200, json=_counts_raw(stimulus_presentation=3),
        )
        # ndiquery must not be invoked.
        ndiquery = router.post("/ndiquery").respond(
            200, json={"documents": []},
        )
        svc = PivotService(cloud)
        with pytest.raises(NotFound):
            await svc.pivot_by_grain(dataset_id, "subject", session=None)
        assert ndiquery.call_count == 0


# ---------------------------------------------------------------------------
# Cache miss → hit (zero extra cloud calls)
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_cache_miss_then_hit() -> None:
    dataset_id = "DSX"
    redis = fakeredis.aioredis.FakeRedis(decode_responses=True)
    cache = RedisTableCache(redis=redis, ttl_seconds=PIVOT_CACHE_TTL_SECONDS)

    async with respx.mock(
        base_url="https://api.example.test/v1", assert_all_called=False,
    ) as router:
        counts_route = router.get(
            f"/datasets/{dataset_id}/document-class-counts",
        ).respond(200, json=_counts_raw(subject=1, openminds_subject=0))

        def _ndiquery(request: httpx.Request, route: Any) -> httpx.Response:
            body = json.loads(request.content.decode())
            param1 = body["searchstructure"][0]["param1"]
            ids = {"subject": ["sub-A"], "openminds_subject": []}.get(param1, [])
            return httpx.Response(200, json=_ndiquery_body(ids))

        ndiquery_route = router.post("/ndiquery").mock(side_effect=_ndiquery)
        bulk_route = router.post(
            f"/datasets/{dataset_id}/documents/bulk-fetch",
        ).respond(
            200, json=_bulk_body([_subject_doc("ndi-sub-A", "sess-1")]),
        )

        client = NdiCloudClient()
        await client.start()
        try:
            svc = PivotService(client, cache=cache)
            r1 = await svc.pivot_by_grain(dataset_id, "subject", session=None)
            first_counts = counts_route.call_count
            first_ndiquery = ndiquery_route.call_count
            first_bulk = bulk_route.call_count
            r2 = await svc.pivot_by_grain(dataset_id, "subject", session=None)
        finally:
            await client.close()
            await redis.aclose()

    # Counts is always consulted (pre-compute the 404 gate); but ndiquery
    # + bulk-fetch must not re-fire on hit.
    assert counts_route.call_count == first_counts + 1
    assert ndiquery_route.call_count == first_ndiquery
    assert bulk_route.call_count == first_bulk
    # Byte-identical envelope — computedAt frozen from the first compute.
    assert r1 == r2


# ---------------------------------------------------------------------------
# User isolation on cache keys
# ---------------------------------------------------------------------------

def test_cache_keys_isolated_per_user() -> None:
    dataset_id = "DSX"
    alice = _minimal_session("alice")
    bob = _minimal_session("bob")
    assert pivot_cache_key(dataset_id, "subject", alice) != pivot_cache_key(
        dataset_id, "subject", bob,
    )
    assert pivot_cache_key(dataset_id, "subject", None) == (
        f"{PIVOT_KEY_PREFIX}:{dataset_id}:subject:public"
    )
    assert pivot_cache_key(dataset_id, "subject", alice) == (
        f"{PIVOT_KEY_PREFIX}:{dataset_id}:subject:{user_scope_for(alice)}"
    )


def test_grain_cache_keys_are_distinct() -> None:
    """Subject vs session vs element must not collide on the same dataset."""
    ds = "DSX"
    subject_key = pivot_cache_key(ds, "subject", None)
    session_key = pivot_cache_key(ds, "session", None)
    element_key = pivot_cache_key(ds, "element", None)
    assert len({subject_key, session_key, element_key}) == 3


# ---------------------------------------------------------------------------
# Pydantic contract
# ---------------------------------------------------------------------------

def test_supported_grains_constant_matches_literal() -> None:
    assert set(SUPPORTED_GRAINS) == {"subject", "session", "element"}


def test_pivot_response_rejects_unknown_fields() -> None:
    with pytest.raises(Exception):  # noqa: B017 — pydantic.ValidationError variant
        PivotResponse.model_validate({
            "datasetId": "x",
            "grain": "subject",
            "columns": [],
            "rows": [],
            "computedAt": "2026-01-01T00:00:00Z",
            "schemaVersion": "pivot:v1",
            "totalRows": 0,
            "extra": "rejected",
        })


def test_pivot_response_rejects_wrong_schema_version() -> None:
    with pytest.raises(Exception):  # noqa: B017 — pydantic.ValidationError variant
        PivotResponse.model_validate({
            "datasetId": "x",
            "grain": "subject",
            "columns": [],
            "rows": [],
            "computedAt": "2026-01-01T00:00:00Z",
            "schemaVersion": "pivot:v2",  # wrong
            "totalRows": 0,
        })
