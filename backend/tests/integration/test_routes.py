"""End-to-end FastAPI routing with respx-mocked cloud + fakeredis."""
from __future__ import annotations

import pytest
import respx
from fastapi.testclient import TestClient

from backend.app import create_app


@pytest.fixture
def app_and_cloud(fake_redis):  # type: ignore[no-untyped-def]
    with respx.mock(base_url="https://api.example.test/v1", assert_all_called=False) as router:
        app = create_app()
        # Inject test fixtures onto app.state by running the lifespan manually.
        with TestClient(app) as client:
            # Override Redis and all Redis-backed state to use fake.
            from backend.auth.session import SessionStore
            from backend.cache.redis_table import RedisTableCache
            from backend.middleware.rate_limit import RateLimiter
            from backend.services.dataset_provenance_service import (
                PROVENANCE_CACHE_TTL_SECONDS,
            )
            from backend.services.dataset_summary_service import (
                SUMMARY_CACHE_TTL_SECONDS,
            )
            from backend.services.facet_service import (
                FACETS_CACHE_TTL_SECONDS,
            )
            from backend.services.pivot_service import PIVOT_CACHE_TTL_SECONDS
            app.state.redis = fake_redis
            app.state.session_store = SessionStore(fake_redis)
            app.state.rate_limiter = RateLimiter(fake_redis)
            app.state.table_cache = RedisTableCache(fake_redis)
            app.state.dataset_summary_cache = RedisTableCache(
                fake_redis, ttl_seconds=SUMMARY_CACHE_TTL_SECONDS,
            )
            app.state.dataset_provenance_cache = RedisTableCache(
                fake_redis, ttl_seconds=PROVENANCE_CACHE_TTL_SECONDS,
            )
            app.state.pivot_cache = RedisTableCache(
                fake_redis, ttl_seconds=PIVOT_CACHE_TTL_SECONDS,
            )
            app.state.facets_cache = RedisTableCache(
                fake_redis, ttl_seconds=FACETS_CACHE_TTL_SECONDS,
            )
            yield client, router


def test_health(app_and_cloud) -> None:  # type: ignore[no-untyped-def]
    client, _ = app_and_cloud
    r = client.get("/api/health")
    assert r.status_code == 200
    assert r.json() == {"status": "ok"}


def test_csrf_endpoint_sets_cookie(app_and_cloud) -> None:  # type: ignore[no-untyped-def]
    client, _ = app_and_cloud
    r = client.get("/api/auth/csrf")
    assert r.status_code == 200
    body = r.json()
    assert "csrfToken" in body
    assert "XSRF-TOKEN" in r.headers.get("set-cookie", "")


def test_me_without_session_returns_401_typed(app_and_cloud) -> None:  # type: ignore[no-untyped-def]
    client, _ = app_and_cloud
    r = client.get("/api/auth/me")
    assert r.status_code == 401
    body = r.json()
    assert body["error"]["code"] == "AUTH_REQUIRED"
    assert body["error"]["recovery"] == "login"


def test_csrf_missing_on_mutation_returns_403_typed(app_and_cloud) -> None:  # type: ignore[no-untyped-def]
    client, _ = app_and_cloud
    r = client.post("/api/query", json={"searchstructure": [], "scope": "public"})
    assert r.status_code == 403
    body = r.json()
    assert body["error"]["code"] == "CSRF_INVALID"


def test_published_datasets_calls_cloud(app_and_cloud) -> None:  # type: ignore[no-untyped-def]
    """Plan B B2: published catalog embeds a compact summary per row.
    When the synthesizer has nothing to work with (unmocked endpoints
    return 404 via respx assert_all_called=False), the row still returns
    with ``summary: null`` — the enricher degrades gracefully so the
    catalog keeps rendering.
    """
    client, router = app_and_cloud
    router.get("/datasets/published").respond(
        200, json={"totalNumber": 1, "datasets": [{"id": "d1", "name": "Test"}]},
    )
    r = client.get("/api/datasets/published")
    assert r.status_code == 200
    body = r.json()
    assert body["datasets"][0]["name"] == "Test"
    # B2: `summary` is always present on the row, even if null.
    assert "summary" in body["datasets"][0]
    # Synth failed (no mocked endpoints) → summary is None rather than propagating.
    assert body["datasets"][0]["summary"] is None


