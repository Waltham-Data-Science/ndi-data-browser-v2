"""Auth proxy endpoints (B3): signup, forgot-password, reset-password,
confirm-email, resend-confirmation, change-password.

Each endpoint is exercised end-to-end through the FastAPI stack with the
shared ``app_and_cloud`` fixture (defined in conftest.py). The cloud is
mocked via respx; the rate-limiter, CSRF middleware, and error handlers run
for real. These are the regression guards for B3 — anything that drops a
typed error code on the wire or accidentally bypasses CSRF on a mutation
breaks the cutover contract.
"""
from __future__ import annotations

import asyncio

import pytest


def _csrf(client) -> tuple[str, dict[str, str]]:  # type: ignore[no-untyped-def]
    """Mint a CSRF token + return the matching X-XSRF-TOKEN header.

    Mirrors the frontend's ``ensureCsrfToken()`` flow: GET /api/auth/csrf
    seeds the cookie, body returns the same value to echo back. TestClient
    persists the cookie automatically across the next request.
    """
    r = client.get("/api/auth/csrf")
    assert r.status_code == 200
    token = r.json()["csrfToken"]
    return token, {"X-XSRF-TOKEN": token}


# ---------------------------------------------------------------------------
# /api/auth/signup
# ---------------------------------------------------------------------------

def test_signup_happy_path_returns_user(app_and_cloud) -> None:  # type: ignore[no-untyped-def]
    """Successful signup proxies to cloud `POST /users` and returns the user
    payload + an `ok: true` envelope. The endpoint MUST require CSRF (it's
    a mutation); without the token the request fails before reaching the
    handler."""
    client, router = app_and_cloud
    _, csrf_headers = _csrf(client)

    cloud_route = router.post("/users").respond(
        200,
        json={
            "id": "u-new",
            "email": "new@example.test",
            "name": "New User",
            "isValidated": False,
            "organizations": [],
        },
    )
    r = client.post(
        "/api/auth/signup",
        headers=csrf_headers,
        json={
            "email": "new@example.test",
            "password": "GoodPass1!",
            "name": "New User",
        },
    )
    assert r.status_code == 200, r.json()
    body = r.json()
    assert body["ok"] is True
    assert body["user"]["email"] == "new@example.test"
    # Cloud was called exactly once.
    assert cloud_route.called


def test_signup_without_csrf_returns_403_typed(app_and_cloud) -> None:  # type: ignore[no-untyped-def]
    """Signup is a mutation → CSRF middleware must reject without the token."""
    client, _ = app_and_cloud
    r = client.post(
        "/api/auth/signup",
        json={
            "email": "new@example.test",
            "password": "GoodPass1!",
            "name": "New User",
        },
    )
    assert r.status_code == 403
    assert r.json()["error"]["code"] == "CSRF_INVALID"


def test_signup_malformed_body_returns_validation_error(
    app_and_cloud,
) -> None:  # type: ignore[no-untyped-def]
    """Pydantic field constraints reject missing/empty required fields. The
    app's RequestValidationError handler turns this into a typed
    VALIDATION_ERROR (400) rather than the FastAPI default 422."""
    client, _ = app_and_cloud
    _, csrf_headers = _csrf(client)
    r = client.post(
        "/api/auth/signup",
        headers=csrf_headers,
        json={"email": "", "password": ""},  # both empty + name missing
    )
    assert r.status_code == 400
    assert r.json()["error"]["code"] == "VALIDATION_ERROR"


def test_signup_existing_email_returns_typed_error(app_and_cloud) -> None:  # type: ignore[no-untyped-def]
    """Cognito UsernameExistsException → 409 EMAIL_ALREADY_EXISTS."""
    client, router = app_and_cloud
    _, csrf_headers = _csrf(client)
    router.post("/users").respond(
        400, json={"errors": "x", "code": "UsernameExistsException"},
    )
    r = client.post(
        "/api/auth/signup",
        headers=csrf_headers,
        json={
            "email": "dup@example.test",
            "password": "GoodPass1!",
            "name": "Dup",
        },
    )
    assert r.status_code == 409
    body = r.json()
    assert body["error"]["code"] == "EMAIL_ALREADY_EXISTS"
    # Cognito name MUST NOT be on the wire.
    assert "UsernameExistsException" not in body["error"]["message"]


