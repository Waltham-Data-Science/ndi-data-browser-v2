"""End-to-end FastAPI routing with respx-mocked cloud + fakeredis.

The shared ``app_and_cloud`` fixture lives in ``conftest.py`` (pytest
auto-discovers it) so multiple integration test modules can consume it
without re-importing.
"""
from __future__ import annotations

import pytest


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
    """``/api/datasets/published`` returns rows with an embedded
    ``summary`` slot per row (Plan B B2).

    Restore (2026-04-28): the route again calls
    :meth:`DatasetService.list_published_with_summaries`. With no
    per-dataset cloud mocks installed, every row's synthesizer hits a
    404 inside ``_enrich_list_response``, the swallow-and-degrade path
    fires, and each row gets ``summary: null``. The page still returns
    200 — that's the contract: per-row failures must not fail the
    page.

    The per-row ``asyncio.wait_for(5s)`` belt
    (``PER_ROW_SUMMARY_TIMEOUT_SECONDS``) is what makes restoring the
    embed safe; see the route docstring for the full history.
    """
    client, router = app_and_cloud
    router.get("/datasets/published").respond(
        200, json={"totalNumber": 1, "datasets": [{"id": "d1", "name": "Test"}]},
    )
    r = client.get("/api/datasets/published")
    assert r.status_code == 200
    body = r.json()
    assert body["datasets"][0]["name"] == "Test"
    assert body["datasets"][0]["id"] == "d1"
    # B2 restore: every row carries a `summary` slot. The slot is `null`
    # here because no per-dataset cloud mocks are installed — the
    # synthesizer's per-row 404 handling degrades the row gracefully.
    assert "summary" in body["datasets"][0]
    assert body["datasets"][0]["summary"] is None


def test_my_datasets_requires_session(app_and_cloud) -> None:  # type: ignore[no-untyped-def]
    """``/api/datasets/my`` is gated by ``require_session``. An unauthed
    caller must get the typed AUTH_REQUIRED 401, not a leaky 500.
    """
    client, _ = app_and_cloud
    r = client.get("/api/datasets/my")
    assert r.status_code == 401
    body = r.json()
    assert body["error"]["code"] == "AUTH_REQUIRED"
    assert body["error"]["recovery"] == "login"


def test_my_datasets_authenticated_aggregates_across_session_orgs(
    app_and_cloud,
) -> None:  # type: ignore[no-untyped-def]
    """Authenticated ``/api/datasets/my`` fans out
    ``GET /organizations/:orgId/datasets`` for every org stored on the
    caller's session (captured at login from the cloud's
    ``UserWithOrganizationsResult``) and returns the merged list. Every
    row gets a ``summary`` slot (null when the synthesizer can't run
    against the unmocked cloud). The access token is threaded through
    to each per-org call so the cloud's permission filter strips
    anything the caller isn't entitled to see.

    This replaces the pre-2026-04-20 ``/datasets/unpublished`` behaviour,
    which surfaced only the narrow in-review slice and hid the caller's
    actual published work + never-submitted drafts.
    """
    import asyncio

    from backend.auth.session import SessionStore

    client, router = app_and_cloud

    # Plant a live session bound to two orgs. The Bearer-token header
    # assertion below doubles as an auth-forwarding regression test.
    store: SessionStore = client.app.state.session_store

    async def _plant():  # type: ignore[no-untyped-def]
        return await store.create(
            user_id="org-user-1",
            email="wds@example.test",
            access_token="my-access-token",
            access_token_expires_in_seconds=3600,
            ip="127.0.0.1",
            user_agent="testclient",
            organization_ids=["org-alpha", "org-beta"],
            is_admin=False,
        )

    session = asyncio.get_event_loop().run_until_complete(_plant())
    client.cookies.set("session", session.session_id)

    alpha_route = router.get(
        "/organizations/org-alpha/datasets",
        headers={"Authorization": "Bearer my-access-token"},
    ).respond(
        200,
        json={
            "totalNumber": 2,
            "page": 1,
            "pageSize": 100,
            "datasets": [
                {"id": "a1", "name": "Alpha One"},
                {"id": "a2", "name": "Alpha Two (draft)", "isPublished": False},
            ],
        },
    )
    beta_route = router.get(
        "/organizations/org-beta/datasets",
        headers={"Authorization": "Bearer my-access-token"},
    ).respond(
        200,
        json={
            "totalNumber": 1,
            "page": 1,
            "pageSize": 100,
            "datasets": [
                {"id": "b1", "name": "Beta One (published)", "isPublished": True},
            ],
        },
    )

    r = client.get("/api/datasets/my", headers={"User-Agent": "testclient"})
    assert r.status_code == 200
    body = r.json()
    assert body["totalNumber"] == 3  # sum across both orgs
    ids = sorted(d["id"] for d in body["datasets"])
    assert ids == ["a1", "a2", "b1"]
    # Compact-summary slot is always present (null when synth has nothing).
    for row in body["datasets"]:
        assert "summary" in row
        assert row["summary"] is None
    assert alpha_route.called, "org-alpha datasets endpoint should have been hit"
    assert beta_route.called, "org-beta datasets endpoint should have been hit"