def test_me_with_ua_mismatch_returns_401(app_and_cloud) -> None:  # type: ignore[no-untyped-def]
    """PR-5: UA hash change on a live session → revoke + AUTH_REQUIRED.

    Simulates cookie theft: session cookie valid, but the user-agent header
    differs from the one captured at login. Frontend's existing login-recovery
    flow picks up AUTH_REQUIRED and redirects. No new error code needed.
    """
    client, _ = app_and_cloud
    # Plant a session directly in Redis bound to one UA, then request with another.
    import asyncio

    from backend.auth.session import SessionStore

    store: SessionStore = client.app.state.session_store

    async def _plant():  # type: ignore[no-untyped-def]
        return await store.create(
            user_id="u1",
            email="victim@example.com",
            access_token="at",
            access_token_expires_in_seconds=3600,
            ip="127.0.0.1",
            user_agent="Victim-Browser/1.0",
        )

    session = asyncio.get_event_loop().run_until_complete(_plant())

    # Same cookie, different UA — simulated attacker who lifted the cookie.
    client.cookies.set("session", session.session_id)
    r = client.get("/api/auth/me", headers={"User-Agent": "Attacker/1.0"})
    assert r.status_code == 401
    body = r.json()
    assert body["error"]["code"] == "AUTH_REQUIRED"

    # Session revoked: stolen cookie is now useless even with the right UA.
    async def _get():  # type: ignore[no-untyped-def]
        return await store.get(session.session_id)

    assert asyncio.get_event_loop().run_until_complete(_get()) is None


def test_request_id_echoed(app_and_cloud) -> None:  # type: ignore[no-untyped-def]
    client, _ = app_and_cloud
    r = client.get("/api/health", headers={"X-Request-ID": "test-id-1234"})
    assert r.headers["X-Request-ID"] == "test-id-1234"


def test_security_headers_present(app_and_cloud) -> None:  # type: ignore[no-untyped-def]
    client, _ = app_and_cloud
    r = client.get("/api/health")
    assert r.headers["X-Frame-Options"] == "DENY"
    assert r.headers["X-Content-Type-Options"] == "nosniff"
    assert "Content-Security-Policy" in r.headers


def test_metrics_endpoint(app_and_cloud) -> None:  # type: ignore[no-untyped-def]
    client, _ = app_and_cloud
    # Make a request so metrics have data.
    client.get("/api/health")
    r = client.get("/metrics")
    assert r.status_code == 200
    assert b"ndb_http_requests_total" in r.content


def test_unknown_route_returns_not_found_typed(app_and_cloud) -> None:  # type: ignore[no-untyped-def]
    client, _ = app_and_cloud
    r = client.get("/api/doesnotexist")
    assert r.status_code == 404
    body = r.json()
    assert body["error"]["code"] == "NOT_FOUND"


# ---------------------------------------------------------------------------
# M4a: table cache + ontology endpoint + doc-types alias
# ---------------------------------------------------------------------------

def test_doc_types_alias_calls_class_counts(app_and_cloud) -> None:  # type: ignore[no-untyped-def]
    """Plan §M4a step 5: /doc-types → /class-counts for v1 vocab parity."""
    client, router = app_and_cloud
    router.get("/datasets/DS1/document-class-counts").respond(
        200,
        json={"datasetId": "DS1", "totalDocuments": 3,
              "classCounts": {"subject": 3}},
    )
    r = client.get("/api/datasets/DS1/doc-types")
    assert r.status_code == 200
    assert r.json()["classCounts"] == {"subject": 3}


def test_tables_ontology_endpoint_groups_by_variable_names(
    app_and_cloud,
) -> None:  # type: ignore[no-untyped-def]
    """Ontology table endpoint groups rows that share the same variableNames
    CSV into one TableResponse group."""
    client, router = app_and_cloud

    def make_otr(doc_id: str, data: dict, ndi_suffix: str = "1") -> dict:
        return {
            "id": doc_id,
            "ndiId": f"ndi-{ndi_suffix}",
            "data": {
                "base": {"id": f"ndi-{ndi_suffix}"},
                "document_class": {"class_name": "ontologyTableRow"},
                "ontologyTableRow": {
                    "names": "Image ID,Patch ID,Radius",
                    "variableNames": "MicroscopyImageIdentifier,BacterialPatchIdentifier,BacterialPatchRadius",
                    "ontologyNodes": "EMPTY:0000153,EMPTY:0000123,EMPTY:0000126",
                    "data": data,
                },
            },
        }

    # ndiquery returns IDs (v2's auto-paginator tolerates a single page).
    router.post("/ndiquery").respond(
        200,
        json={
            "number_matches": 2,
            "pageSize": 1000,
            "page": 1,
            "documents": [{"id": "m1"}, {"id": "m2"}],
        },
    )
    router.post("/datasets/DS1/documents/bulk-fetch").respond(
        200,
        json={"documents": [
            make_otr("m1", {
                "MicroscopyImageIdentifier": "3649",
                "BacterialPatchIdentifier": "0011",
                "BacterialPatchRadius": 1.33,
            }, ndi_suffix="1"),
            make_otr("m2", {
                "MicroscopyImageIdentifier": "3650",
                "BacterialPatchIdentifier": "0012",
                "BacterialPatchRadius": 1.41,
            }, ndi_suffix="2"),
        ]},
    )
    r = client.get("/api/datasets/DS1/tables/ontology")
    assert r.status_code == 200
    body = r.json()
    assert "groups" in body
    groups = body["groups"]
    assert len(groups) == 1
    g = groups[0]
    assert g["rowCount"] == 2
    assert g["variableNames"] == [
        "MicroscopyImageIdentifier",
        "BacterialPatchIdentifier",
        "BacterialPatchRadius",
    ]
    assert g["ontologyNodes"] == ["EMPTY:0000153", "EMPTY:0000123", "EMPTY:0000126"]
    # Column definitions include ontologyTerm per column (what the popover reads).
    cols = g["table"]["columns"]
    assert cols[0]["key"] == "MicroscopyImageIdentifier"
    assert cols[0]["ontologyTerm"] == "EMPTY:0000153"
    # Rows preserve values keyed by variableName.
    rows = g["table"]["rows"]
    assert rows[0]["BacterialPatchRadius"] == 1.33