def test_signup_weak_password_returns_typed_error(app_and_cloud) -> None:  # type: ignore[no-untyped-def]
    """Cognito InvalidPasswordException → 400 WEAK_PASSWORD."""
    client, router = app_and_cloud
    _, csrf_headers = _csrf(client)
    router.post("/users").respond(
        400, json={"errors": "x", "code": "InvalidPasswordException"},
    )
    r = client.post(
        "/api/auth/signup",
        headers=csrf_headers,
        json={"email": "weak@example.test", "password": "abc", "name": "Weak"},
    )
    assert r.status_code == 400
    assert r.json()["error"]["code"] == "WEAK_PASSWORD"


def test_signup_rate_limit_returns_429_typed(
    app_and_cloud, monkeypatch,
) -> None:  # type: ignore[no-untyped-def]
    """Per-IP rate limit (default 5/15min) — sixth attempt within the
    window returns AUTH_RATE_LIMITED (429). The cloud must NOT be called
    on the rejected attempt."""
    monkeypatch.setenv("RATE_LIMIT_LOGIN_PER_IP_15MIN", "2")
    from backend.config import get_settings
    get_settings.cache_clear()
    try:
        client, router = app_and_cloud
        _, csrf_headers = _csrf(client)
        cloud_route = router.post("/users").respond(
            400, json={"errors": "x", "code": "UsernameExistsException"},
        )
        # Burn through the budget (limit=2).
        for _ in range(2):
            client.post(
                "/api/auth/signup",
                headers=csrf_headers,
                json={
                    "email": "burn@example.test",
                    "password": "GoodPass1!",
                    "name": "Burn",
                },
            )
        # Third attempt — rejected before reaching the cloud.
        cloud_calls_before = cloud_route.call_count
        r = client.post(
            "/api/auth/signup",
            headers=csrf_headers,
            json={
                "email": "burn@example.test",
                "password": "GoodPass1!",
                "name": "Burn",
            },
        )
        assert r.status_code == 429
        assert r.json()["error"]["code"] == "AUTH_RATE_LIMITED"
        assert cloud_route.call_count == cloud_calls_before, (
            "rate-limited request must not reach the cloud"
        )
    finally:
        monkeypatch.delenv("RATE_LIMIT_LOGIN_PER_IP_15MIN", raising=False)
        get_settings.cache_clear()


# ---------------------------------------------------------------------------
# /api/auth/forgot-password
# ---------------------------------------------------------------------------

def test_forgot_password_happy_path(app_and_cloud) -> None:  # type: ignore[no-untyped-def]
    client, router = app_and_cloud
    _, csrf_headers = _csrf(client)
    cloud_route = router.post("/auth/password/forgot").respond(
        200,
        json={
            "CodeDeliveryDetails": {
                "Destination": "u***@example.test",
                "DeliveryMedium": "EMAIL",
                "AttributeName": "email",
            },
        },
    )
    r = client.post(
        "/api/auth/forgot-password",
        headers=csrf_headers,
        json={"email": "user@example.test"},
    )
    assert r.status_code == 200, r.json()
    assert r.json()["ok"] is True
    assert cloud_route.called


def test_forgot_password_unknown_email_still_returns_ok(app_and_cloud) -> None:  # type: ignore[no-untyped-def]
    """SECURITY: enumeration resistance. Unknown emails MUST get the same
    ``ok: true`` response so an attacker can't probe which addresses are
    registered."""
    client, router = app_and_cloud
    _, csrf_headers = _csrf(client)
    router.post("/auth/password/forgot").respond(
        400, json={"errors": "x", "code": "UserNotFoundException"},
    )
    r = client.post(
        "/api/auth/forgot-password",
        headers=csrf_headers,
        json={"email": "ghost@example.test"},
    )
    assert r.status_code == 200, r.json()
    assert r.json()["ok"] is True