def test_my_datasets_with_no_orgs_returns_empty(
    app_and_cloud,
) -> None:  # type: ignore[no-untyped-def]
    """Admin users who aren't enrolled in any org — or anyone whose
    session predates the 2026-04-20 organization_ids capture — see an
    empty list. Better than a 500; the frontend renders an empty-state
    hint explaining that datasets from their orgs will appear here.
    """
    import asyncio

    from backend.auth.session import SessionStore

    client, _ = app_and_cloud

    store: SessionStore = client.app.state.session_store

    async def _plant():  # type: ignore[no-untyped-def]
        return await store.create(
            user_id="orphan-user",
            email="orphan@example.test",
            access_token="orphan-access-token",
            access_token_expires_in_seconds=3600,
            ip="127.0.0.1",
            user_agent="testclient",
            # No orgs — simulates a pre-2026-04-20 session or an
            # unenrolled admin.
            organization_ids=[],
            is_admin=True,
        )

    session = asyncio.get_event_loop().run_until_complete(_plant())
    client.cookies.set("session", session.session_id)

    r = client.get("/api/datasets/my", headers={"User-Agent": "testclient"})
    assert r.status_code == 200
    body = r.json()
    assert body == {"totalNumber": 0, "datasets": []}


def test_my_datasets_scope_all_admin_falls_back_to_legacy_firehose(
    app_and_cloud,
) -> None:  # type: ignore[no-untyped-def]
    """``/api/datasets/my?scope=all`` with an ``is_admin=True`` session
    opts into the legacy cross-org in-review firehose — cloud's
    ``GET /datasets/unpublished`` admin-bypass path. Intended for
    platform-admin debugging (\"what does the old /my show me across
    every org\").

    The org-scoped fan-out endpoints (``/organizations/:orgId/datasets``)
    must NOT be called when ``scope=all`` is honored — that's a
    different branch and we don't want the double-cost.
    """
    import asyncio

    from backend.auth.session import SessionStore

    client, router = app_and_cloud

    store: SessionStore = client.app.state.session_store

    async def _plant():  # type: ignore[no-untyped-def]
        return await store.create(
            user_id="admin-user",
            email="admin@example.test",
            access_token="admin-token",
            access_token_expires_in_seconds=3600,
            ip="127.0.0.1",
            user_agent="testclient",
            organization_ids=["org-alpha"],
            is_admin=True,
        )

    session = asyncio.get_event_loop().run_until_complete(_plant())
    client.cookies.set("session", session.session_id)

    unpublished_route = router.get(
        "/datasets/unpublished",
        headers={"Authorization": "Bearer admin-token"},
    ).respond(
        200,
        json={
            "totalNumber": 2,
            "datasets": [
                {"id": "firehose-1", "name": "Cross-org IR 1", "isPublished": False},
                {"id": "firehose-2", "name": "Cross-org IR 2", "isPublished": False},
            ],
        },
    )

    r = client.get(
        "/api/datasets/my?scope=all",
        headers={"User-Agent": "testclient"},
    )
    assert r.status_code == 200
    body = r.json()
    assert body["totalNumber"] == 2
    ids = sorted(d["id"] for d in body["datasets"])
    assert ids == ["firehose-1", "firehose-2"]
    assert unpublished_route.called, "scope=all admin should hit legacy endpoint"


