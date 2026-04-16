# ADR 002 — Session cookie with server-side JWT, not JWT in JavaScript

**Status:** Accepted, 2026-04-16

## Context

Two common auth patterns for SPAs that talk to a JWT-issuing API:
- (A) Server holds the JWT, SPA holds an opaque session ID cookie, proxy forwards.
- (B) SPA holds the JWT in localStorage / sessionStorage / memory, attaches `Authorization` header directly.

## Decision

Use pattern A. Access token and refresh token are both encrypted at rest in Redis, referenced via a random 128-bit session ID carried in an `HttpOnly; Secure; SameSite=Lax` cookie.

## Rationale

1. **XSS resistance.** Any script-readable token is an XSS target. With pattern B, a single compromised npm package or a rendered-as-HTML malicious string can exfiltrate the JWT. With pattern A, neither JavaScript nor extensions can read the session cookie.
2. **CSRF handled.** `SameSite=Lax` prevents most cross-site request forgery; we also add a double-submit CSRF token on mutations.
3. **Short refresh cycle doesn't leak.** Refresh tokens live longer than access tokens. Exposing them to JS is worse; encrypting and keeping them server-side bounds the blast radius to the backend.
4. **Logout is definitive.** We delete the Redis entry; the cookie can't be re-used.

## Consequences

- Slightly more server complexity (encrypt/decrypt, Redis dependency).
- Users can't "copy their token" for programmatic access. For that, they should use NDI-python directly with their username/password.
- CORS must be configured for `credentials: include`.

## Encryption details

- Fernet (cryptography.fernet), symmetric, 128-bit AES-CBC + HMAC-SHA256.
- Key sourced from `SESSION_ENCRYPTION_KEY` env var (32 bytes, base64-encoded).
- Rotation: store key version number in the stored payload; accept N and N-1 on read, write only N. A rotation changes keys and bumps the version.

## Alternatives considered

- **Pattern B with HttpOnly `Authorization` header via service worker.** Rejected: service workers can still be compromised; added complexity isn't worth it.
- **In-memory JWT only (no persistence).** Rejected: page reload re-requires login; poor UX.