def test_forgot_password_without_csrf_returns_403(app_and_cloud) -> None:  # type: ignore[no-untyped-def]
    client, _ = app_and_cloud
    r = client.post(
        "/api/auth/forgot-password",
        json={"email": "user@example.test"},
    )
    assert r.status_code == 403
    assert r.json()["error"]["code"] == "CSRF_INVALID"


def test_forgot_password_malformed_body_returns_validation_error(
    app_and_cloud,
) -> None:  # type: ignore[no-untyped-def]
    client, _ = app_and_cloud
    _, csrf_headers = _csrf(client)
    r = client.post(
        "/api/auth/forgot-password",
        headers=csrf_headers,
        json={},  # missing email
    )
    assert r.status_code == 400
    assert r.json()["error"]["code"] == "VALIDATION_ERROR"


def test_forgot_password_cloud_unreachable_returns_typed(app_and_cloud) -> None:  # type: ignore[no-untyped-def]
    """Network failure → 502 CLOUD_UNREACHABLE."""
    import httpx
    client, router = app_and_cloud
    _, csrf_headers = _csrf(client)
    router.post("/auth/password/forgot").mock(
        side_effect=httpx.ConnectError("refused"),
    )
    r = client.post(
        "/api/auth/forgot-password",
        headers=csrf_headers,
        json={"email": "user@example.test"},
    )
    assert r.status_code == 502
    assert r.json()["error"]["code"] == "CLOUD_UNREACHABLE"


# ---------------------------------------------------------------------------
# /api/auth/reset-password
# ---------------------------------------------------------------------------

def test_reset_password_happy_path(app_and_cloud) -> None:  # type: ignore[no-untyped-def]
    client, router = app_and_cloud
    _, csrf_headers = _csrf(client)
    cloud_route = router.post("/auth/password/confirm").respond(
        200, json={"message": "Password reset", "code": "Success!"},
    )
    r = client.post(
        "/api/auth/reset-password",
        headers=csrf_headers,
        json={
            "email": "user@example.test",
            "code": "123456",
            "newPassword": "NewGood1!",
        },
    )
    assert r.status_code == 200, r.json()
    assert r.json()["ok"] is True
    assert cloud_route.called


def test_reset_password_wrong_code_returns_typed_error(app_and_cloud) -> None:  # type: ignore[no-untyped-def]
    """The cloud QUIRK: failure rides on a 200 with ``code: CodeMismatchException``
    in the body. The proxy translates that to 400 INVALID_VERIFICATION_CODE."""
    client, router = app_and_cloud
    _, csrf_headers = _csrf(client)
    router.post("/auth/password/confirm").respond(
        200,
        json={"errors": "Unable to reset password", "code": "CodeMismatchException"},
    )
    r = client.post(
        "/api/auth/reset-password",
        headers=csrf_headers,
        json={
            "email": "user@example.test",
            "code": "WRONG1",
            "newPassword": "NewGood1!",
        },
    )
    assert r.status_code == 400
    assert r.json()["error"]["code"] == "INVALID_VERIFICATION_CODE"


def test_reset_password_expired_code_returns_typed_error(app_and_cloud) -> None:  # type: ignore[no-untyped-def]
    client, router = app_and_cloud
    _, csrf_headers = _csrf(client)
    router.post("/auth/password/confirm").respond(
        200,
        json={"errors": "x", "code": "ExpiredCodeException"},
    )
    r = client.post(
        "/api/auth/reset-password",
        headers=csrf_headers,
        json={
            "email": "user@example.test",
            "code": "OLD123",
            "newPassword": "NewGood1!",
        },
    )
    assert r.status_code == 400
    assert r.json()["error"]["code"] == "VERIFICATION_CODE_EXPIRED"