def test_my_datasets_scope_all_non_admin_silently_downgrades(
    app_and_cloud,
) -> None:  # type: ignore[no-untyped-def]
    """A non-admin caller requesting ``?scope=all`` is silently downgraded
    to the default ``mine`` behaviour. No leak of the admin firehose to
    regular users — we fan out ``/organizations/:orgId/datasets`` like
    the unparameterized case.
    """
    import asyncio

    from backend.auth.session import SessionStore

    client, router = app_and_cloud

    store: SessionStore = client.app.state.session_store

    async def _plant():  # type: ignore[no-untyped-def]
        return await store.create(
            user_id="regular-user",
            email="user@example.test",
            access_token="user-token",
            access_token_expires_in_seconds=3600,
            ip="127.0.0.1",
            user_agent="testclient",
            organization_ids=["org-gamma"],
            is_admin=False,  # key: non-admin
        )

    session = asyncio.get_event_loop().run_until_complete(_plant())
    client.cookies.set("session", session.session_id)

    org_route = router.get("/organizations/org-gamma/datasets").respond(
        200,
        json={
            "totalNumber": 1,
            "datasets": [{"id": "g1", "name": "Gamma One"}],
        },
    )
    # If the admin bypass had leaked, this would be hit. Assert it's NOT.
    leaked_route = router.get("/datasets/unpublished").respond(
        200, json={"totalNumber": 99, "datasets": []},
    )

    r = client.get(
        "/api/datasets/my?scope=all",
        headers={"User-Agent": "testclient"},
    )
    assert r.status_code == 200
    body = r.json()
    assert body["totalNumber"] == 1
    assert body["datasets"][0]["id"] == "g1"
    assert org_route.called, "fanout should still hit the org endpoint"
    assert not leaked_route.called, "non-admin must not reach legacy firehose"


def test_me_includes_organization_ids_and_admin_flag(
    app_and_cloud,
) -> None:  # type: ignore[no-untyped-def]
    """``/api/auth/me`` surfaces the session's cached org IDs + admin
    flag so the frontend can render admin-scoped affordances (filter
    chips, badges) without a second cloud round-trip.
    """
    import asyncio

    from backend.auth.session import SessionStore

    client, _ = app_and_cloud

    store: SessionStore = client.app.state.session_store

    async def _plant():  # type: ignore[no-untyped-def]
        return await store.create(
            user_id="admin-user",
            email="admin@example.test",
            access_token="admin-token",
            access_token_expires_in_seconds=3600,
            ip="127.0.0.1",
            user_agent="testclient",
            organization_ids=["org-alpha"],
            is_admin=True,
        )

    session = asyncio.get_event_loop().run_until_complete(_plant())
    client.cookies.set("session", session.session_id)

    r = client.get("/api/auth/me", headers={"User-Agent": "testclient"})
    assert r.status_code == 200
    body = r.json()
    assert body["organizationIds"] == ["org-alpha"]
    assert body["isAdmin"] is True


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


def test_single_class_pagination_via_query_params(app_and_cloud) -> None:  # type: ignore[no-untyped-def]
    """Stream 5.8 (2026-05-16) — server-side pagination on /tables/{class}.

    Verifies the new envelope ``{page, pageSize, totalRows, hasMore}`` is
    returned when ``?page`` and ``?pageSize`` are provided, AND that page 2
    is served from the same cached full row set as page 1 (zero extra
    cloud calls). This is the 95%-egress-saving invariant.
    """
    client, router = app_and_cloud

    # Build a synthetic 5-subject result so we can paginate page_size=2
    # → 3 pages (rows 0..1, 2..3, 4).
    five_ids = [f"sub{i}" for i in range(5)]
    ndiquery_route = router.post("/ndiquery").respond(
        200,
        json={
            "number_matches": 5,
            "pageSize": 1000,
            "page": 1,
            "documents": [{"id": sid} for sid in five_ids],
        },
    )
    router.post("/datasets/DS1/documents/bulk-fetch").respond(
        200,
        json={
            "documents": [
                {
                    "id": sid,
                    "ndiId": f"ndi-{sid}",
                    "data": {
                        "base": {"id": f"ndi-{sid}", "session_id": "sess"},
                        "subject": {"local_identifier": f"local-{sid}"},
                        "document_class": {"class_name": "subject"},
                    },
                }
                for sid in five_ids
            ],
        },
    )

    # Page 1 — top of the table.
    r1 = client.get("/api/datasets/DS1/tables/subject?page=1&pageSize=2")
    assert r1.status_code == 200, r1.json()
    body1 = r1.json()
    assert body1["page"] == 1
    assert body1["pageSize"] == 2
    assert body1["totalRows"] == 5
    assert body1["hasMore"] is True
    assert len(body1["rows"]) == 2
    # distinct_summary is carried through verbatim (computed on the FULL
    # row set, not the page slice).
    assert "distinct_summary" in body1

    first_call_count = ndiquery_route.call_count

    # Page 2 — middle of the table. Served from cache, no extra cloud hit.
    r2 = client.get("/api/datasets/DS1/tables/subject?page=2&pageSize=2")
    assert r2.status_code == 200
    body2 = r2.json()
    assert body2["page"] == 2
    assert body2["totalRows"] == 5
    assert body2["hasMore"] is True
    assert len(body2["rows"]) == 2
    assert ndiquery_route.call_count == first_call_count, (
        "Page 2 should slice the cached full envelope — no new cloud calls"
    )

    # Page 3 — last (partial) page.
    r3 = client.get("/api/datasets/DS1/tables/subject?page=3&pageSize=2")
    assert r3.status_code == 200
    body3 = r3.json()
    assert body3["page"] == 3
    assert body3["totalRows"] == 5
    assert body3["hasMore"] is False
    assert len(body3["rows"]) == 1


