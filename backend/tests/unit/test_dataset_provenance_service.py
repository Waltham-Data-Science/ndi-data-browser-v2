"""DatasetProvenanceService — Plan B B5 (dataset derivation + depends_on aggregation).

Exercises:

- happy path (parent + children + cross-dataset ``depends_on`` edges)
- ``branchOf=None`` (this is not a branch)
- empty branches (no children)
- empty cross-dataset dependencies (no ``depends_on`` crosses)
- cross-dataset edge dedupe (two docs → same target dataset merged)
- same-dataset ``depends_on`` references are NOT emitted (only cross)
- cache miss → compute → write, then cache hit bypasses the cloud
- user isolation: alice and bob use different cache keys
- ``/branches`` 5xx downgrades to empty branches, not a build failure
- extraction helpers (_branch_of_from_raw, _branch_ids_from_raw,
  _depends_on_ndi_ids, _extract_ids, _classes_to_walk)
"""
from __future__ import annotations

import json
import time
from collections.abc import AsyncIterator
from typing import Any

import fakeredis.aioredis
import httpx
import pytest
import respx
from cryptography.fernet import Fernet
from pydantic import ValidationError

from backend.auth.session import SessionData
from backend.cache.redis_table import RedisTableCache
from backend.clients.ndi_cloud import NdiCloudClient
from backend.services.dataset_provenance_service import (
    PROVENANCE_CACHE_TTL_SECONDS,
    PROVENANCE_KEY_PREFIX,
    DatasetDependencyEdge,
    DatasetProvenance,
    DatasetProvenanceService,
    _branch_ids_from_raw,
    _branch_of_from_raw,
    _classes_to_walk,
    _depends_on_ndi_ids,
    _extract_ids,
    _owning_dataset_id,
    provenance_cache_key,
)


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
async def cloud() -> AsyncIterator[NdiCloudClient]:
    import os
    os.environ.setdefault("SESSION_ENCRYPTION_KEY", Fernet.generate_key().decode())
    client = NdiCloudClient()
    await client.start()
    try:
        yield client
    finally:
        await client.close()


# ---------------------------------------------------------------------------
# Builders
# ---------------------------------------------------------------------------

def _dataset_raw(
    dataset_id: str,
    *,
    branch_of: str | None = None,
    **overrides: Any,
) -> dict[str, Any]:
    base: dict[str, Any] = {"_id": dataset_id, "name": f"Dataset {dataset_id}"}
    if branch_of is not None:
        base["branchOf"] = branch_of
    base.update(overrides)
    return base


def _counts_raw(**class_counts: int) -> dict[str, Any]:
    total = sum(class_counts.values())
    return {
        "datasetId": "DSX",
        "totalDocuments": total,
        "classCounts": class_counts,
    }


def _ndiquery_ids_body(ids: list[str]) -> dict[str, Any]:
    return {
        "number_matches": len(ids),
        "pageSize": 1000,
        "page": 1,
        "documents": [{"id": i} for i in ids],
    }


def _bulk_body(docs: list[dict[str, Any]]) -> dict[str, Any]:
    return {"documents": docs}


def _doc_with_depends_on(
    doc_id: str, ndi_id: str, deps: list[tuple[str, str]],
) -> dict[str, Any]:
    """Mongo doc with a data.depends_on list of (name, value-ndi-id) pairs."""
    return {
        "id": doc_id,
        "ndiId": ndi_id,
        "data": {
            "base": {"id": ndi_id},
            "depends_on": [{"name": n, "value": v} for n, v in deps],
        },
    }


def _resolve_ndi_body(ndi_id: str, owning_dataset: str) -> dict[str, Any]:
    """Response for ``ndiquery exact_string base.id=<ndi_id> scope=public``."""
    return {
        "number_matches": 1,
        "pageSize": 5,
        "page": 1,
        "documents": [
            {
                "id": f"mongo-{ndi_id}",
                "ndiId": ndi_id,
                "dataset": owning_dataset,
                "data": {"base": {"id": ndi_id}},
            },
        ],
    }