def test_reset_password_without_csrf_returns_403(app_and_cloud) -> None:  # type: ignore[no-untyped-def]
    client, _ = app_and_cloud
    r = client.post(
        "/api/auth/reset-password",
        json={
            "email": "user@example.test",
            "code": "123456",
            "newPassword": "NewGood1!",
        },
    )
    assert r.status_code == 403
    assert r.json()["error"]["code"] == "CSRF_INVALID"


def test_reset_password_malformed_body_returns_validation_error(
    app_and_cloud,
) -> None:  # type: ignore[no-untyped-def]
    client, _ = app_and_cloud
    _, csrf_headers = _csrf(client)
    r = client.post(
        "/api/auth/reset-password",
        headers=csrf_headers,
        json={"email": "user@example.test"},  # missing code + newPassword
    )
    assert r.status_code == 400
    assert r.json()["error"]["code"] == "VALIDATION_ERROR"


# ---------------------------------------------------------------------------
# /api/auth/confirm-email
# ---------------------------------------------------------------------------

def test_confirm_email_happy_path(app_and_cloud) -> None:  # type: ignore[no-untyped-def]
    client, router = app_and_cloud
    _, csrf_headers = _csrf(client)
    cloud_route = router.post("/auth/verify").respond(
        200, json={"id": "u1", "email": "new@example.test", "isValidated": True},
    )
    r = client.post(
        "/api/auth/confirm-email",
        headers=csrf_headers,
        json={"email": "new@example.test", "code": "123456"},
    )
    assert r.status_code == 200, r.json()
    assert r.json()["ok"] is True
    assert cloud_route.called


def test_confirm_email_wrong_code_returns_typed_error(app_and_cloud) -> None:  # type: ignore[no-untyped-def]
    client, router = app_and_cloud
    _, csrf_headers = _csrf(client)
    router.post("/auth/verify").respond(
        400, json={"errors": "x", "code": "CodeMismatchException"},
    )
    r = client.post(
        "/api/auth/confirm-email",
        headers=csrf_headers,
        json={"email": "new@example.test", "code": "WRONG1"},
    )
    assert r.status_code == 400
    assert r.json()["error"]["code"] == "INVALID_VERIFICATION_CODE"


def test_confirm_email_already_confirmed_returns_typed_error(
    app_and_cloud,
) -> None:  # type: ignore[no-untyped-def]
    """NotAuthorizedException ('User cannot be confirmed. Current status is
    CONFIRMED') → 409 EMAIL_ALREADY_VERIFIED."""
    client, router = app_and_cloud
    _, csrf_headers = _csrf(client)
    router.post("/auth/verify").respond(
        400, json={"errors": "x", "code": "NotAuthorizedException"},
    )
    r = client.post(
        "/api/auth/confirm-email",
        headers=csrf_headers,
        json={"email": "done@example.test", "code": "123456"},
    )
    assert r.status_code == 409
    assert r.json()["error"]["code"] == "EMAIL_ALREADY_VERIFIED"


def test_confirm_email_without_csrf_returns_403(app_and_cloud) -> None:  # type: ignore[no-untyped-def]
    client, _ = app_and_cloud
    r = client.post(
        "/api/auth/confirm-email",
        json={"email": "new@example.test", "code": "123456"},
    )
    assert r.status_code == 403
    assert r.json()["error"]["code"] == "CSRF_INVALID"


def test_confirm_email_malformed_body_returns_validation_error(
    app_and_cloud,
) -> None:  # type: ignore[no-untyped-def]
    client, _ = app_and_cloud
    _, csrf_headers = _csrf(client)
    r = client.post(
        "/api/auth/confirm-email",
        headers=csrf_headers,
        json={"email": "new@example.test"},  # missing code
    )
    assert r.status_code == 400
    assert r.json()["error"]["code"] == "VALIDATION_ERROR"


# ---------------------------------------------------------------------------
# /api/auth/resend-confirmation
# ---------------------------------------------------------------------------

