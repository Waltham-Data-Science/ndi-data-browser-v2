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
from dataclasses import asdict, dataclass, field
from typing import Any

from cryptography.fernet import Fernet, InvalidToken
from fastapi import Request
from redis.asyncio import Redis

from ..config import Settings, get_settings
from ..observability.logging import get_logger

log = get_logger(__name__)


def _hash_ip(ip: str) -> str:
    return hashlib.sha256(ip.encode()).hexdigest()[:32]


def _hash_user_agent(user_agent: str) -> str:
    return hashlib.sha256(user_agent.encode()).hexdigest()[:32]


def fingerprint(request: Request) -> tuple[str, str]:
    """Compute (ip_hash, user_agent_hash) for a request.

    Same hashing as ``SessionStore.create``. Used at both session creation
    (via ``do_login``) and session validation (via ``get_current_session``)
    so the two callsites can never drift.
    """
    ip = request.client.host if request.client else "unknown"
    user_agent = request.headers.get("user-agent", "unknown")
    return _hash_ip(ip), _hash_user_agent(user_agent)


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
    # Cached from the cloud's login response so `/api/auth/me` and
    # `/api/datasets/my` don't need a second cloud round-trip per
    # request. Safe to store unhashed (organization IDs are not PII
    # and `isAdmin` is a boolean capability, not a secret).
    # Defaults below are for backward-compat when deserializing
    # pre-2026-04-20 sessions that predate these fields.
    organization_ids: list[str] = field(default_factory=list)
    is_admin: bool = False

    def to_redis(self, fernet: Fernet) -> dict[str, Any]:
        d = asdict(self)
        d["access_token"] = fernet.encrypt(self.access_token.encode()).decode()
        return d

    @classmethod
    def from_redis(cls, data: dict[str, Any], fernet: Fernet) -> SessionData:
        """Rehydrate a session from its Redis blob.

        Raises ``CorruptSession`` if the blob is missing required fields or
        any field is the wrong type. Audit 2026-04-23 (#56): previously this
        raised ``KeyError`` / ``TypeError`` / ``ValueError`` directly, and
        only ``InvalidToken`` + ``CorruptSession`` were caught upstream, so a
        drifted Redis payload crashed session reads with a 500 instead of
        falling through to re-login.
        """
        try:
            access_token = fernet.decrypt(data["access_token"].encode()).decode()
            raw_orgs = data.get("organization_ids") or []
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
                organization_ids=[str(x) for x in raw_orgs],
                is_admin=bool(data.get("is_admin", False)),
            )
        except InvalidToken:
            # Propagate so callers can distinguish "decryption failed"
            # (encryption-key rotation) from "schema drift".
            raise
        except (KeyError, TypeError, ValueError, AttributeError) as e:
            raise CorruptSession(f"session schema drift: {e}") from e


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
        organization_ids: list[str] | None = None,
        is_admin: bool = False,
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
            ip_addr_hash=_hash_ip(ip),
            user_agent_hash=_hash_user_agent(user_agent),
            organization_ids=list(organization_ids or []),
            is_admin=bool(is_admin),
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
            log.warning("session.corrupt_json", session_id=session_id[:8])
            await self.redis.delete(key)
            return None
        try:
            return SessionData.from_redis(payload, self.fernet)
        except (CorruptSession, InvalidToken) as e:
            # Drift / decryption-failure / missing-field all surface here
            # as a soft re-auth rather than a 500. Clean up the bad blob
            # so the session doesn't keep re-crashing every request.
            log.warning(
                "session.corrupt_payload",
                session_id=session_id[:8],
                reason=type(e).__name__,
            )
            await self.redis.delete(key)
            return None

    async def delete(self, session_id: str) -> None:
        await self.redis.delete(self._key(session_id))

    async def touch(self, session: SessionData) -> None:
        session.last_active = int(time.time())
        await self._write(session)

    async def _write(self, session: SessionData) -> None:
        """Persist the session blob with a Redis TTL that respects BOTH
        the absolute lifetime ceiling AND the idle-window floor (O3).

        TTL = `min(remaining_absolute, idle_ttl)`:
        - On a fresh session: TTL = idle_ttl (typically 2h). Each `touch`
          refreshes the TTL back to idle_ttl, so an active session gets
          a rolling 2-hour grace from its last activity.
        - On a long-running session in its 23rd hour: remaining_absolute
          drops below idle_ttl and caps the TTL → the absolute 24h ceiling
          is respected.
        - On an idle session: Redis expires the key naturally after
          idle_ttl with no touch — the session disappears without an
          explicit reaper.

        The 60-second floor preserves a minimum useful lifetime for the
        "last few seconds before absolute" edge so a request mid-flight
        doesn't lose its session before it can finish.
        """
        payload = session.to_redis(self.fernet)
        absolute_ttl = self.settings.SESSION_ABSOLUTE_TTL_SECONDS
        idle_ttl = self.settings.SESSION_IDLE_TTL_SECONDS
        remaining_absolute = max(60, absolute_ttl - (int(time.time()) - session.issued_at))
        ttl = min(remaining_absolute, idle_ttl)
        await self.redis.set(self._key(session.session_id), json.dumps(payload), ex=ttl)