def test_single_class_unpaged_request_keeps_legacy_envelope(
    app_and_cloud,  # type: ignore[no-untyped-def]
) -> None:
    """BC check: unpaged call (no page/pageSize) returns the legacy
    ``{columns, rows, distinct_summary}`` envelope so existing callers
    (Document Explorer's full-set fetch, cron warm-cache) don't break."""
    client, router = app_and_cloud

    router.post("/ndiquery").respond(
        200,
        json={
            "number_matches": 1, "pageSize": 1000, "page": 1,
            "documents": [{"id": "sub1"}],
        },
    )
    router.post("/datasets/DS1/documents/bulk-fetch").respond(
        200,
        json={"documents": [{
            "id": "sub1", "ndiId": "ndi-sub1",
            "data": {
                "base": {"id": "ndi-sub1", "session_id": "sess1"},
                "subject": {"local_identifier": "local-id"},
                "document_class": {"class_name": "subject"},
            },
        }]},
    )

    r = client.get("/api/datasets/DS1/tables/subject")
    assert r.status_code == 200
    body = r.json()
    # Paged fields MUST NOT be present on the unpaged response.
    assert "page" not in body
    assert "pageSize" not in body
    assert "totalRows" not in body
    assert "hasMore" not in body
    # Legacy fields still there.
    assert "columns" in body
    assert "rows" in body


def test_single_class_pagination_rejects_out_of_range_inputs(
    app_and_cloud,  # type: ignore[no-untyped-def]
) -> None:
    """FastAPI Query bounds rejection: page<1, pageSize<1, pageSize>1000
    all surface as 400 Bad Request (the app's request-validation
    middleware remaps pydantic 422 → 400 for consistency with auth +
    body-shape rejections — see existing 400-asserting tests in
    test_auth_proxy.py). Prevents pathological queries from sneaking
    past the safety guard."""
    client, _ = app_and_cloud

    # page=0 violates ge=1.
    r = client.get("/api/datasets/DS1/tables/subject?page=0&pageSize=10")
    assert r.status_code == 400

    # pageSize=0 violates ge=1.
    r = client.get("/api/datasets/DS1/tables/subject?page=1&pageSize=0")
    assert r.status_code == 400

    # pageSize=1001 violates le=1000.
    r = client.get("/api/datasets/DS1/tables/subject?page=1&pageSize=1001")
    assert r.status_code == 400


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
    cause the current build to fail so the cache layer skips writes.
    Otherwise a transient cloud blip pins an empty-ontology table into
    Redis for the full 1h TTL — exactly the bug observed on Haley's first
    post-M7 prod deploy.

    This test asserts the contract at the HTTP response layer, not at the
    Python-exception layer: attempt #1 hits the cloud failure and returns
    5xx with the typed INTERNAL error envelope; attempt #2 (cloud
    recovered) returns 200 with real data. If the failed build had
    poisoned the cache, attempt #2 would silently 200 with empty
    enrichment, which is the bug we're guarding against. Previous
    incarnation of this test relied on TestClient re-raising the
    RuntimeError, but FastAPI's exception_handler(Exception) catches it
    first in current Starlette versions, so we assert on the response
    envelope instead.
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
    # Attempt #1: subject succeeds, openminds_subject fails. The
    # RuntimeError propagates out of TestClient. Depending on the
    # Starlette / anyio / pytest-anyio combination, the exception can
    # surface as either a plain RuntimeError OR a BaseExceptionGroup
    # wrapping it (PEP 654 TaskGroup behavior). We accept both and
    # check the message regardless of wrapping.
    with pytest.raises((RuntimeError, BaseExceptionGroup)) as exc_info:
        client.get("/api/datasets/DS1/tables/subject")

    def _leaves(err: BaseException) -> list[BaseException]:
        if isinstance(err, BaseExceptionGroup):
            out: list[BaseException] = []
            for e in err.exceptions:
                out.extend(_leaves(e))
            return out
        return [err]

    leaves = _leaves(exc_info.value)
    assert any(
        isinstance(e, RuntimeError) and "openminds_subject" in str(e)
        for e in leaves
    ), (
        "Expected the enrichment RuntimeError to propagate. "
        f"Got leaves: {leaves}"
    )

    # Attempt #2 after cloud "recovers": openminds_subject now
    # succeeds. If attempt #1's failure had been cached, we'd still see
    # the empty-enrichment body from attempt #1. Because we refused to
    # cache the failed build, attempt #2 rebuilds from scratch and
    # returns 200.
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