def test_resend_confirmation_happy_path(app_and_cloud) -> None:  # type: ignore[no-untyped-def]
    client, router = app_and_cloud
    _, csrf_headers = _csrf(client)
    cloud_route = router.post("/auth/confirmation/resend").respond(
        200, json={"confirmationResent": True},
    )
    r = client.post(
        "/api/auth/resend-confirmation",
        headers=csrf_headers,
        json={"email": "new@example.test"},
    )
    assert r.status_code == 200, r.json()
    assert r.json()["ok"] is True
    assert cloud_route.called


def test_resend_confirmation_already_verified_returns_typed_error(
    app_and_cloud,
) -> None:  # type: ignore[no-untyped-def]
    """InvalidParameterException → 409 EMAIL_ALREADY_VERIFIED."""
    client, router = app_and_cloud
    _, csrf_headers = _csrf(client)
    router.post("/auth/confirmation/resend").respond(
        400, json={"errors": "x", "code": "InvalidParameterException"},
    )
    r = client.post(
        "/api/auth/resend-confirmation",
        headers=csrf_headers,
        json={"email": "done@example.test"},
    )
    assert r.status_code == 409
    assert r.json()["error"]["code"] == "EMAIL_ALREADY_VERIFIED"


def test_resend_confirmation_unknown_email_still_returns_ok(
    app_and_cloud,
) -> None:  # type: ignore[no-untyped-def]
    """SECURITY: enumeration resistance, same as forgot-password."""
    client, router = app_and_cloud
    _, csrf_headers = _csrf(client)
    router.post("/auth/confirmation/resend").respond(
        400, json={"errors": "x", "code": "UserNotFoundException"},
    )
    r = client.post(
        "/api/auth/resend-confirmation",
        headers=csrf_headers,
        json={"email": "ghost@example.test"},
    )
    assert r.status_code == 200
    assert r.json()["ok"] is True


def test_resend_confirmation_without_csrf_returns_403(app_and_cloud) -> None:  # type: ignore[no-untyped-def]
    client, _ = app_and_cloud
    r = client.post(
        "/api/auth/resend-confirmation",
        json={"email": "new@example.test"},
    )
    assert r.status_code == 403
    assert r.json()["error"]["code"] == "CSRF_INVALID"


def test_resend_confirmation_malformed_body_returns_validation_error(
    app_and_cloud,
) -> None:  # type: ignore[no-untyped-def]
    client, _ = app_and_cloud
    _, csrf_headers = _csrf(client)
    r = client.post(
        "/api/auth/resend-confirmation",
        headers=csrf_headers,
        json={},
    )
    assert r.status_code == 400
    assert r.json()["error"]["code"] == "VALIDATION_ERROR"


# ---------------------------------------------------------------------------
# Per-IP rate limits — fan out across the four "anyone can hit" endpoints
# (signup is covered above with bucket="signup-ip"). Each unauthenticated
# endpoint gets its OWN bucket so abusing one doesn't lock out the others
# and so dashboards can attribute rejections to the right flow.
# ---------------------------------------------------------------------------