def test_single_class_table_is_redis_cached(app_and_cloud) -> None:  # type: ignore[no-untyped-def]
    """Plan §M4a step 3: same (dataset, class, auth) tuple must hit Redis
    on the second request — proved by zero additional ndiquery calls."""
    client, router = app_and_cloud

    ndiquery_route = router.post("/ndiquery").respond(
        200,
        json={"number_matches": 1, "pageSize": 1000, "page": 1,
              "documents": [{"id": "sub1"}]},
    )
    router.post("/datasets/DS1/documents/bulk-fetch").respond(
        200,
        json={"documents": [{
            "id": "sub1", "ndiId": "ndi-sub1",
            "data": {
                "base": {"id": "ndi-sub1", "session_id": "sess1"},
                "subject": {"local_identifier": "subject-local-id"},
                "document_class": {"class_name": "subject"},
            },
        }]},
    )

    r1 = client.get("/api/datasets/DS1/tables/subject")
    assert r1.status_code == 200, r1.json()
    first_call_count = ndiquery_route.call_count
    assert first_call_count >= 1

    r2 = client.get("/api/datasets/DS1/tables/subject")
    assert r2.status_code == 200
    # Second call served from Redis — no additional cloud hits.
    assert ndiquery_route.call_count == first_call_count, (
        "Second request should hit Redis cache, not re-query the cloud"
    )
    # And the payload is byte-identical.
    assert r1.json() == r2.json()


def test_ontology_endpoint_is_redis_cached(app_and_cloud) -> None:  # type: ignore[no-untyped-def]
    """Ontology table grouping is cached under table:{ds}:ontology:{mode}."""
    client, router = app_and_cloud

    ndiquery_route = router.post("/ndiquery").respond(
        200, json={"number_matches": 0, "pageSize": 1000, "page": 1, "documents": []},
    )
    router.post("/datasets/DS1/documents/bulk-fetch").respond(
        200, json={"documents": []},
    )

    r1 = client.get("/api/datasets/DS1/tables/ontology")
    assert r1.status_code == 200
    first_count = ndiquery_route.call_count
    assert first_count >= 1

    r2 = client.get("/api/datasets/DS1/tables/ontology")
    assert r2.status_code == 200
    assert ndiquery_route.call_count == first_count  # served from cache


def test_required_enrichment_failure_skips_cache(app_and_cloud) -> None:  # type: ignore[no-untyped-def]
    """Plan §M4a step 3: "Skip cache if cloud call fails."

    Required enrichments (e.g. openminds_subject for the subject table) must
    propagate failures so the cache layer skips writes. Otherwise a transient
    cloud blip pins an empty-ontology table into Redis for the full 1h TTL —
    exactly the bug observed on Haley's first post-M7 prod deploy.
    """
    import httpx

    client, router = app_and_cloud

    ndiquery_calls: list[dict] = []

    def _ndiquery(request, route):  # type: ignore[no-untyped-def]
        body = request.content.decode() if request.content else ""
        ndiquery_calls.append({"body": body})
        # First call = subject class (succeeds with 1 doc).
        # Second call = openminds_subject enrichment (500s — simulating a
        # transient cloud failure).
        if "openminds_subject" in body:
            return httpx.Response(500, json={"message": "cloud exploded"})
        return httpx.Response(
            200,
            json={
                "number_matches": 1, "pageSize": 1000, "page": 1,
                "documents": [{"id": "sub1"}],
            },
        )

    router.post("/ndiquery").mock(side_effect=_ndiquery)
    router.post("/datasets/DS1/documents/bulk-fetch").respond(
        200,
        json={"documents": [{
            "id": "sub1", "ndiId": "ndi-sub1",
            "data": {
                "base": {"id": "ndi-sub1", "session_id": "s"},
                "subject": {"local_identifier": "A"},
                "document_class": {"class_name": "subject"},
            },
        }]},
    )
    # First attempt: subject succeeds, openminds_subject fails → build raises
    # RuntimeError, which FastAPI's unhandled-exception handler converts to a
    # typed INTERNAL 500. TestClient default raises server exceptions, so we
    # catch directly to assert on the propagation.
    import pytest
    with pytest.raises(RuntimeError, match="Required enrichment 'openminds_subject'"):
        client.get("/api/datasets/DS1/tables/subject")

    # Second attempt after cloud "recovers": openminds_subject now succeeds.
    # If the failure had been cached under v3 key, this would silently return
    # the empty-enrichment response from attempt #1. Because we refused to
    # cache, the second attempt rebuilds and succeeds.
    def _ndiquery_healthy(request, route):  # type: ignore[no-untyped-def]
        return httpx.Response(
            200,
            json={
                "number_matches": 1, "pageSize": 1000, "page": 1,
                "documents": [{"id": "sub1"}],
            },
        )

    router.post("/ndiquery").mock(side_effect=_ndiquery_healthy)
    r2 = client.get("/api/datasets/DS1/tables/subject")
    assert r2.status_code == 200, r2.json()