def test_published_datasets_per_row_synth_failure_does_not_fail_the_page(
    app_and_cloud,
) -> None:  # type: ignore[no-untyped-def]
    """Perf regression guard (2026-04-28, replacing the 2026-04-26
    no-fanout guard from PR #97).

    With the embed restored, the route DOES call
    :meth:`DatasetService.list_published_with_summaries`. The invariant
    we now guard is the one that makes restoring the embed safe: when a
    per-row synthesizer fails (404, timeout, exception), the route must
    still return 200 with that row's ``summary`` set to ``null``. A
    single misbehaving row — or all of them — must not fail the page.

    This test exercises the all-fail variant: a 3-row catalog response
    with NO per-dataset cloud mocks installed. Each row's synthesizer
    therefore hits 404 inside ``_enrich_list_response``, the
    swallow-and-degrade path fires (see ``catalog.summary_enrichment_failed``
    log warning), and the response shape is preserved.

    The per-row ``asyncio.wait_for(PER_ROW_SUMMARY_TIMEOUT_SECONDS=5.0)``
    belt in :mod:`backend.services.dataset_service` is what bounds
    worst-case wall clock at ``ceil(N / 3) * 5s`` and prevents the
    90s-pin failure mode that originally drove PR #97. This test
    indirectly validates that path — if the wait_for were removed, this
    test would hang for far longer than the 30s respx default.
    """
    # ProxyCaches is module-level; other tests may have cached a stale
    # /datasets/published response. Clear before this test so the mock we
    # install is what gets returned.
    from backend.cache.ttl import ProxyCaches
    ProxyCaches.datasets_list.clear()

    client, router = app_and_cloud
    list_route = router.get("/datasets/published").respond(
        200,
        json={
            "totalNumber": 3,
            "datasets": [
                {"id": "DS42", "name": "B2 Catalog Dataset", "license": "CC-BY-4.0"},
                {"id": "DS43", "name": "Another", "license": "CC-BY-4.0"},
                {"id": "DS44", "name": "Third", "license": "CC-BY-4.0"},
            ],
        },
    )

    r = client.get("/api/datasets/published")
    assert r.status_code == 200, r.json()
    body = r.json()

    # All three rows present with raw cloud fields preserved.
    assert len(body["datasets"]) == 3
    for row, expected_id in zip(
        body["datasets"], ["DS42", "DS43", "DS44"], strict=True,
    ):
        assert row["id"] == expected_id
        # B2 restore: every row carries a `summary` slot. With no
        # per-dataset mocks installed, the synthesizer's 404 path fires
        # and the slot is `null` — graceful degradation.
        assert "summary" in row, (
            "Regression: the embed restore (2026-04-28) requires every "
            "row to carry a `summary` key, even when null. The frontend "
            "DatasetCard reads it without an `in` check."
        )
        assert row["summary"] is None, (
            "Per-row failures must degrade to summary: null, not raise."
        )

    # The list endpoint hit cloud once. (Cache repopulation also pulls
    # this once per test process; the Clear() above means we see a fresh
    # call_count.)
    assert list_route.call_count == 1


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