@pytest.mark.parametrize(
    ("path", "body", "bucket_label"),
    [
        ("/api/auth/forgot-password", {"email": "x@example.test"}, "pwreset-ip"),
        (
            "/api/auth/reset-password",
            {"email": "x@example.test", "code": "123456", "newPassword": "NewGood1!"},
            "pwreset-confirm-ip",
        ),
        (
            "/api/auth/confirm-email",
            {"email": "x@example.test", "code": "123456"},
            "verify-ip",
        ),
        (
            "/api/auth/resend-confirmation",
            {"email": "x@example.test"},
            "verify-resend-ip",
        ),
    ],
)
def test_unauth_endpoints_have_per_ip_rate_limit(
    app_and_cloud, monkeypatch, path, body, bucket_label,
) -> None:  # type: ignore[no-untyped-def]
    """Each unauthenticated endpoint uses a distinct bucket so metrics can
    differentiate, but they all share the same per-IP envelope. Burn the
    quota and confirm the next call returns 429 + AUTH_RATE_LIMITED."""
    # bucket_label is asserted indirectly: the rate limiter uses
    # ``ratelimit:<bucket>:<subject>`` keys; if every endpoint shared the
    # ``login-ip`` bucket, hitting forgot-password would burn the budget
    # for confirm-email — this test only burns budget for ONE endpoint
    # then verifies the OTHERS still work. We start each test from a
    # fresh fakeredis (the fake_redis fixture is function-scoped).
    del bucket_label  # asserted in test_unauth_endpoints_use_distinct_buckets
    monkeypatch.setenv("RATE_LIMIT_LOGIN_PER_IP_15MIN", "2")
    from backend.config import get_settings
    get_settings.cache_clear()
    try:
        client, router = app_and_cloud
        _, csrf_headers = _csrf(client)
        # Wire benign cloud responses for whichever endpoint we're hitting.
        router.post("/auth/password/forgot").respond(
            200, json={"CodeDeliveryDetails": {"Destination": "x"}},
        )
        router.post("/auth/password/confirm").respond(
            200, json={"message": "Password reset", "code": "Success!"},
        )
        router.post("/auth/verify").respond(200, json={"id": "u1"})
        router.post("/auth/confirmation/resend").respond(
            200, json={"confirmationResent": True},
        )
        # Burn the budget of 2.
        for _ in range(2):
            client.post(path, headers=csrf_headers, json=body)
        r = client.post(path, headers=csrf_headers, json=body)
        assert r.status_code == 429, r.json()
        assert r.json()["error"]["code"] == "AUTH_RATE_LIMITED"
    finally:
        monkeypatch.delenv("RATE_LIMIT_LOGIN_PER_IP_15MIN", raising=False)
        get_settings.cache_clear()


def test_unauth_endpoints_use_distinct_buckets(
    app_and_cloud, monkeypatch,
) -> None:  # type: ignore[no-untyped-def]
    """If buckets were shared, burning forgot-password would also lock out
    signup — that would be terrible UX (legitimate user mistypes their
    email twice during password reset, suddenly cannot sign up either).
    This test pins per-endpoint isolation: burn forgot-password's budget,
    then confirm signup still works."""
    monkeypatch.setenv("RATE_LIMIT_LOGIN_PER_IP_15MIN", "1")
    from backend.config import get_settings
    get_settings.cache_clear()
    try:
        client, router = app_and_cloud
        _, csrf_headers = _csrf(client)
        router.post("/auth/password/forgot").respond(
            200, json={"CodeDeliveryDetails": {"Destination": "x"}},
        )
        router.post("/users").respond(
            200,
            json={
                "id": "u-new", "email": "new@example.test",
                "name": "New", "isValidated": False, "organizations": [],
            },
        )
        # Burn forgot-password budget (1 request → next is rejected).
        client.post(
            "/api/auth/forgot-password",
            headers=csrf_headers,
            json={"email": "burn@example.test"},
        )
        r_burned = client.post(
            "/api/auth/forgot-password",
            headers=csrf_headers,
            json={"email": "burn@example.test"},
        )
        assert r_burned.status_code == 429
        # Signup still works because it's a different bucket.
        r_signup = client.post(
            "/api/auth/signup",
            headers=csrf_headers,
            json={
                "email": "fresh@example.test",
                "password": "GoodPass1!",
                "name": "Fresh",
            },
        )
        assert r_signup.status_code == 200, r_signup.json()
    finally:
        monkeypatch.delenv("RATE_LIMIT_LOGIN_PER_IP_15MIN", raising=False)
        get_settings.cache_clear()


