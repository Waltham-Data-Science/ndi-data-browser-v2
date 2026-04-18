# ADR 005 — Transparent Cognito refresh token flow

**Status:** Superseded by ADR-008 (2026-04-17)

**Status:** Accepted, 2026-04-16

## Context

Cognito access tokens default to 1-hour TTL. If the backend simply passes them through and returns `AUTH_EXPIRED` on expiry, users must re-login every hour during active use. This is disruptive for long-running table explorations.

## Decision

Store both access and refresh tokens (encrypted) in Redis session. On any authenticated request, if the access token is within 60 seconds of expiry or already expired, attempt refresh via `POST /auth/refresh` before forwarding. Use a short-lived Redis lock to prevent concurrent refresh attempts on the same session.

## Rationale

1. **Better UX.** With a refresh token TTL of e.g. 30 days, users re-login once per month instead of once per hour.
2. **Server-only state.** The refresh token never reaches the browser, so XSS cannot steal it.
3. **Deterministic behavior.** Access tokens are refreshed eagerly (60s grace), not reactively on 401, so request paths don't contain retry loops after a failed-then-refreshed call.
4. **Thundering herd avoided.** Redis `SET NX EX 5` ensures only one worker refreshes a given session at a time; others wait briefly and then re-read.

## Consequences

- One more Cognito dependency (`POST /auth/refresh`).
- Refresh failures (token rotation revoked, IAM changes) cascade into a single `AUTH_EXPIRED` for the user, identical to expiry-without-refresh.
- Adds one Redis write per refresh (~once per hour per active session). Insignificant load.

## Flow (happens on every authed request)

```
load session from Redis
if access_token_expires_at - now < 60s:
    acquired = SET NX EX 5 session:<id>:refresh-lock "1"
    if acquired:
        POST cloud /auth/refresh { refreshToken }
          on success: UPDATE session with new access_token + expires_at
          on failure: DELETE session, raise AUTH_EXPIRED
        DEL session:<id>:refresh-lock
    else:
        wait up to 5s with exponential backoff, re-read session
        if still stale after timeout: raise CLOUD_UNREACHABLE
forward request with access token
```

## Alternatives considered

- **No refresh (original plan).** Rejected: bad UX, forces users to re-login hourly.
- **Client-driven refresh.** Rejected: moves refresh token to JavaScript, defeats ADR-002.
- **Refresh token rotation on every refresh.** Deferred: more complex, not needed for v2. Revisit if Cognito configuration requires it.

## Monitoring

- `ndb_cognito_refresh_total{outcome="success|failure"}`
- `ndb_cognito_refresh_duration_seconds`
- `ndb_session_refresh_lock_contention_total`

If failure rate exceeds 1% of refresh attempts, investigate (Cognito config change, clock skew, etc.).