# ---------------------------------------------------------------------------
# Happy path — parent + children + cross-dataset depends_on
# ---------------------------------------------------------------------------

def _install_happy_path_routes(
    router: respx.MockRouter,
    dataset_id: str,
    *,
    branch_of: str | None,
    branch_ids: list[str],
    class_docs: dict[str, list[dict[str, Any]]],
    ndi_id_to_dataset: dict[str, str],
) -> None:
    """Install respx routes for a happy-path provenance build.

    ``class_docs`` maps each document class → the list of docs returned for
    that class from bulk-fetch. ``ndi_id_to_dataset`` maps target-resolution
    ndiIds → their owning dataset.
    """
    router.get(f"/datasets/{dataset_id}").respond(
        200, json=_dataset_raw(dataset_id, branch_of=branch_of),
    )
    branches_payload = {
        "datasets": [{"id": b, "name": f"Branch {b}"} for b in branch_ids],
    }
    router.get(f"/datasets/{dataset_id}/branches").respond(
        200, json=branches_payload,
    )
    router.get(
        f"/datasets/{dataset_id}/document-class-counts",
    ).respond(
        200,
        json={
            "datasetId": dataset_id,
            "totalDocuments": sum(len(v) for v in class_docs.values()),
            "classCounts": {c: len(d) for c, d in class_docs.items()},
        },
    )

    # ndiquery handler: two modes.
    #   1) isa=<class> scope=<dataset_id> → return ids for that class
    #   2) exact_string base.id=<ndi_id> scope=public|all → resolve owning
    #      dataset of <ndi_id>.
    def _ndiquery_handler(
        request: httpx.Request, route: Any,
    ) -> httpx.Response:
        body = json.loads(request.content.decode())
        op = body["searchstructure"][0]
        if op["operation"] == "isa":
            cls = op["param1"]
            docs = class_docs.get(cls, [])
            return httpx.Response(
                200, json=_ndiquery_ids_body([d["id"] for d in docs]),
            )
        if op["operation"] == "exact_string" and op.get("field") == "base.id":
            ndi_id = op["param1"]
            owning = ndi_id_to_dataset.get(ndi_id)
            if owning is None:
                return httpx.Response(
                    200,
                    json={
                        "number_matches": 0,
                        "pageSize": 5,
                        "page": 1,
                        "documents": [],
                    },
                )
            return httpx.Response(200, json=_resolve_ndi_body(ndi_id, owning))
        return httpx.Response(400, json={"error": "unexpected operation"})

    router.post("/ndiquery").mock(side_effect=_ndiquery_handler)

    # bulk-fetch handler: return whichever docs were requested by id.
    def _bulk_handler(
        request: httpx.Request, route: Any,
    ) -> httpx.Response:
        body = json.loads(request.content.decode())
        requested = body["documentIds"]
        by_id: dict[str, dict[str, Any]] = {}
        for cls_docs in class_docs.values():
            for d in cls_docs:
                by_id[d["id"]] = d
        docs = [by_id[i] for i in requested if i in by_id]
        return httpx.Response(200, json=_bulk_body(docs))

    router.post(f"/datasets/{dataset_id}/documents/bulk-fetch").mock(
        side_effect=_bulk_handler,
    )


