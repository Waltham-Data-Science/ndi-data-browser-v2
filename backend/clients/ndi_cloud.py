"""Typed async client for ndi-cloud-node.

One `httpx.AsyncClient` (HTTP/2, keep-alive), retry with exponential backoff + jitter
on network errors and 5xx, and a shared circuit breaker per instance.

Every method takes an optional Bearer access token and maps cloud responses into
typed Pydantic models or raises a BrowserError subclass.
"""
from __future__ import annotations

import asyncio
import random
import time
from typing import Any

import httpx
from pydantic import BaseModel

from ..config import Settings, get_settings
from ..errors import (
    BulkFetchTooLarge,
    CloudInternalError,
    CloudTimeout,
    CloudUnreachable,
    Forbidden,
    NotFound,
    QueryInvalidNegation,
    QueryTimeout,
    QueryTooLarge,
    ValidationFailed,
)
from ..observability.logging import get_logger
from ..observability.metrics import (
    cloud_call_duration_seconds,
    cloud_call_total,
    cloud_retries_total,
    query_timeout_total,
)
from .circuit_breaker import CircuitBreaker, CircuitOpen

log = get_logger(__name__)

BULK_FETCH_MAX = 500
UNAUTHED_RETRYABLE_STATUSES = {500, 502, 503, 504}


class CloudAuthResult(BaseModel):
    access_token: str
    refresh_token: str | None = None
    expires_in_seconds: int = 3600
    user: dict[str, Any] | None = None