# ---------------------------------------------------------------------------
# /api/auth/change-password (B3 close-out)
# ---------------------------------------------------------------------------
#
# Distinct from /api/auth/reset-password (the forgot-password follow-up
# that takes an emailed code). Change-password is the AUTHENTICATED flow
# — the user is signed in and proving they still know the current
# password before rotating to a new one. Two-factor verification:
# session cookie + password proof. This protects against an attacker
# with a stolen XSRF cookie but no password from silently rotating
# creds.
#
# The cloud-side endpoint is `POST /auth/password` with
# `{ oldPassword, newPassword }` and a Bearer token. NotAuthorizedException
# at this endpoint means "old password is wrong" (NOT "email already
# verified", which is the meaning at confirm-email — see the bespoke
# translation in NdiCloudClient.change_password).
#
# Per-USER rate limit (RATE_LIMIT_LOGIN_PER_USER_HOUR), not per-IP, since
# the user is authenticated.

def _plant_session(client, *, user_id="u1", email="u1@example.test"):  # type: ignore[no-untyped-def]
    """Mint a live session bound to the test client's cookie jar.

    Mirrors the helper in test_routes.py — the auth dependencies look up
    the session by cookie and the cloud client gets the bearer when the
    handler passes it through.
    """
    from backend.auth.session import SessionStore
    store: SessionStore = client.app.state.session_store

    async def _plant():  # type: ignore[no-untyped-def]
        return await store.create(
            user_id=user_id,
            email=email,
            access_token="my-access-token",
            access_token_expires_in_seconds=3600,
            ip="127.0.0.1",
            user_agent="testclient",
            organization_ids=[],
            is_admin=False,
        )

    session = asyncio.get_event_loop().run_until_complete(_plant())
    client.cookies.set("session", session.session_id)
    return session


def test_change_password_happy_path(app_and_cloud) -> None:  # type: ignore[no-untyped-def]
    """Authenticated user with valid CSRF + correct old password rotates
    to a new one. Cloud is called with `{oldPassword, newPassword}` and
    the session's bearer token. Response is `{ok: true}`."""
    client, router = app_and_cloud
    _plant_session(client)
    _, csrf_headers = _csrf(client)

    cloud_route = router.post(
        "/auth/password",
        headers={"Authorization": "Bearer my-access-token"},
    ).respond(200, json={"message": "Password changed successfully"})

    r = client.post(
        "/api/auth/change-password",
        headers=csrf_headers,
        json={"currentPassword": "OldGood1!", "newPassword": "NewGood2!"},
    )
    assert r.status_code == 200, r.json()
    assert r.json() == {"ok": True}
    assert cloud_route.called
    # Verify the wire body matches the cloud's expected camelCase
    # `oldPassword`/`newPassword` (NOT the frontend's
    # `currentPassword`/`newPassword`).
    sent = cloud_route.calls.last.request.content
    import json as _json
    sent_body = _json.loads(sent)
    assert sent_body == {"oldPassword": "OldGood1!", "newPassword": "NewGood2!"}


def test_change_password_without_session_returns_401_typed(
    app_and_cloud,
) -> None:  # type: ignore[no-untyped-def]
    """No session cookie → require_session dep raises AUTH_REQUIRED before
    the handler runs. CSRF is irrelevant on the unauth path."""
    client, _ = app_and_cloud
    _, csrf_headers = _csrf(client)
    r = client.post(
        "/api/auth/change-password",
        headers=csrf_headers,
        json={"currentPassword": "OldGood1!", "newPassword": "NewGood2!"},
    )
    assert r.status_code == 401
    assert r.json()["error"]["code"] == "AUTH_REQUIRED"


def test_change_password_without_csrf_returns_403_typed(
    app_and_cloud,
) -> None:  # type: ignore[no-untyped-def]
    """Authenticated session but no CSRF → middleware rejects before the
    handler. Mutating endpoint, no exemption."""
    client, _ = app_and_cloud
    _plant_session(client)
    r = client.post(
        "/api/auth/change-password",
        json={"currentPassword": "OldGood1!", "newPassword": "NewGood2!"},
    )
    assert r.status_code == 403
    assert r.json()["error"]["code"] == "CSRF_INVALID"