def test_published_datasets_embed_compact_summary(app_and_cloud) -> None:  # type: ignore[no-untyped-def]
    """Plan B B2: when the cloud mocks are wired for the synth fanout, the
    published list embeds a fully-populated compact summary per row.
    """
    # ProxyCaches is module-level; other tests may have cached a stale
    # /datasets/published response. Clear before this test so the mock we
    # install is what hits the enricher.
    from backend.cache.ttl import ProxyCaches
    ProxyCaches.datasets_list.clear()

    client, router = app_and_cloud
    router.get("/datasets/published").respond(
        200,
        json={
            "totalNumber": 1,
            "datasets": [{
                "id": "DS42",
                "name": "B2 Catalog Dataset",
                "license": "CC-BY-4.0",
            }],
        },
    )
    # Synth fanout — /datasets/DS42 + /document-class-counts + ndiquery +
    # bulk-fetch. Subjects=1 so the species path fires.
    router.get("/datasets/DS42").respond(
        200,
        json={
            "_id": "DS42",
            "name": "B2 Catalog Dataset",
            "license": "CC-BY-4.0",
            "createdAt": "2025-07-01T00:00:00.000Z",
            "doi": "https://doi.org/10.63884/ds42",
        },
    )
    router.get("/datasets/DS42/document-class-counts").respond(
        200,
        json={
            "datasetId": "DS42",
            "totalDocuments": 2,
            "classCounts": {"subject": 1, "openminds_subject": 1},
        },
    )
    router.post("/ndiquery").respond(
        200,
        json={
            "number_matches": 1, "pageSize": 1000, "page": 1,
            "documents": [{"id": "om-sp"}],
        },
    )
    router.post("/datasets/DS42/documents/bulk-fetch").respond(
        200,
        json={"documents": [{
            "id": "om-sp",
            "ndiId": "ndi-om-sp",
            "data": {
                "base": {"id": "ndi-om-sp"},
                "depends_on": [
                    {"name": "subject_id", "value": "ndi-subj"},
                ],
                "openminds": {
                    "openminds_type": "https://openminds.om-i.org/types/Species",
                    "fields": {
                        "name": "Mus musculus",
                        "preferredOntologyIdentifier": "NCBITaxon:10090",
                    },
                },
            },
        }]},
    )
    r = client.get("/api/datasets/published")
    assert r.status_code == 200, r.json()
    body = r.json()
    row = body["datasets"][0]

    # ─── Raw DatasetRecord fields MUST be preserved alongside `summary` ───
    # The wire-shape extension is strictly additive: pre-existing fields
    # from the cloud's list response pass through unchanged. Regression
    # guard against an enricher that accidentally drops or rewrites them.
    assert row["id"] == "DS42"
    assert row["name"] == "B2 Catalog Dataset"
    assert row["license"] == "CC-BY-4.0"

    # ─── `summary` field is attached and populated ─────────────────────────
    assert row["summary"] is not None
    s = row["summary"]
    assert s["datasetId"] == "DS42"
    assert s["schemaVersion"] == "summary:v1"
    assert s["counts"]["subjects"] == 1
    assert s["counts"]["totalDocuments"] == 2
    assert s["species"][0]["ontologyId"] == "NCBITaxon:10090"
    assert s["citation"]["license"] == "CC-BY-4.0"
    assert s["citation"]["year"] == 2025
    # Compact: no contributors / paperDois / extractionWarnings / probeTypes /
    # strains / sexes / dateRange / totalSizeBytes — confirm absence.
    assert "contributors" not in s["citation"]
    assert "paperDois" not in s["citation"]
    assert "strains" not in s
    assert "probeTypes" not in s
    assert "dateRange" not in s


