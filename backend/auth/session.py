"""Redis-backed session store.

Stores the (encrypted) access token, user ID, timestamps, and a soft
fingerprint for audit. Access tokens encrypted via Fernet.

Session lifecycle:
  - Created on login. TTL = absolute (default 24h).
  - Touched (last_active updated) on every authenticated request.
  - Deleted on logout or when the access token expires (see ADR-008 —
    the cloud does not expose a refresh endpoint; expired sessions are
    deleted and force re-login).
"""
from __future__ import annotations

import hashlib
import json
import secrets
import time
from dataclasses import asdict, dataclass
from typing import Any

from cryptography.fernet import Fernet, InvalidToken
from redis.asyncio import Redis

from ..config import Settings, get_settings
from ..observability.logging import get_logger

log = get_logger(__name__)


def _derive_fernet_key(raw: str) -> bytes:
    """Accept either a Fernet key (base64 urlsafe 44 chars) or an arbitrary string.

    For arbitrary strings, SHA-256 + urlsafe-base64-encode to derive a valid key.
    """
    # If the raw value is already a valid Fernet key, use it.
    try:
        Fernet(raw.encode())
        return raw.encode()
    except Exception:
        import base64
        digest = hashlib.sha256(raw.encode()).digest()
        return base64.urlsafe_b64encode(digest)


@dataclass(slots=True)
class SessionData:
    session_id: str
    user_id: str
    user_email_hash: str
    access_token: str
    access_token_expires_at: int
    issued_at: int
    last_active: int
    ip_addr_hash: str
    user_agent_hash: str

    def to_redis(self, fernet: Fernet) -> dict[str, Any]:
        d = asdict(self)
        d["access_token"] = fernet.encrypt(self.access_token.encode()).decode()
        return d

    @classmethod
    def from_redis(cls, data: dict[str, Any], fernet: Fernet) -> SessionData:
        access_token = fernet.decrypt(data["access_token"].encode()).decode()
        return cls(
            session_id=data["session_id"],
            user_id=data["user_id"],
            user_email_hash=data["user_email_hash"],
            access_token=access_token,
            access_token_expires_at=int(data["access_token_expires_at"]),
            issued_at=int(data["issued_at"]),
            last_active=int(data["last_active"]),
            ip_addr_hash=data["ip_addr_hash"],
            user_agent_hash=data["user_agent_hash"],
        )


def user_scope_for(session: SessionData | None) -> str:
    """Stable, opaque cache-key scope derived from the session.

    Replaces the 1-bit ``authed: bool`` cache-key dimension used prior to
    PR-3. Per-user scoping prevents two authenticated users from sharing
    a cached entry — a latent false-sharing hazard that was previously
    safe by construction (the cloud returned user-invariant bodies for
    all cached endpoints) but would become exploitable the moment the
    cloud shipped any per-user variation.

    Returns ``"public"`` for unauthenticated reads, and
    ``f"u:{sha256(user_id)[:16]}"`` for authenticated reads. Truncated to
    16 hex chars (64 bits) — collision-resistant for the scale we care
    about and ~20 bytes per Redis key versus ~72 for a full SHA-256.
    """
    if session is None:
        return "public"
    digest = hashlib.sha256(session.user_id.encode()).hexdigest()
    return f"u:{digest[:16]}"


class CorruptSession(Exception):
    pass


class SessionStore:
    def __init__(self, redis: Redis, settings: Settings | None = None) -> None:
        self.redis = redis
        self.settings = settings or get_settings()
        self.fernet = Fernet(_derive_fernet_key(self.settings.SESSION_ENCRYPTION_KEY))

    # --- Creation & deletion ---

    def _key(self, session_id: str) -> str:
        return f"session:{session_id}"

    async def create(
        self,
        *,
        user_id: str,
        email: str,
        access_token: str,
        access_token_expires_in_seconds: int,
        ip: str,
        user_agent: str,
    ) -> SessionData:
        session_id = secrets.token_hex(16)  # 128 bits
        now = int(time.time())
        data = SessionData(
            session_id=session_id,
            user_id=user_id,
            user_email_hash=hashlib.sha256(email.lower().encode()).hexdigest(),
            access_token=access_token,
            access_token_expires_at=now + access_token_expires_in_seconds,
            issued_at=now,
            last_active=now,
            ip_addr_hash=hashlib.sha256(ip.encode()).hexdigest()[:32],
            user_agent_hash=hashlib.sha256(user_agent.encode()).hexdigest()[:32],
        )
        await self._write(data)
        return data

    async def get(self, session_id: str) -> SessionData | None:
        key = self._key(session_id)
        raw = await self.redis.get(key)
        if raw is None:
            return None
        try:
            payload = json.loads(raw)
        except (TypeError, ValueError):
            return None
        try:
            return SessionData.from_redis(payload, self.fernet)
        except (CorruptSession, InvalidToken):
            await self.redis.delete(key)
            return None

    async def delete(self, session_id: str) -> None:
        await self.redis.delete(self._key(session_id))

    async def touch(self, session: SessionData) -> None:
        session.last_active = int(time.time())
        await self._write(session)

    async def _write(self, session: SessionData) -> None:
        payload = session.to_redis(self.fernet)
        ttl = self.settings.SESSION_ABSOLUTE_TTL_SECONDS
        # Compute remaining TTL so a refresh doesn't extend absolute lifetime.
        remaining = max(60, ttl - (int(time.time()) - session.issued_at))
        await self.redis.set(self._key(session.session_id), json.dumps(payload), ex=remaining)