@pytest.mark.asyncio
async def test_happy_path_populates_every_field(cloud: NdiCloudClient) -> None:
    """Parent + 2 children + 3 cross-dataset depends_on edges (2 targets x
    different classes), plus a same-dataset edge that must NOT be emitted.
    """
    dataset_id = "DSX"
    class_docs = {
        # element docs point to subjects in another dataset (DSX -> DSY).
        "element": [
            _doc_with_depends_on(
                "el1", "ndi-el1",
                [("subject_id", "ndi-sub1-in-DSY")],
            ),
            _doc_with_depends_on(
                "el2", "ndi-el2",
                [("subject_id", "ndi-sub2-in-DSY")],
            ),
        ],
        # epoch docs point to an element in a third dataset AND an element
        # in OUR OWN dataset (the latter must be filtered out).
        "element_epoch": [
            _doc_with_depends_on(
                "ep1", "ndi-ep1",
                [
                    ("element_id", "ndi-el-in-DSZ"),
                    ("element_id", "ndi-el1"),  # same dataset → skip
                ],
            ),
        ],
    }
    ndi_id_to_dataset = {
        "ndi-sub1-in-DSY": "DSY",
        "ndi-sub2-in-DSY": "DSY",
        "ndi-el-in-DSZ": "DSZ",
        # ndi-el1 belongs to dataset_id itself → same-dataset, filtered out.
        "ndi-el1": dataset_id,
    }

    async with respx.mock(base_url="https://api.example.test/v1") as router:
        _install_happy_path_routes(
            router, dataset_id,
            branch_of="DSPARENT",
            branch_ids=["DSCHILD1", "DSCHILD2"],
            class_docs=class_docs,
            ndi_id_to_dataset=ndi_id_to_dataset,
        )
        svc = DatasetProvenanceService(cloud)
        prov = await svc.build_provenance(dataset_id, session=None)

    assert isinstance(prov, DatasetProvenance)
    assert prov.datasetId == dataset_id
    assert prov.schemaVersion == "provenance:v1"
    assert prov.branchOf == "DSPARENT"
    assert prov.branches == ["DSCHILD1", "DSCHILD2"]

    # Three edges expected:
    #   DSX --element--> DSY   (2 documents)
    #   DSX --element_epoch--> DSZ (1 document)
    # Same-dataset edge (DSX --> DSX) must NOT appear.
    edge_map = {
        (e.targetDatasetId, e.viaDocumentClass): e.edgeCount
        for e in prov.documentDependencies
    }
    assert edge_map == {
        ("DSY", "element"): 2,
        ("DSZ", "element_epoch"): 1,
    }
    # No DSX->DSX self-edge.
    assert not any(
        e.targetDatasetId == dataset_id for e in prov.documentDependencies
    )


# ---------------------------------------------------------------------------
# branchOf=None (this dataset was not branched)
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_branch_of_absent_yields_null(cloud: NdiCloudClient) -> None:
    dataset_id = "DSX"
    async with respx.mock(base_url="https://api.example.test/v1") as router:
        router.get(f"/datasets/{dataset_id}").respond(
            200, json=_dataset_raw(dataset_id),  # no branchOf
        )
        router.get(f"/datasets/{dataset_id}/branches").respond(
            200, json={"datasets": []},
        )
        router.get(
            f"/datasets/{dataset_id}/document-class-counts",
        ).respond(200, json=_counts_raw())  # no docs

        svc = DatasetProvenanceService(cloud)
        prov = await svc.build_provenance(dataset_id, session=None)

    assert prov.branchOf is None
    assert prov.branches == []
    assert prov.documentDependencies == []


# ---------------------------------------------------------------------------
# Empty branches array (dataset exists but nothing has been forked off it)
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_empty_branches_list(cloud: NdiCloudClient) -> None:
    dataset_id = "DSX"
    async with respx.mock(base_url="https://api.example.test/v1") as router:
        router.get(f"/datasets/{dataset_id}").respond(
            200, json=_dataset_raw(dataset_id, branch_of="DSPARENT"),
        )
        # Older cloud shape: list directly, not wrapped in `{datasets: [...]}`.
        router.get(f"/datasets/{dataset_id}/branches").respond(
            200, json=[],
        )
        router.get(
            f"/datasets/{dataset_id}/document-class-counts",
        ).respond(200, json=_counts_raw())

        svc = DatasetProvenanceService(cloud)
        prov = await svc.build_provenance(dataset_id, session=None)

    assert prov.branchOf == "DSPARENT"
    assert prov.branches == []