def test_dataset_summary_returns_synthesized_shape(app_and_cloud) -> None:  # type: ignore[no-untyped-def]
    """End-to-end B1 route: GET /api/datasets/:id/summary composes
    get_dataset + class-counts + ndiquery-fanout + bulk-fetch, returning the
    canonical :class:`DatasetSummary` shape.
    """
    client, router = app_and_cloud

    router.get("/datasets/DS1").respond(
        200,
        json={
            "_id": "DS1",
            "name": "Integration Test Dataset",
            "license": "CC-BY-4.0",
            "doi": "https://doi.org/10.63884/xyz",
            "totalSize": 424242,
            "createdAt": "2025-06-01T00:00:00.000Z",
            "updatedAt": "2026-03-01T00:00:00.000Z",
            "contributors": [
                {"firstName": "Ada", "lastName": "Lovelace",
                 "orcid": "https://orcid.org/0000-0001"},
            ],
            "associatedPublications": [
                {"title": "Paper A", "DOI": "https://doi.org/10.1/abc"},
            ],
        },
    )
    router.get("/datasets/DS1/document-class-counts").respond(
        200,
        json={
            "datasetId": "DS1",
            "totalDocuments": 3,
            "classCounts": {"subject": 1, "session": 1, "openminds_subject": 1},
        },
    )
    router.post("/ndiquery").respond(
        200,
        json={
            "number_matches": 1, "pageSize": 1000, "page": 1,
            "documents": [{"id": "om-sp"}],
        },
    )
    router.post("/datasets/DS1/documents/bulk-fetch").respond(
        200,
        json={"documents": [{
            "id": "om-sp",
            "ndiId": "ndi-om-sp",
            "data": {
                "base": {"id": "ndi-om-sp"},
                "depends_on": [
                    {"name": "subject_id", "value": "ndi-subj"},
                ],
                "openminds": {
                    "openminds_type": "https://openminds.om-i.org/types/Species",
                    "fields": {
                        "name": "Rattus norvegicus",
                        "preferredOntologyIdentifier": "NCBITaxon:10116",
                    },
                },
            },
        }]},
    )

    r = client.get("/api/datasets/DS1/summary")
    assert r.status_code == 200, r.json()
    body = r.json()
    assert body["datasetId"] == "DS1"
    assert body["schemaVersion"] == "summary:v1"
    assert body["counts"]["subjects"] == 1
    assert body["counts"]["sessions"] == 1
    assert body["species"] is not None
    assert body["species"][0]["ontologyId"] == "NCBITaxon:10116"
    assert body["citation"]["license"] == "CC-BY-4.0"
    assert body["citation"]["datasetDoi"] == "https://doi.org/10.63884/xyz"
    assert body["citation"]["paperDois"] == ["https://doi.org/10.1/abc"]
    # Probes/elements were zero — those buckets stay None.
    assert body["brainRegions"] is None
    assert body["probeTypes"] is None


def test_dataset_summary_404_on_unknown_dataset(app_and_cloud) -> None:  # type: ignore[no-untyped-def]
    """Unknown dataset → cloud 404 → typed NOT_FOUND via BrowserError handler."""
    client, router = app_and_cloud
    router.get("/datasets/MISSING").respond(404, json={"error": "not found"})
    # Counts may never be called when the dataset lookup fails first, but the
    # gather initiates both in parallel — respx tolerates that via
    # assert_all_called=False (configured on the shared fixture).
    router.get("/datasets/MISSING/document-class-counts").respond(
        404, json={"error": "not found"},
    )
    r = client.get("/api/datasets/MISSING/summary")
    assert r.status_code == 404
    body = r.json()
    assert body["error"]["code"] == "NOT_FOUND"


def test_list_by_class_paginates_at_cloud_layer(app_and_cloud) -> None:  # type: ignore[no-untyped-def]
    """Regression for the `list_by_class` query-all-then-slice perf bug.

    Haley's openminds_subject class has 9,032 docs. Before the fix, a
    /documents?class=X&pageSize=50 request would pull all 9,032 IDs via
    ndiquery, slice client-side to 50, then bulk-fetch. This test pins
    the post-fix behavior: ndiquery receives the page+pageSize params
    and only the requested slice is bulk-fetched.
    """
    client, router = app_and_cloud

    # Track the query params ndiquery was called with.
    captured: list[dict] = []

    def _ndiquery_handler(request, route):  # type: ignore[no-untyped-def]
        captured.append(dict(request.url.params))
        import httpx
        return httpx.Response(
            200,
            json={
                "number_matches": 9032,
                "pageSize": int(request.url.params.get("pageSize", "1000")),
                "page": int(request.url.params.get("page", "1")),
                "documents": [{"id": "m1"}, {"id": "m2"}],  # just the slice
            },
        )

    router.post("/ndiquery").mock(side_effect=_ndiquery_handler)
    router.post("/datasets/DS1/documents/bulk-fetch").respond(
        200,
        json={"documents": [{"id": "m1", "name": "doc1", "data": {}}]},
    )

    r = client.get("/api/datasets/DS1/documents?class=openminds_subject&pageSize=50&page=3")
    assert r.status_code == 200
    body = r.json()
    # Total reflects the cloud's number_matches, not len(ids) from a
    # full pull. This is the bug fix.
    assert body["total"] == 9032
    assert body["page"] == 3
    assert body["pageSize"] == 50
    # Exactly one ndiquery call — no extra "count" call — with the page
    # params forwarded.
    assert len(captured) == 1, f"expected 1 ndiquery call, got {len(captured)}"
    assert captured[0]["page"] == "3"
    assert captured[0]["pageSize"] == "50"


# ---------------------------------------------------------------------------
# Plan B B6e: grain-selectable pivot (behind FEATURE_PIVOT_V1)
# ---------------------------------------------------------------------------