# ---------------------------------------------------------------------------
# /api/datasets/:id/documents/:docId/data/raw — PIL-bypass passthrough
# ---------------------------------------------------------------------------
#
# The raw endpoint is the companion to /data/image for headerless raw-uint8
# imageStack files. PIL's Image.open chokes on raw pixel buffers (no PNG/
# JPEG magic), surfacing as BINARY_DECODE_FAILED. /data/raw streams the
# bytes verbatim so the frontend can decode using sidecar metadata from
# the partner imageStack_parameters document.

# The integration tests below exercise the full router → DocumentService →
# cloud.get_document → BinaryService.get_raw → cloud.download_file pipeline
# with respx mocking both the cloud doc-detail call and the S3 download.

# Use a 24-hex-char doc id so DocumentService.detail's _MONGO_OBJECT_ID
# match short-circuits the ndi-id resolver — one fewer cloud call to mock.
_RAW_DOC_ID = "0123456789abcdef01234567"
_RAW_S3_URL = "https://ndi-data.s3.us-east-1.amazonaws.com/imagestack/sig"


def test_raw_returns_octet_stream_with_correct_bytes(
    app_and_cloud,
) -> None:  # type: ignore[no-untyped-def]
    """Happy path. A document with a single file ref → bytes stream
    through unchanged with `application/octet-stream` + Content-Length +
    optional X-NDI-* headers."""
    client, router = app_and_cloud

    raw_payload = bytes(range(256)) * 16  # 4096 bytes — typical raw-uint8 small frame.

    router.get(f"/datasets/DS1/documents/{_RAW_DOC_ID}").respond(
        200,
        json={
            "id": _RAW_DOC_ID,
            "className": "imageStack",
            "files": {
                "file_info": {
                    "name": "stack.bin",
                    "locations": {"location": _RAW_S3_URL},
                },
            },
        },
    )
    # Absolute-URL respx route matches whatever the cloud client sends to
    # the signed S3 URL — same way `cloud.download_file` issues the GET.
    router.get(_RAW_S3_URL).respond(
        200,
        content=raw_payload,
        headers={"Content-Type": "application/octet-stream"},
    )

    r = client.get(f"/api/datasets/DS1/documents/{_RAW_DOC_ID}/data/raw")
    assert r.status_code == 200, r.text
    assert r.headers.get("content-type", "").startswith("application/octet-stream")
    assert r.headers.get("content-length") == str(len(raw_payload))
    assert r.headers.get("x-ndi-doc-id") == _RAW_DOC_ID
    assert r.headers.get("x-ndi-class-name") == "imageStack"
    assert r.content == raw_payload


def test_raw_returns_404_when_document_has_no_file_refs(
    app_and_cloud,
) -> None:  # type: ignore[no-untyped-def]
    """A document with no `files.file_info` shape raises BinaryNotFound,
    which the typed-error handler maps to HTTP 404 with code
    BINARY_NOT_FOUND. No S3 call should be attempted."""
    client, router = app_and_cloud

    router.get(f"/datasets/DS1/documents/{_RAW_DOC_ID}").respond(
        200,
        json={
            "id": _RAW_DOC_ID,
            "className": "imageStack",
            # No files block — represents a partner doc or a malformed
            # imageStack record.
            "files": {},
        },
    )

    r = client.get(f"/api/datasets/DS1/documents/{_RAW_DOC_ID}/data/raw")
    assert r.status_code == 404
    body = r.json()
    assert body["error"]["code"] == "BINARY_NOT_FOUND"


def test_raw_off_allowlist_url_returns_404(
    app_and_cloud,
) -> None:  # type: ignore[no-untyped-def]
    """SSRF defense: if `files.file_info.locations.location` points to a
    host outside the download allowlist (e.g. AWS metadata service or an
    arbitrary attacker host), the cloud client raises BinaryNotFound
    BEFORE issuing any HTTP request. The endpoint inherits this guard
    automatically — no extra defense needed at the router layer."""
    client, router = app_and_cloud

    # NOT on the default allowlist (no s3.amazonaws.com / cloudfront.net
    # suffix). cloud.download_file should reject this before reaching the
    # network — so respx never sees the request, and we don't register a
    # mock for it.
    bad_url = "http://169.254.169.254/latest/meta-data/iam/role"
    router.get(f"/datasets/DS1/documents/{_RAW_DOC_ID}").respond(
        200,
        json={
            "id": _RAW_DOC_ID,
            "className": "imageStack",
            "files": {
                "file_info": {
                    "name": "stack.bin",
                    "locations": {"location": bad_url},
                },
            },
        },
    )

    r = client.get(f"/api/datasets/DS1/documents/{_RAW_DOC_ID}/data/raw")
    # BinaryNotFound bubbles up from cloud.download_file → 404 with the
    # typed code, never a 5xx leak of the SSRF rejection reason.
    assert r.status_code == 404
    body = r.json()
    assert body["error"]["code"] == "BINARY_NOT_FOUND"