# ---------------------------------------------------------------------------
# Empty cross-dataset dependencies (all depends_on refs stay in-dataset)
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_empty_document_dependencies(cloud: NdiCloudClient) -> None:
    dataset_id = "DSX"
    class_docs = {
        "element": [
            _doc_with_depends_on(
                "el1", "ndi-el1", [("subject_id", "ndi-sub1")],
            ),
        ],
    }
    # The one depends_on target resolves back to the same dataset → no cross.
    ndi_id_to_dataset = {"ndi-sub1": dataset_id}

    async with respx.mock(base_url="https://api.example.test/v1") as router:
        _install_happy_path_routes(
            router, dataset_id,
            branch_of=None, branch_ids=[],
            class_docs=class_docs,
            ndi_id_to_dataset=ndi_id_to_dataset,
        )
        svc = DatasetProvenanceService(cloud)
        prov = await svc.build_provenance(dataset_id, session=None)

    assert prov.documentDependencies == []


# ---------------------------------------------------------------------------
# Cross-dataset edge dedup — two docs in the same class pointing at the
# same target dataset collapse to ONE edge with edgeCount=2
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_cross_dataset_edge_dedup(cloud: NdiCloudClient) -> None:
    dataset_id = "DSX"
    class_docs = {
        "element": [
            _doc_with_depends_on(
                "el1", "ndi-el1", [("subject_id", "ndi-sub1")],
            ),
            _doc_with_depends_on(
                "el2", "ndi-el2", [("subject_id", "ndi-sub2")],
            ),
        ],
    }
    # Both target ndiIds resolve to the SAME target dataset.
    ndi_id_to_dataset = {
        "ndi-sub1": "DSY",
        "ndi-sub2": "DSY",
    }

    async with respx.mock(base_url="https://api.example.test/v1") as router:
        _install_happy_path_routes(
            router, dataset_id,
            branch_of=None, branch_ids=[],
            class_docs=class_docs,
            ndi_id_to_dataset=ndi_id_to_dataset,
        )
        svc = DatasetProvenanceService(cloud)
        prov = await svc.build_provenance(dataset_id, session=None)

    # One deduplicated edge with count=2.
    assert len(prov.documentDependencies) == 1
    edge = prov.documentDependencies[0]
    assert edge.sourceDatasetId == dataset_id
    assert edge.targetDatasetId == "DSY"
    assert edge.viaDocumentClass == "element"
    assert edge.edgeCount == 2


# ---------------------------------------------------------------------------
# edgeCount counts DISTINCT target ndiIds, NOT source documents. Two source
# docs pointing at the same target ndiId contribute 1, not 2. This is the
# ndiId-level dedup behavior pinned by the JSDoc/docstring after the initial
# review flagged a semantics-vs-doc mismatch. A regression to document-level
# counting (e.g. summing rather than set-collapsing) would flunk this test.
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_edge_count_is_distinct_target_ndiids_not_source_docs(
    cloud: NdiCloudClient,
) -> None:
    dataset_id = "DSX"
    class_docs = {
        "element": [
            # Two source documents, both pointing at the SAME target ndiId
            # — this is the common case where multiple elements share a
            # probe or a subject reference.
            _doc_with_depends_on(
                "el1", "ndi-el1", [("subject_id", "ndi-shared-sub")],
            ),
            _doc_with_depends_on(
                "el2", "ndi-el2", [("subject_id", "ndi-shared-sub")],
            ),
        ],
    }
    ndi_id_to_dataset = {"ndi-shared-sub": "DSY"}

    async with respx.mock(base_url="https://api.example.test/v1") as router:
        _install_happy_path_routes(
            router, dataset_id,
            branch_of=None, branch_ids=[],
            class_docs=class_docs,
            ndi_id_to_dataset=ndi_id_to_dataset,
        )
        svc = DatasetProvenanceService(cloud)
        prov = await svc.build_provenance(dataset_id, session=None)

    assert len(prov.documentDependencies) == 1
    edge = prov.documentDependencies[0]
    # Two source docs, but only ONE distinct target ndiId → edgeCount=1.
    # A document-level counter would have given edgeCount=2.
    assert edge.edgeCount == 1, (
        f"edgeCount must dedup at the target-ndiId level (expected 1), "
        f"got {edge.edgeCount} — regression to per-document counting?"
    )