def test_change_password_wrong_old_password_returns_typed_401(
    app_and_cloud,
) -> None:  # type: ignore[no-untyped-def]
    """Cognito returns NotAuthorizedException when the old password is
    wrong. At THIS endpoint NotAuthorizedException means "wrong password"
    (NOT "user already confirmed" — that's the confirm-email meaning).
    Surface as AUTH_INVALID_CREDENTIALS so the form can highlight the
    current-password field instead of the new-password one."""
    client, router = app_and_cloud
    _plant_session(client)
    _, csrf_headers = _csrf(client)
    router.post("/auth/password").respond(
        400,
        json={"errors": "Unable to authenticate", "code": "NotAuthorizedException"},
    )
    r = client.post(
        "/api/auth/change-password",
        headers=csrf_headers,
        json={"currentPassword": "WrongOld!", "newPassword": "NewGood2!"},
    )
    assert r.status_code == 401, r.json()
    assert r.json()["error"]["code"] == "AUTH_INVALID_CREDENTIALS"


def test_change_password_weak_new_password_returns_typed_400(
    app_and_cloud,
) -> None:  # type: ignore[no-untyped-def]
    """Cognito's InvalidPasswordException maps to WEAK_PASSWORD so the
    frontend can highlight the new-password field with the policy hint."""
    client, router = app_and_cloud
    _plant_session(client)
    _, csrf_headers = _csrf(client)
    router.post("/auth/password").respond(
        400,
        json={
            "errors": "Unable to change password",
            "code": "InvalidPasswordException",
        },
    )
    r = client.post(
        "/api/auth/change-password",
        headers=csrf_headers,
        json={"currentPassword": "OldGood1!", "newPassword": "weak"},
    )
    assert r.status_code == 400, r.json()
    assert r.json()["error"]["code"] == "WEAK_PASSWORD"


def test_change_password_malformed_body_returns_validation_error(
    app_and_cloud,
) -> None:  # type: ignore[no-untyped-def]
    """Empty fields fail Pydantic's `min_length=1` constraint and surface
    as the app's typed VALIDATION_ERROR (400) rather than 422."""
    client, _ = app_and_cloud
    _plant_session(client)
    _, csrf_headers = _csrf(client)
    r = client.post(
        "/api/auth/change-password",
        headers=csrf_headers,
        json={"currentPassword": "", "newPassword": ""},
    )
    assert r.status_code == 400
    assert r.json()["error"]["code"] == "VALIDATION_ERROR"


def test_change_password_per_user_rate_limit(
    app_and_cloud, monkeypatch,
) -> None:  # type: ignore[no-untyped-def]
    """Burn the per-user budget; the next call returns AUTH_RATE_LIMITED.
    The bucket is `change-password-user`; subject is the user_id (not the
    IP) so a shared NAT or office WiFi doesn't burn another user's quota
    on this authenticated path."""
    monkeypatch.setenv("RATE_LIMIT_LOGIN_PER_USER_HOUR", "2")
    from backend.config import get_settings
    get_settings.cache_clear()
    try:
        client, router = app_and_cloud
        _plant_session(client)
        _, csrf_headers = _csrf(client)
        router.post("/auth/password").respond(
            200, json={"message": "Password changed successfully"},
        )
        # Burn the budget of 2.
        for _ in range(2):
            client.post(
                "/api/auth/change-password",
                headers=csrf_headers,
                json={
                    "currentPassword": "OldGood1!",
                    "newPassword": "NewGood2!",
                },
            )
        r = client.post(
            "/api/auth/change-password",
            headers=csrf_headers,
            json={"currentPassword": "OldGood1!", "newPassword": "NewGood2!"},
        )
        assert r.status_code == 429, r.json()
        assert r.json()["error"]["code"] == "AUTH_RATE_LIMITED"
    finally:
        monkeypatch.delenv("RATE_LIMIT_LOGIN_PER_USER_HOUR", raising=False)
        get_settings.cache_clear()