def test_pivot_subject_returns_envelope_when_flag_enabled(
    app_and_cloud, monkeypatch,
) -> None:  # type: ignore[no-untyped-def]
    """End-to-end B6e: GET /api/datasets/:id/pivot/subject returns the
    canonical ``PivotResponse`` envelope with one row per subject doc.

    Flag is lru-cached via ``get_settings()``; flip via env + cache_clear.
    """
    client, router = app_and_cloud

    monkeypatch.setenv("FEATURE_PIVOT_V1", "true")
    from backend.config import get_settings
    get_settings.cache_clear()
    try:
        router.get("/datasets/DS1/document-class-counts").respond(
            200,
            json={
                "datasetId": "DS1",
                "totalDocuments": 2,
                "classCounts": {"subject": 1, "openminds_subject": 1},
            },
        )
        router.post("/ndiquery").respond(
            200,
            json={
                "number_matches": 1, "pageSize": 1000, "page": 1,
                "documents": [{"id": "sub-A"}],
            },
        )
        router.post("/datasets/DS1/documents/bulk-fetch").respond(
            200,
            json={"documents": [{
                "id": "sub-A", "ndiId": "ndi-sub-A",
                "data": {
                    "base": {
                        "id": "ndi-sub-A",
                        "session_id": "sess-1",
                        "name": "subject-A",
                    },
                    "subject": {"local_identifier": "A@lab.edu"},
                },
            }]},
        )

        r = client.get("/api/datasets/DS1/pivot/subject")
        assert r.status_code == 200, r.json()
        body = r.json()
        assert body["schemaVersion"] == "pivot:v1"
        assert body["grain"] == "subject"
        assert body["totalRows"] == 1
        assert body["rows"][0]["subjectLocalIdentifier"] == "A@lab.edu"
        assert body["rows"][0]["sessionDocumentIdentifier"] == "sess-1"
    finally:
        monkeypatch.delenv("FEATURE_PIVOT_V1", raising=False)
        get_settings.cache_clear()


def test_pivot_returns_503_when_feature_disabled(
    app_and_cloud,
) -> None:  # type: ignore[no-untyped-def]
    """FEATURE_PIVOT_V1 is false by default → 503 service unavailable.

    Frontend hides the pivot nav on this response code.
    """
    client, _ = app_and_cloud
    # Default env — flag is false.
    r = client.get("/api/datasets/DS1/pivot/subject")
    assert r.status_code == 503
    body = r.json()
    # StarletteHTTPException handler maps non-404/400 to Internal with
    # status preserved.
    assert "FEATURE_PIVOT_V1" in body["error"]["message"]


def test_pivot_invalid_grain_returns_400_typed(
    app_and_cloud, monkeypatch,
) -> None:  # type: ignore[no-untyped-def]
    """Unsupported grain → typed VALIDATION_ERROR (400)."""
    client, router = app_and_cloud

    monkeypatch.setenv("FEATURE_PIVOT_V1", "true")
    from backend.config import get_settings
    get_settings.cache_clear()
    try:
        # Counts is consulted first; any non-empty classCounts is fine since
        # the grain check rejects before presence-gate.
        router.get("/datasets/DS1/document-class-counts").respond(
            200,
            json={"datasetId": "DS1", "totalDocuments": 1,
                  "classCounts": {"subject": 1}},
        )
        r = client.get("/api/datasets/DS1/pivot/quark")
        assert r.status_code == 400
        body = r.json()
        assert body["error"]["code"] == "VALIDATION_ERROR"
        assert "quark" in body["error"]["message"]
    finally:
        monkeypatch.delenv("FEATURE_PIVOT_V1", raising=False)
        get_settings.cache_clear()


def test_pivot_empty_grain_returns_404_typed(
    app_and_cloud, monkeypatch,
) -> None:  # type: ignore[no-untyped-def]
    """Grain with zero docs on this dataset → typed NOT_FOUND (404)."""
    client, router = app_and_cloud

    monkeypatch.setenv("FEATURE_PIVOT_V1", "true")
    from backend.config import get_settings
    get_settings.cache_clear()
    try:
        router.get("/datasets/DS1/document-class-counts").respond(
            200,
            json={"datasetId": "DS1", "totalDocuments": 3,
                  "classCounts": {"stimulus_presentation": 3}},
        )
        r = client.get("/api/datasets/DS1/pivot/subject")
        assert r.status_code == 404
        body = r.json()
        assert body["error"]["code"] == "NOT_FOUND"
    finally:
        monkeypatch.delenv("FEATURE_PIVOT_V1", raising=False)
        get_settings.cache_clear()
# Plan B B5: dataset provenance route
# ---------------------------------------------------------------------------