class NdiCloudClient:
    """Singleton-style client. Create once at app startup; close at shutdown."""

    def __init__(self, settings: Settings | None = None) -> None:
        self.settings = settings or get_settings()
        self._client: httpx.AsyncClient | None = None
        self.breaker = CircuitBreaker(
            threshold=self.settings.CLOUD_CIRCUIT_BREAKER_THRESHOLD,
            cooldown_seconds=float(self.settings.CLOUD_CIRCUIT_BREAKER_COOLDOWN_SECONDS),
        )

    async def start(self) -> None:
        if self._client is None:
            limits = httpx.Limits(
                max_connections=self.settings.CLOUD_POOL_SIZE,
                max_keepalive_connections=self.settings.CLOUD_POOL_SIZE,
                keepalive_expiry=30.0,
            )
            self._client = httpx.AsyncClient(
                base_url=self.settings.cloud_base_url,
                http2=True,
                timeout=httpx.Timeout(self.settings.CLOUD_HTTP_TIMEOUT_SECONDS),
                limits=limits,
                headers={"Accept-Encoding": "gzip", "User-Agent": "ndi-data-browser-v2/2.0"},
            )

    async def close(self) -> None:
        if self._client is not None:
            await self._client.aclose()
            self._client = None

    @property
    def client(self) -> httpx.AsyncClient:
        if self._client is None:
            raise RuntimeError("NdiCloudClient.start() was not called")
        return self._client

    # --- Core request plumbing ---

    async def _request(
        self,
        method: str,
        url: str,
        *,
        endpoint_label: str,
        access_token: str | None = None,
        json: Any | None = None,
        params: dict[str, Any] | None = None,
        request_id: str | None = None,
        idempotent: bool = True,
    ) -> httpx.Response:
        await self.breaker.before_call()
        headers: dict[str, str] = {}
        if access_token:
            headers["Authorization"] = f"Bearer {access_token}"
        if request_id:
            headers["X-Request-ID"] = request_id

        attempts = self.settings.CLOUD_MAX_RETRIES if idempotent else 1
        last_exc: Exception | None = None
        start = time.perf_counter()
        outcome = "unknown"
        response: httpx.Response | None = None

        for attempt in range(attempts):
            try:
                response = await self.client.request(
                    method=method,
                    url=url,
                    headers=headers,
                    json=json,
                    params=params,
                )
                # Retry only on 5xx for idempotent methods.
                if idempotent and response.status_code in UNAUTHED_RETRYABLE_STATUSES and attempt + 1 < attempts:
                    cloud_retries_total.labels(endpoint=endpoint_label).inc()
                    await asyncio.sleep(self._backoff_seconds(attempt))
                    continue
                break
            except httpx.TimeoutException as e:
                last_exc = e
                if attempt + 1 < attempts:
                    cloud_retries_total.labels(endpoint=endpoint_label).inc()
                    await asyncio.sleep(self._backoff_seconds(attempt))
                    continue
                await self.breaker.record_failure()
                cloud_call_duration_seconds.labels(endpoint=endpoint_label).observe(
                    time.perf_counter() - start,
                )
                cloud_call_total.labels(endpoint=endpoint_label, outcome="timeout").inc()
                log.warning("cloud.timeout", endpoint=endpoint_label, attempt=attempt + 1)
                raise CloudTimeout(log_context={"endpoint": endpoint_label}) from e
            except (httpx.NetworkError, httpx.ConnectError, httpx.RemoteProtocolError) as e:
                last_exc = e
                if attempt + 1 < attempts:
                    cloud_retries_total.labels(endpoint=endpoint_label).inc()
                    await asyncio.sleep(self._backoff_seconds(attempt))
                    continue
                await self.breaker.record_failure()
                cloud_call_duration_seconds.labels(endpoint=endpoint_label).observe(
                    time.perf_counter() - start,
                )
                cloud_call_total.labels(endpoint=endpoint_label, outcome="network").inc()
                log.warning("cloud.network_error", endpoint=endpoint_label, error=str(e))
                raise CloudUnreachable(log_context={"endpoint": endpoint_label}) from e
            except CircuitOpen:
                cloud_call_total.labels(endpoint=endpoint_label, outcome="breaker_open").inc()
                raise CloudUnreachable("NDI Cloud is temporarily unavailable.")

        assert response is not None, "Unreachable: loop exits with either response or exception"
        elapsed = time.perf_counter() - start
        cloud_call_duration_seconds.labels(endpoint=endpoint_label).observe(elapsed)

        if 200 <= response.status_code < 300:
            outcome = "success"
            await self.breaker.record_success()
        elif response.status_code in UNAUTHED_RETRYABLE_STATUSES:
            outcome = "server_error"
            await self.breaker.record_failure()
        else:
            outcome = "client_error"
            # 4xx doesn't trip the breaker (it's user/content, not cloud health)

        cloud_call_total.labels(endpoint=endpoint_label, outcome=outcome).inc()
        return response

    @staticmethod
    def _backoff_seconds(attempt: int) -> float:
        base = 0.25 * (2 ** attempt)
        return base + random.uniform(0, base)  # full jitter

    @staticmethod
    def _raise_for_status(response: httpx.Response, *, endpoint: str) -> None:
        if response.status_code < 400:
            return
        try:
            body = response.json()
        except Exception:
            body = None
        if response.status_code == 401:
            # Upper layers handle the refresh/re-login distinction.
            raise _UpstreamUnauthorized()
        if response.status_code == 403:
            raise Forbidden(log_context={"endpoint": endpoint})
        if response.status_code == 404:
            raise NotFound(log_context={"endpoint": endpoint})
        if response.status_code == 400:
            detail_msg = _extract_detail(body)
            # Detect the specific cloud-side negation rejection.
            if detail_msg and "~or" in detail_msg.lower():
                raise QueryInvalidNegation()
            raise ValidationFailed(
                f"NDI Cloud rejected the request: {detail_msg or 'bad request'}",
                details={"cloud_detail": detail_msg},
            )
        if response.status_code in (408, 504):
            raise CloudTimeout(log_context={"endpoint": endpoint})
        if response.status_code in UNAUTHED_RETRYABLE_STATUSES:
            raise CloudInternalError(log_context={"endpoint": endpoint, "status": response.status_code})
        raise CloudInternalError(log_context={"endpoint": endpoint, "status": response.status_code})

    # --- Endpoints ---

    async def login(self, email: str, password: str) -> CloudAuthResult:
        """Cloud API expects {email, password}, returns {token, user}."""
        resp = await self._request(
            "POST",
            "/auth/login",
            endpoint_label="auth_login",
            json={"email": email, "password": password},
            idempotent=False,
        )
        if resp.status_code in (401, 404):
            from ..errors import AuthInvalidCredentials
            raise AuthInvalidCredentials()
        self._raise_for_status(resp, endpoint="auth_login")
        data = resp.json()
        return _auth_from_cloud(data)

    async def refresh(self, refresh_token: str) -> CloudAuthResult:
        """ndi-cloud-node does NOT currently expose a refresh endpoint.

        Raise AuthExpired so the caller deletes the session and triggers re-login.
        If Steve ships /auth/refresh later, the body format will likely be
        {refreshToken} and we swap this no-op out.
        """
        from ..errors import AuthExpired
        del refresh_token
        raise AuthExpired("Session expired — no refresh endpoint available.")

    async def logout(self, access_token: str) -> None:
        try:
            resp = await self._request(
                "POST",
                "/auth/logout",
                endpoint_label="auth_logout",
                access_token=access_token,
                json={},
                idempotent=False,
            )
            # Ignore non-2xx on logout — we'll clear our session anyway.
            if resp.status_code >= 400:
                log.info("cloud.logout_non_2xx", status=resp.status_code)
        except (CloudUnreachable, CloudTimeout) as e:
            log.info("cloud.logout_network_error", error=str(e))

    async def get_published_datasets(
        self, *, page: int = 1, page_size: int = 20, access_token: str | None = None,
    ) -> dict[str, Any]:
        resp = await self._request(
            "GET",
            "/datasets/published",
            endpoint_label="datasets_published",
            params={"page": page, "pageSize": page_size},
            access_token=access_token,
        )
        self._raise_for_status(resp, endpoint="datasets_published")
        return resp.json()

    async def get_my_datasets(self, *, access_token: str) -> dict[str, Any]:
        resp = await self._request(
            "GET",
            "/datasets/unpublished",
            endpoint_label="datasets_mine",
            access_token=access_token,
        )
        self._raise_for_status(resp, endpoint="datasets_mine")
        return resp.json()

    async def get_dataset(self, dataset_id: str, *, access_token: str | None = None) -> dict[str, Any]:
        resp = await self._request(
            "GET",
            f"/datasets/{dataset_id}",
            endpoint_label="dataset_detail",
            access_token=access_token,
        )
        self._raise_for_status(resp, endpoint="dataset_detail")
        return resp.json()

    async def get_document_class_counts(
        self, dataset_id: str, *, access_token: str | None = None,
    ) -> dict[str, Any]:
        resp = await self._request(
            "GET",
            f"/datasets/{dataset_id}/document-class-counts",
            endpoint_label="document_class_counts",
            access_token=access_token,
        )
        self._raise_for_status(resp, endpoint="document_class_counts")
        return resp.json()

    async def get_document(
        self, dataset_id: str, document_id: str, *, access_token: str | None = None,
    ) -> dict[str, Any]:
        resp = await self._request(
            "GET",
            f"/datasets/{dataset_id}/documents/{document_id}",
            endpoint_label="document_detail",
            access_token=access_token,
        )
        self._raise_for_status(resp, endpoint="document_detail")
        return resp.json()

    async def bulk_fetch(
        self,
        dataset_id: str,
        document_ids: list[str],
        *,
        access_token: str | None = None,
    ) -> list[dict[str, Any]]:
        if not document_ids:
            return []
        if len(document_ids) > BULK_FETCH_MAX:
            raise BulkFetchTooLarge(
                f"You can fetch at most {BULK_FETCH_MAX} documents at a time.",
                details={"max_batch_size": BULK_FETCH_MAX, "requested": len(document_ids)},
            )
        resp = await self._request(
            "POST",
            f"/datasets/{dataset_id}/documents/bulk-fetch",
            endpoint_label="bulk_fetch",
            json={"documentIds": document_ids},
            access_token=access_token,
        )
        self._raise_for_status(resp, endpoint="bulk_fetch")
        body = resp.json()
        return list(body.get("documents", []))

    async def ndiquery(
        self,
        *,
        searchstructure: list[dict[str, Any]],
        scope: str,
        access_token: str | None = None,
        page: int = 1,
        page_size: int = 1000,
        fetch_all: bool = True,
        max_total: int = 50_000,
    ) -> dict[str, Any]:
        """scope: 'public' | 'private' | 'all' | 'csv of objectids'.

        The cloud paginates ndiquery (default pageSize=20). We request large
        pages and auto-loop to assemble up to `max_total`, then return a merged
        response: `{documents: [...], totalItems: N, page, pageSize}`.
        """
        scope_kind = _scope_kind(scope)
        all_docs: list[Any] = []
        current_page = page
        total_items: int | None = None
        start = time.perf_counter()

        while True:
            try:
                resp = await self._request(
                    "POST",
                    "/ndiquery",
                    endpoint_label="ndiquery",
                    json={"searchstructure": searchstructure, "scope": scope},
                    params={"page": current_page, "pageSize": page_size},
                    access_token=access_token,
                )
            except CloudTimeout:
                query_timeout_total.labels(scope_kind=scope_kind).inc()
                raise QueryTimeout() from None
            self._raise_for_status(resp, endpoint="ndiquery")
            body = resp.json()
            page_docs = body.get("documents") or body.get("ids") or []
            all_docs.extend(page_docs)
            # ndi-cloud-node uses `number_matches` for this endpoint.
            if total_items is None:
                total_items = int(body.get("number_matches") or body.get("totalItems") or len(page_docs))
            if not fetch_all:
                break
            if len(all_docs) >= max_total:
                raise QueryTooLarge(
                    f"Matched {total_items} documents. Please narrow your query.",
                    details={"count": total_items},
                )
            if len(page_docs) < page_size or (total_items and len(all_docs) >= total_items):
                break
            current_page += 1

        from ..observability.metrics import query_execution_duration_seconds
        query_execution_duration_seconds.labels(scope_kind=scope_kind).observe(
            time.perf_counter() - start,
        )
        if total_items is not None and total_items > max_total:
            raise QueryTooLarge(
                f"Matched {total_items} documents. Please narrow your query.",
                details={"count": total_items},
            )
        return {
            "documents": all_docs,
            "totalItems": total_items if total_items is not None else len(all_docs),
            "page": page,
            "pageSize": page_size,
        }

    async def download_file(
        self, url: str, *, access_token: str | None = None,
    ) -> bytes:
        """Download a signed file URL (S3 or similar). Returns raw bytes."""
        headers = {"Accept-Encoding": "gzip"}
        if access_token:
            headers["Authorization"] = f"Bearer {access_token}"
        try:
            resp = await self.client.get(url, headers=headers, timeout=60.0)
        except httpx.TimeoutException as e:
            raise CloudTimeout("Binary download timed out.") from e
        except httpx.NetworkError as e:
            raise CloudUnreachable("Could not reach binary storage.") from e
        if resp.status_code == 404:
            from ..errors import BinaryNotFound
            raise BinaryNotFound()
        if resp.status_code >= 400:
            raise CloudInternalError(f"Binary download failed (HTTP {resp.status_code})")
        return resp.content


class _UpstreamUnauthorized(Exception):
    """Internal marker — 401 from cloud means auth layer should attempt refresh."""


def _auth_from_cloud(data: dict[str, Any]) -> CloudAuthResult:
    """ndi-cloud-node returns {token, user}. Cognito ID tokens default to 1h TTL."""
    return CloudAuthResult(
        access_token=data.get("token") or data.get("accessToken") or "",
        refresh_token=data.get("refreshToken"),  # may be None — the cloud doesn't currently issue one
        expires_in_seconds=int(data.get("expiresIn", 3600)),
        user=data.get("user"),
    )


def _extract_detail(body: Any) -> str | None:
    if isinstance(body, dict):
        for key in ("error", "message", "detail"):
            v = body.get(key)
            if isinstance(v, str):
                return v
            if isinstance(v, dict):
                m = v.get("message")
                if isinstance(m, str):
                    return m
    return None


def _scope_kind(scope: str) -> str:
    if scope in ("public", "private", "all"):
        return scope
    if "," in scope:
        return "multi-dataset"
    return "single-dataset"