# ---------------------------------------------------------------------------
# /data/raw — Range support + Content-Type detection (PR follow-up to #106)
# ---------------------------------------------------------------------------
#
# These tests pin the new behaviors layered on top of the basic passthrough:
#
#   1. Magic-byte sniffing: the endpoint inspects the first ~12 bytes of
#      the payload and sets Content-Type accordingly so HTML5 <video> /
#      <img> work natively. PNG / JPEG / TIFF / MP4 covered, plus the
#      headerless-uint8 fallback to application/octet-stream.
#   2. Range pass-through: a ``Range: bytes=START-END`` header on the
#      request is forwarded to S3 (which honors Range on signed URLs
#      natively), and the proxy returns 206 with the matching
#      Content-Range / Content-Length.
#   3. Backwards compat: a non-Range request still 200s and now also
#      advertises Accept-Ranges: bytes so browsers know to issue Range
#      follow-ups for seek.

# An MP4 head — 4-byte big-endian box size + ASCII ``ftyp`` + brand. Any
# extra bytes are filler payload to bulk out the body so Range slicing has
# something to bite into.
_MP4_HEAD = b"\x00\x00\x00\x18ftypisom"


def test_raw_sets_video_mp4_content_type_from_magic_bytes(
    app_and_cloud,
) -> None:  # type: ignore[no-untyped-def]
    """A payload starting with the MP4 ``ftyp`` magic should surface as
    ``video/mp4`` (not ``application/octet-stream``) so the browser's
    ``<video>`` element will play it without a manual Content-Type
    override."""
    client, router = app_and_cloud
    payload = _MP4_HEAD + (b"\x00" * 512)
    router.get(f"/datasets/DS1/documents/{_RAW_DOC_ID}").respond(
        200,
        json={
            "id": _RAW_DOC_ID,
            "className": "imageStack",
            "files": {
                "file_info": {
                    "name": "movie.mp4",
                    "locations": {"location": _RAW_S3_URL},
                },
            },
        },
    )
    router.get(_RAW_S3_URL).respond(
        200,
        content=payload,
        headers={
            "Content-Type": "application/octet-stream",
            "Content-Length": str(len(payload)),
        },
    )

    r = client.get(f"/api/datasets/DS1/documents/{_RAW_DOC_ID}/data/raw")
    assert r.status_code == 200
    assert r.headers.get("content-type", "").startswith("video/mp4")
    # Accept-Ranges is set on every response so the browser knows to issue
    # Range requests for seek even on a non-Range initial GET.
    assert r.headers.get("accept-ranges") == "bytes"
    assert r.content == payload


def test_raw_sets_image_png_content_type_from_magic_bytes(
    app_and_cloud,
) -> None:  # type: ignore[no-untyped-def]
    """PNG signature → ``image/png``."""
    client, router = app_and_cloud
    payload = b"\x89PNG\r\n\x1a\n" + (b"\x00" * 16)
    router.get(f"/datasets/DS1/documents/{_RAW_DOC_ID}").respond(
        200,
        json={
            "id": _RAW_DOC_ID,
            "className": "imageStack",
            "files": {
                "file_info": {
                    "name": "frame.png",
                    "locations": {"location": _RAW_S3_URL},
                },
            },
        },
    )
    router.get(_RAW_S3_URL).respond(
        200,
        content=payload,
        headers={"Content-Length": str(len(payload))},
    )
    r = client.get(f"/api/datasets/DS1/documents/{_RAW_DOC_ID}/data/raw")
    assert r.status_code == 200
    assert r.headers.get("content-type", "").startswith("image/png")