def test_dataset_provenance_returns_aggregated_shape(
    app_and_cloud,
) -> None:  # type: ignore[no-untyped-def]
    """End-to-end B5 route: GET /api/datasets/:id/provenance composes
    get_dataset + /branches + class-counts + per-class ndiquery-fanout +
    bulk-fetch + ndiquery-resolve, emitting a :class:`DatasetProvenance`
    payload.
    """
    import httpx
    client, router = app_and_cloud

    router.get("/datasets/DS1").respond(
        200,
        json={
            "_id": "DS1",
            "name": "Provenance Integration Dataset",
            "branchOf": "DSPARENT",
        },
    )
    router.get("/datasets/DS1/branches").respond(
        200,
        json={"datasets": [{"id": "DSCHILD1"}, {"id": "DSCHILD2"}]},
    )
    router.get("/datasets/DS1/document-class-counts").respond(
        200,
        json={
            "datasetId": "DS1",
            "totalDocuments": 2,
            "classCounts": {"element": 2},
        },
    )

    # ndiquery handler serves BOTH the per-class isa query and the
    # ndiId-resolution query.
    def _ndiquery_handler(request, route):  # type: ignore[no-untyped-def]
        body_json = request.content.decode() if request.content else ""
        import json as _json
        body = _json.loads(body_json)
        op = body["searchstructure"][0]
        if op.get("operation") == "isa" and op.get("param1") == "element":
            return httpx.Response(
                200,
                json={
                    "number_matches": 2, "pageSize": 1000, "page": 1,
                    "documents": [{"id": "el1"}, {"id": "el2"}],
                },
            )
        if (
            op.get("operation") == "exact_string"
            and op.get("field") == "base.id"
        ):
            ndi_id = op["param1"]
            owning = {
                "ndi-target-1": "DSY",
                "ndi-target-2": "DSY",  # same target dataset → deduped
            }.get(ndi_id)
            if owning is None:
                return httpx.Response(
                    200,
                    json={
                        "number_matches": 0, "pageSize": 5, "page": 1,
                        "documents": [],
                    },
                )
            return httpx.Response(
                200,
                json={
                    "number_matches": 1, "pageSize": 5, "page": 1,
                    "documents": [
                        {
                            "id": f"mongo-{ndi_id}",
                            "ndiId": ndi_id,
                            "dataset": owning,
                            "data": {"base": {"id": ndi_id}},
                        },
                    ],
                },
            )
        return httpx.Response(400, json={"error": "unexpected op"})

    router.post("/ndiquery").mock(side_effect=_ndiquery_handler)

    # bulk-fetch returns the two element docs with depends_on entries.
    def _bulk_handler(request, route):  # type: ignore[no-untyped-def]
        import json as _json
        body = _json.loads(request.content.decode())
        by_id = {
            "el1": {
                "id": "el1",
                "ndiId": "ndi-el1",
                "data": {
                    "base": {"id": "ndi-el1"},
                    "depends_on": [
                        {"name": "subject_id", "value": "ndi-target-1"},
                    ],
                },
            },
            "el2": {
                "id": "el2",
                "ndiId": "ndi-el2",
                "data": {
                    "base": {"id": "ndi-el2"},
                    "depends_on": [
                        {"name": "subject_id", "value": "ndi-target-2"},
                    ],
                },
            },
        }
        docs = [by_id[i] for i in body["documentIds"] if i in by_id]
        return httpx.Response(200, json={"documents": docs})

    router.post("/datasets/DS1/documents/bulk-fetch").mock(
        side_effect=_bulk_handler,
    )

    r = client.get("/api/datasets/DS1/provenance")
    assert r.status_code == 200, r.json()
    body = r.json()
    assert body["datasetId"] == "DS1"
    assert body["schemaVersion"] == "provenance:v1"
    assert body["branchOf"] == "DSPARENT"
    assert body["branches"] == ["DSCHILD1", "DSCHILD2"]
    # Two docs both target DSY → one aggregated edge with edgeCount=2.
    assert len(body["documentDependencies"]) == 1
    edge = body["documentDependencies"][0]
    assert edge["sourceDatasetId"] == "DS1"
    assert edge["targetDatasetId"] == "DSY"
    assert edge["viaDocumentClass"] == "element"
    assert edge["edgeCount"] == 2


def test_dataset_provenance_404_on_unknown_dataset(
    app_and_cloud,
) -> None:  # type: ignore[no-untyped-def]
    """Unknown dataset → cloud 404 on GET /datasets/:id → typed NOT_FOUND."""
    client, router = app_and_cloud
    router.get("/datasets/MISSING").respond(404, json={"error": "not found"})
    # /branches is tolerated-failure internal to the service (graceful
    # empty-list downgrade), but the top-level build calls /datasets/:id
    # via asyncio.gather so we stub it with a 404 too.
    router.get("/datasets/MISSING/branches").respond(
        404, json={"error": "not found"},
    )
    router.get("/datasets/MISSING/document-class-counts").respond(
        404, json={"error": "not found"},
    )
    r = client.get("/api/datasets/MISSING/provenance")
    assert r.status_code == 404
    body = r.json()
    assert body["error"]["code"] == "NOT_FOUND"


# ---------------------------------------------------------------------------
# B3: cross-dataset facet aggregation
# ---------------------------------------------------------------------------

