"""Transparent access-token refresh.

Called by the auth dependency on every authenticated request. If the access
token is within `grace_seconds` of expiry or already expired, attempt to refresh.
Lock-protected to avoid thundering herd.
"""
from __future__ import annotations

import time

from ..clients.ndi_cloud import NdiCloudClient
from ..config import get_settings
from ..errors import AuthExpired, CloudUnreachable
from ..observability.logging import get_logger
from ..observability.metrics import (
    cognito_refresh_duration_seconds,
    cognito_refresh_total,
)
from .session import SessionData, SessionStore

log = get_logger(__name__)


async def ensure_fresh_access_token(
    session: SessionData,
    *,
    store: SessionStore,
    cloud: NdiCloudClient,
) -> SessionData:
    settings = get_settings()
    grace = settings.ACCESS_TOKEN_REFRESH_GRACE_SECONDS
    now = int(time.time())

    if session.access_token_expires_at - now > grace:
        return session

    if session.refresh_token is None:
        # Cloud doesn't expose a refresh endpoint yet. Delete the session and
        # force re-login on the next authenticated request. ADR 005 flags this.
        await store.delete(session.session_id)
        raise AuthExpired("Session expired. Please log in again.")

    acquired = await store.acquire_refresh_lock(session.session_id)
    if not acquired:
        # Another worker refreshing — wait and re-read.
        await store.wait_for_refresh(session.session_id)
        fresh = await store.get(session.session_id)
        if fresh is None:
            raise AuthExpired("Session disappeared while waiting for refresh.")
        # If still stale, we'll try once more; otherwise return.
        if fresh.access_token_expires_at - int(time.time()) > grace:
            return fresh
        # Fall through to try refreshing ourselves now.
        session = fresh

    try:
        t0 = time.perf_counter()
        try:
            auth = await cloud.refresh(session.refresh_token)
            await store.update_tokens(
                session,
                access_token=auth.access_token,
                refresh_token=auth.refresh_token,
                access_token_expires_in_seconds=auth.expires_in_seconds,
            )
            cognito_refresh_total.labels(outcome="success").inc()
            log.info("auth.refresh.success", session_id=session.session_id)
            return session
        except AuthExpired:
            cognito_refresh_total.labels(outcome="failure").inc()
            await store.delete(session.session_id)
            raise
        except CloudUnreachable:
            cognito_refresh_total.labels(outcome="unreachable").inc()
            raise
        finally:
            cognito_refresh_duration_seconds.observe(time.perf_counter() - t0)
    finally:
        await store.release_refresh_lock(session.session_id)