# ---------------------------------------------------------------------------
# Unresolvable ndiIds (deleted target doc) just drop out — edge not emitted
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_unresolvable_ndi_ids_are_dropped(cloud: NdiCloudClient) -> None:
    dataset_id = "DSX"
    class_docs = {
        "element": [
            _doc_with_depends_on(
                "el1", "ndi-el1",
                [
                    ("subject_id", "ndi-resolvable"),
                    ("subject_id", "ndi-deleted"),
                ],
            ),
        ],
    }
    # Only one of the two resolves.
    ndi_id_to_dataset = {"ndi-resolvable": "DSY"}

    async with respx.mock(base_url="https://api.example.test/v1") as router:
        _install_happy_path_routes(
            router, dataset_id,
            branch_of=None, branch_ids=[],
            class_docs=class_docs,
            ndi_id_to_dataset=ndi_id_to_dataset,
        )
        svc = DatasetProvenanceService(cloud)
        prov = await svc.build_provenance(dataset_id, session=None)

    # Only the resolvable edge emitted.
    assert len(prov.documentDependencies) == 1
    assert prov.documentDependencies[0].targetDatasetId == "DSY"


# ---------------------------------------------------------------------------
# /branches endpoint failure downgrades to empty list (graceful)
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_branches_endpoint_failure_downgrades_to_empty(
    cloud: NdiCloudClient,
) -> None:
    dataset_id = "DSX"
    async with respx.mock(base_url="https://api.example.test/v1") as router:
        router.get(f"/datasets/{dataset_id}").respond(
            200, json=_dataset_raw(dataset_id, branch_of="DSPARENT"),
        )
        # Simulate /branches not yet deployed or a 5xx blip.
        router.get(f"/datasets/{dataset_id}/branches").respond(
            500, json={"error": "internal"},
        )
        router.get(
            f"/datasets/{dataset_id}/document-class-counts",
        ).respond(200, json=_counts_raw())

        svc = DatasetProvenanceService(cloud)
        prov = await svc.build_provenance(dataset_id, session=None)

    # branchOf survived (it came from /datasets/:id, which was fine).
    assert prov.branchOf == "DSPARENT"
    # branches degraded gracefully.
    assert prov.branches == []


# ---------------------------------------------------------------------------
# Cache: miss → compute → hit (bypasses cloud entirely the second time)
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_cache_hit_bypasses_cloud(cloud: NdiCloudClient) -> None:
    dataset_id = "DSX"
    redis = fakeredis.aioredis.FakeRedis(decode_responses=True)
    try:
        cache = RedisTableCache(
            redis, ttl_seconds=PROVENANCE_CACHE_TTL_SECONDS,
        )

        async with respx.mock(base_url="https://api.example.test/v1") as router:
            detail_route = router.get(f"/datasets/{dataset_id}").respond(
                200, json=_dataset_raw(dataset_id, branch_of=None),
            )
            branches_route = router.get(
                f"/datasets/{dataset_id}/branches",
            ).respond(200, json={"datasets": []})
            counts_route = router.get(
                f"/datasets/{dataset_id}/document-class-counts",
            ).respond(200, json=_counts_raw())

            svc = DatasetProvenanceService(cloud, cache=cache)

            # First call: populates cache.
            prov1 = await svc.build_provenance(dataset_id, session=None)
            first_detail = detail_route.call_count
            first_branches = branches_route.call_count
            first_counts = counts_route.call_count

            # Second call: served from cache, no new cloud calls.
            prov2 = await svc.build_provenance(dataset_id, session=None)
            assert detail_route.call_count == first_detail
            assert branches_route.call_count == first_branches
            assert counts_route.call_count == first_counts

            assert prov1.model_dump() == prov2.model_dump()
    finally:
        await redis.aclose()


