# ADR 008 — Deprecate Cognito refresh token flow

**Status:** Accepted, 2026-04-17
**Supersedes:** ADR-005 (Transparent Cognito refresh token flow)

## Context

ADR-005 specified a transparent access-token refresh mechanism using `POST /auth/refresh` on the NDI Cloud. At the time of writing, that endpoint was assumed to be on the roadmap. Steve has since confirmed (Slack, 2026-04) that no `/auth/refresh` endpoint is planned; the cloud's Cognito integration issues access tokens with a 1-hour TTL and does not expose refresh tokens to the backend.

The scaffolding built for ADR-005 — `backend/auth/token_refresh.py` (87 LOC), the `NdiCloudClient.refresh()` no-op that always raises `AuthExpired`, the `acquire_refresh_lock/wait_for_refresh/release_refresh_lock` primitives on `SessionStore`, and the `cognito_refresh_*` metrics — ran on every authed request but accomplished nothing, because the cloud method guaranteed failure by design.

Independent code reviews (see Phase 1 review pack) surfaced both the dead-code cost and a latent `finally`-releases-foreign-lock bug that would activate the day a real `/auth/refresh` endpoint shipped.

## Decision

Delete the entire refresh subsystem. Sessions that cross the access-token expiry boundary raise `AuthExpired` directly from `get_current_session`; the frontend's existing login-recovery path handles the user re-login.

## Rationale

1. **The mechanism cannot work.** Without a cloud-side refresh endpoint, there is no way to obtain a fresh access token. The only outcomes of the current code are "no refresh needed yet" or "AUTH_EXPIRED." The machinery between those two outcomes is unreachable.
2. **Dead scaffolding carries cost.** Every authed request paid the overhead of lock-acquire logic and metric observations that could never register meaningful data. A subtle bug in the lock-release path was flagged by Claude systematic-debugging H1 and gstack `/review` P1 — deletion removes the bug class entirely.
3. **Speculative code is a liability, not an asset.** ADR-005 was written in anticipation of a cloud feature that did not ship. If Steve ever adds refresh in the future, the *new* code will be written against the *actual* cloud contract — which may not match the ADR-005 shape. Keeping scaffolding for a feature that might arrive in a different form biases future design toward the old shape.

## Consequences

- Users re-login every hour during active use. This matches the pre-ADR-005 behavior.
- The 1-hour UX problem that motivated ADR-005 **remains unsolved**. It is flagged here as an open product concern, not blocked by this ADR. Possible future paths (each with distinct tradeoffs, none in scope of this ADR): raise Cognito access-token TTL, Cognito device flow, a separate re-auth UX that doesn't require full-page login.
- The deletion removes ~230 LOC from the backend and removes the lock-release-foreign-lock bug class.

## Alternatives considered

- **Fix the bug in place, keep the scaffolding.** Rejected: the scaffolding doesn't work even with the bug fixed, because the cloud endpoint it calls doesn't exist.
- **Leave it and wait for the cloud feature.** Rejected: Steve has confirmed no such feature is planned, and retaining dead code in main creates both cognitive load and CI cost for every contributor.

## References

- ADR-005 (superseded by this ADR)
- Phase 1 review pack findings: Karpathy P2 SUBTLE, Karpathy P3 OBVIOUS, gstack `/review` P1, Claude systematic-debugging H1
- Slack, Steve <-> Audri thread (2026-04, refresh endpoint status)