def test_raw_falls_back_to_octet_stream_for_unknown_magic(
    app_and_cloud,
) -> None:  # type: ignore[no-untyped-def]
    """The original raw-uint8 imageStack use case: pixel bytes with no
    magic header. Must still surface as application/octet-stream so the
    browser doesn't try to render the bytes — the frontend decodes them
    from the partner imageStack_parameters document."""
    client, router = app_and_cloud
    # Headerless raw uint8 — exactly the imageStack shape that motivated
    # /data/raw in the first place.
    payload = bytes(range(256)) * 4
    router.get(f"/datasets/DS1/documents/{_RAW_DOC_ID}").respond(
        200,
        json={
            "id": _RAW_DOC_ID,
            "className": "imageStack",
            "files": {
                "file_info": {
                    "name": "stack.bin",
                    "locations": {"location": _RAW_S3_URL},
                },
            },
        },
    )
    router.get(_RAW_S3_URL).respond(
        200,
        content=payload,
        headers={"Content-Length": str(len(payload))},
    )
    r = client.get(f"/api/datasets/DS1/documents/{_RAW_DOC_ID}/data/raw")
    assert r.status_code == 200
    assert r.headers.get("content-type", "").startswith("application/octet-stream")
    assert r.headers.get("accept-ranges") == "bytes"


def test_raw_range_header_returns_206_with_content_range(
    app_and_cloud,
) -> None:  # type: ignore[no-untyped-def]
    """``Range: bytes=0-99`` → upstream returns 206 with the slice + a
    Content-Range header; the proxy mirrors that back to the client. This
    is the codepath that makes ``<video>`` seek work."""
    client, router = app_and_cloud
    total_size = 50_000
    payload = _MP4_HEAD + (b"X" * (total_size - len(_MP4_HEAD)))
    slice_payload = payload[0:100]  # bytes 0-99 inclusive
    router.get(f"/datasets/DS1/documents/{_RAW_DOC_ID}").respond(
        200,
        json={
            "id": _RAW_DOC_ID,
            "className": "imageStack",
            "files": {
                "file_info": {
                    "name": "movie.mp4",
                    "locations": {"location": _RAW_S3_URL},
                },
            },
        },
    )
    # The upstream S3 mock returns 206 with the standard Range headers.
    # respx matches on URL only — header-based dispatch isn't required;
    # the test ensures the proxy forwards the Range header (it does, via
    # cloud.download_file_range), and trusts respx to behave deterministically.
    router.get(_RAW_S3_URL).respond(
        206,
        content=slice_payload,
        headers={
            "Content-Range": f"bytes 0-99/{total_size}",
            "Content-Length": str(len(slice_payload)),
            "Content-Type": "application/octet-stream",
        },
    )

    r = client.get(
        f"/api/datasets/DS1/documents/{_RAW_DOC_ID}/data/raw",
        headers={"Range": "bytes=0-99"},
    )
    assert r.status_code == 206
    assert r.headers.get("content-range") == f"bytes 0-99/{total_size}"
    assert r.headers.get("content-length") == str(len(slice_payload))
    assert r.headers.get("accept-ranges") == "bytes"
    # MP4 magic is in the slice (bytes 0-7), so sniff still says video/mp4.
    assert r.headers.get("content-type", "").startswith("video/mp4")
    assert r.content == slice_payload


def test_raw_416_from_upstream_returns_typed_400(
    app_and_cloud,
) -> None:  # type: ignore[no-untyped-def]
    """RFC 7233 416 Requested Range Not Satisfiable from S3 → typed
    ValidationFailed (400) at our boundary. The browser learns the range
    was bad without leaking SSRF / allowlist details."""
    client, router = app_and_cloud
    router.get(f"/datasets/DS1/documents/{_RAW_DOC_ID}").respond(
        200,
        json={
            "id": _RAW_DOC_ID,
            "className": "imageStack",
            "files": {
                "file_info": {
                    "name": "movie.mp4",
                    "locations": {"location": _RAW_S3_URL},
                },
            },
        },
    )
    router.get(_RAW_S3_URL).respond(
        416,
        content=b"",
        headers={"Content-Range": "bytes */1024"},
    )
    r = client.get(
        f"/api/datasets/DS1/documents/{_RAW_DOC_ID}/data/raw",
        headers={"Range": "bytes=999999999-"},
    )
    assert r.status_code == 400
    body = r.json()
    assert body["error"]["code"] == "VALIDATION_ERROR"