def test_facets_endpoint_aggregates_across_published(
    app_and_cloud,
) -> None:  # type: ignore[no-untyped-def]
    """Plan B B3: GET /api/facets walks the published catalog + for each
    dataset builds a full summary, then aggregates distinct ontology-typed
    facets across them all. Mocking two datasets here exercises:
      - catalog walk with totalNumber=2
      - summary fanout (ndiquery + bulk-fetch)
      - dedupe when two datasets share a species ontologyId
    """
    # Clear module-level ProxyCaches so other tests' responses don't leak.
    from backend.cache.ttl import ProxyCaches
    ProxyCaches.datasets_list.clear()
    ProxyCaches.dataset_detail.clear()
    ProxyCaches.class_counts.clear()

    import json as _json

    import httpx

    client, router = app_and_cloud
    router.get("/datasets/published").respond(
        200,
        json={
            "totalNumber": 2,
            "datasets": [
                {"id": "DSA", "name": "Dataset A"},
                {"id": "DSB", "name": "Dataset B"},
            ],
        },
    )
    # Shared per-dataset mocks — both datasets have a single species + probe.
    for dsid in ("DSA", "DSB"):
        router.get(f"/datasets/{dsid}").respond(
            200, json={
                "_id": dsid,
                "name": f"Dataset {dsid}",
                "createdAt": "2025-01-01T00:00:00.000Z",
            },
        )
        router.get(f"/datasets/{dsid}/document-class-counts").respond(
            200, json={
                "datasetId": dsid,
                "totalDocuments": 3,
                "classCounts": {"subject": 1, "openminds_subject": 1, "element": 1},
            },
        )

    # ndiquery: dispatch by class name param.

    def _ndiquery(request: httpx.Request, route) -> httpx.Response:  # type: ignore[no-untyped-def]
        body = _json.loads(request.content.decode())
        param1 = body["searchstructure"][0]["param1"]
        ids = {
            "openminds_subject": ["om-sp"],
            "element": ["el1"],
            "probe_location": [],
        }.get(param1, [])
        return httpx.Response(
            200, json={
                "number_matches": len(ids),
                "pageSize": 1000,
                "page": 1,
                "documents": [{"id": i} for i in ids],
            },
        )

    router.post("/ndiquery").mock(side_effect=_ndiquery)

    # Per-dataset bulk-fetch returns the species + element docs.
    for dsid, probe_type in [("DSA", "patch-Vm"), ("DSB", "stimulator")]:

        def _make_bulk(pt):  # type: ignore[no-untyped-def]
            def _bulk(request, route):  # type: ignore[no-untyped-def]
                body = _json.loads(request.content.decode())
                fixtures = {
                    "om-sp": {
                        "id": "om-sp", "ndiId": "ndi-om-sp",
                        "data": {
                            "base": {"id": "ndi-om-sp"},
                            "depends_on": [{"name": "subject_id", "value": "ndi-subj"}],
                            "openminds": {
                                "openminds_type": "https://openminds.om-i.org/types/Species",
                                "fields": {
                                    "name": "Rattus norvegicus",
                                    "preferredOntologyIdentifier": "NCBITaxon:10116",
                                },
                            },
                        },
                    },
                    "el1": {
                        "id": "el1", "ndiId": "ndi-el1",
                        "data": {
                            "base": {"id": "ndi-el1"},
                            "depends_on": [{"name": "subject_id", "value": "ndi-subj"}],
                            "element": {"name": "probe-1", "type": pt, "reference": 1},
                        },
                    },
                }
                docs = [fixtures[i] for i in body["documentIds"] if i in fixtures]
                return httpx.Response(200, json={"documents": docs})
            return _bulk

        router.post(f"/datasets/{dsid}/documents/bulk-fetch").mock(
            side_effect=_make_bulk(probe_type),
        )

    r = client.get("/api/facets")
    assert r.status_code == 200, r.json()
    body = r.json()
    assert body["schemaVersion"] == "facets:v1"
    # Species dedupe: two datasets, one ontologyId.
    assert len(body["species"]) == 1
    assert body["species"][0]["ontologyId"] == "NCBITaxon:10116"
    # Probe types: two distinct free-text values.
    assert set(body["probeTypes"]) == {"patch-Vm", "stimulator"}
    # datasetCount: 2 contributing.
    assert body["datasetCount"] == 2


def test_facets_empty_catalog_returns_empty_lists(
    app_and_cloud,
) -> None:  # type: ignore[no-untyped-def]
    """An empty published catalog returns a fully-formed FacetsResponse with
    empty lists + datasetCount=0. Doesn't crash; frontend can still render.
    """
    from backend.cache.ttl import ProxyCaches
    ProxyCaches.datasets_list.clear()

    client, router = app_and_cloud
    router.get("/datasets/published").respond(
        200, json={"totalNumber": 0, "datasets": []},
    )
    r = client.get("/api/facets")
    assert r.status_code == 200
    body = r.json()
    assert body["species"] == []
    assert body["brainRegions"] == []
    assert body["strains"] == []
    assert body["sexes"] == []
    assert body["probeTypes"] == []
    assert body["datasetCount"] == 0
    assert body["schemaVersion"] == "facets:v1"
