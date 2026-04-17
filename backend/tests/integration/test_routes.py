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
            app.state.redis = fake_redis
            app.state.session_store = SessionStore(fake_redis)
            app.state.rate_limiter = RateLimiter(fake_redis)
            app.state.table_cache = RedisTableCache(fake_redis)
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
    client, router = app_and_cloud
    router.get("/datasets/published").respond(
        200, json={"totalNumber": 1, "datasets": [{"id": "d1", "name": "Test"}]},
    )
    r = client.get("/api/datasets/published")
    assert r.status_code == 200
    assert r.json()["datasets"][0]["name"] == "Test"


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