# ---------------------------------------------------------------------------
# Cache: user isolation — alice and bob land on different keys
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_user_isolation_in_cache_keys() -> None:
    """Two authenticated users must not share a cached provenance entry."""
    alice = _minimal_session("alice")
    bob = _minimal_session("bob")
    k_alice = provenance_cache_key("DSX", alice)
    k_bob = provenance_cache_key("DSX", bob)
    k_anon = provenance_cache_key("DSX", None)
    assert k_alice != k_bob
    assert k_alice != k_anon
    assert k_bob != k_anon
    assert k_anon == f"{PROVENANCE_KEY_PREFIX}:DSX:public"
    assert k_alice.startswith(f"{PROVENANCE_KEY_PREFIX}:DSX:u:")
    # Same user stable.
    assert provenance_cache_key("DSX", alice) == k_alice


# ---------------------------------------------------------------------------
# Perf smoke — synthetic 500-doc dataset builds in well under 10s
# (stop-condition bound; cloud I/O is mocked so we measure pure CPU +
# asyncio coordination).
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_perf_500_docs_under_10s(cloud: NdiCloudClient) -> None:
    dataset_id = "DSX"
    # 500 element docs, each pointing to a unique ndiId in DSY.
    class_docs = {
        "element": [
            _doc_with_depends_on(
                f"el{i}", f"ndi-el{i}",
                [("subject_id", f"ndi-target-{i}")],
            )
            for i in range(500)
        ],
    }
    ndi_id_to_dataset = {f"ndi-target-{i}": "DSY" for i in range(500)}

    async with respx.mock(base_url="https://api.example.test/v1") as router:
        _install_happy_path_routes(
            router, dataset_id,
            branch_of=None, branch_ids=[],
            class_docs=class_docs,
            ndi_id_to_dataset=ndi_id_to_dataset,
        )
        svc = DatasetProvenanceService(cloud)
        t0 = time.perf_counter()
        prov = await svc.build_provenance(dataset_id, session=None)
        elapsed = time.perf_counter() - t0

    # All 500 edges dedupe into ONE aggregated edge (class=element, target=DSY).
    assert len(prov.documentDependencies) == 1
    assert prov.documentDependencies[0].edgeCount == 500
    # The real bound is "well under 10s" on a 500-doc synthetic. Allow 5s to
    # give CI headroom (mocked HTTP + asyncio scheduling + pydantic).
    assert elapsed < 5.0, f"500-doc provenance took {elapsed:.2f}s (>5s budget)"


# ---------------------------------------------------------------------------
# Extraction helpers
# ---------------------------------------------------------------------------

class TestBranchOfFromRaw:
    def test_string_value_returned(self) -> None:
        assert _branch_of_from_raw({"branchOf": "DSPARENT"}) == "DSPARENT"

    def test_whitespace_is_stripped(self) -> None:
        assert _branch_of_from_raw({"branchOf": "  DSPARENT  "}) == "DSPARENT"

    def test_missing_returns_none(self) -> None:
        assert _branch_of_from_raw({}) is None

    def test_empty_string_returns_none(self) -> None:
        assert _branch_of_from_raw({"branchOf": ""}) is None
        assert _branch_of_from_raw({"branchOf": "   "}) is None

    def test_non_string_returns_none(self) -> None:
        assert _branch_of_from_raw({"branchOf": 123}) is None
        assert _branch_of_from_raw({"branchOf": None}) is None


class TestBranchIdsFromRaw:
    def test_picks_id_key(self) -> None:
        branches = [{"id": "A"}, {"id": "B"}]
        assert _branch_ids_from_raw(branches) == ["A", "B"]

    def test_falls_back_to_underscore_id(self) -> None:
        assert _branch_ids_from_raw([{"_id": "M"}]) == ["M"]

    def test_dedupes_preserving_order(self) -> None:
        assert _branch_ids_from_raw([{"id": "A"}, {"id": "A"}, {"id": "B"}]) == [
            "A", "B",
        ]

    def test_drops_non_dict_and_non_string(self) -> None:
        # Deliberately heterogeneous: _branch_ids_from_raw must survive None
        # and non-string id values without crashing, so we cast through Any
        # to bypass the list-invariant typing.
        heterogeneous: list[Any] = [None, {"id": 123}, {"id": "ok"}]
        assert _branch_ids_from_raw(heterogeneous) == ["ok"]


class TestDependsOnNdiIds:
    def test_list_shape(self) -> None:
        doc = {"data": {"depends_on": [
            {"name": "subject_id", "value": "n1"},
            {"name": "element_id", "value": "n2"},
        ]}}
        assert _depends_on_ndi_ids(doc) == ["n1", "n2"]

    def test_single_dict_shape(self) -> None:
        doc = {"data": {"depends_on": {"name": "subject_id", "value": "n1"}}}
        assert _depends_on_ndi_ids(doc) == ["n1"]

    def test_missing_field(self) -> None:
        assert _depends_on_ndi_ids({}) == []
        assert _depends_on_ndi_ids({"data": {}}) == []
        assert _depends_on_ndi_ids({"data": {"depends_on": None}}) == []

    def test_drops_entries_with_no_value(self) -> None:
        doc = {"data": {"depends_on": [
            {"name": "subject_id", "value": ""},
            {"name": "probe_id"},
            {"name": "session_id", "value": "good"},
        ]}}
        assert _depends_on_ndi_ids(doc) == ["good"]


class TestExtractIds:
    def test_documents_list(self) -> None:
        body = {"documents": [{"id": "a"}, {"id": "b"}]}
        assert _extract_ids(body) == ["a", "b"]

    def test_legacy_ids_list(self) -> None:
        body = {"ids": ["x", "y"]}
        assert _extract_ids(body) == ["x", "y"]

    def test_empty(self) -> None:
        assert _extract_ids({}) == []


class TestClassesToWalk:
    def test_sorted_by_count_desc(self) -> None:
        counts_raw = {
            "classCounts": {"subject": 5, "element": 20, "epoch": 100},
        }
        assert _classes_to_walk(counts_raw) == ["epoch", "element", "subject"]

    def test_unknown_is_skipped(self) -> None:
        counts_raw = {"classCounts": {"element": 5, "unknown": 99}}
        assert _classes_to_walk(counts_raw) == ["element"]

    def test_zero_count_dropped(self) -> None:
        counts_raw = {"classCounts": {"element": 0, "epoch": 5}}
        assert _classes_to_walk(counts_raw) == ["epoch"]

    def test_malformed_input(self) -> None:
        assert _classes_to_walk({}) == []
        assert _classes_to_walk({"classCounts": None}) == []


class TestOwningDatasetId:
    def test_canonical_field(self) -> None:
        assert _owning_dataset_id({"dataset": "DSX"}) == "DSX"

    def test_legacy_fields(self) -> None:
        assert _owning_dataset_id({"datasetId": "DSY"}) == "DSY"
        assert _owning_dataset_id({"dataset_id": "DSZ"}) == "DSZ"

    def test_prefers_canonical_over_legacy(self) -> None:
        assert _owning_dataset_id(
            {"dataset": "DSX", "datasetId": "DSY"},
        ) == "DSX"

    def test_missing_returns_none(self) -> None:
        assert _owning_dataset_id({}) is None
        assert _owning_dataset_id({"dataset": ""}) is None
        assert _owning_dataset_id({"dataset": 123}) is None


# ---------------------------------------------------------------------------
# Pydantic model enforcement — extra=forbid etc.
# ---------------------------------------------------------------------------

def test_dataset_dependency_edge_rejects_extra_fields() -> None:
    with pytest.raises(ValidationError):
        DatasetDependencyEdge(
            sourceDatasetId="A",
            targetDatasetId="B",
            viaDocumentClass="element",
            edgeCount=1,
            rogueField="not allowed",  # type: ignore[call-arg]
        )


def test_dataset_provenance_rejects_extra_fields() -> None:
    with pytest.raises(ValidationError):
        DatasetProvenance(
            datasetId="DSX",
            computedAt="2026-04-17T00:00:00Z",
            rogueField="not allowed",  # type: ignore[call-arg]
        )


def test_edge_count_must_be_ge_1() -> None:
    with pytest.raises(ValidationError):
        DatasetDependencyEdge(
            sourceDatasetId="A",
            targetDatasetId="B",
            viaDocumentClass="element",
            edgeCount=0,
        )
