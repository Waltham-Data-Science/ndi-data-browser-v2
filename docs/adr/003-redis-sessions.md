# ADR 003 — Redis-backed sessions (not Postgres, not in-memory)

**Status:** Accepted, 2026-04-16
**Supersedes:** informal proposal in `sequential-crunching-biscuit.md` to use Postgres.

## Context

Session state needs to survive backend worker restarts. In-memory won't work because Railway deploys multiple workers and restarts them routinely. The earlier plan called for Postgres. This ADR revises that choice.

## Decision

Use Redis for session storage and rate-limit counters.

## Rationale

1. **Sessions are small and ephemeral.** ~500 bytes each, 24-hour absolute TTL. Redis's native TTL + key expiration handles the lifecycle without cron jobs.
2. **Sub-millisecond access.** Redis session lookup is ~0.2 ms; Postgres is ~2-5 ms. Since the auth check happens on every authenticated request, this adds up.
3. **Rate limit counters belong here too.** Sliding-window counters via Redis sorted sets are a standard pattern. Postgres is wrong-tool-shape for this.
4. **Lighter infra.** Railway's Redis add-on has an entry tier; Postgres for the same job carries a schema, migrations, and connection pooling we don't need.
5. **Refresh-lock primitive.** We need an atomic "only one worker refreshes a given session at a time" primitive. `SET NX EX` on Redis is one line; Postgres advisory locks are more ceremony.

## Consequences

- One extra dependency (Redis) instead of one extra dependency (Postgres). Net-net: same.
- No SQL to write, no migrations.
- Session encryption key rotation becomes "add a version byte to stored blobs" — same as with Postgres, no change.
- Not a durable store. If Redis is reset, everyone re-logs-in. Acceptable: sessions are by definition short-lived.

## Schema (conceptual)

```
KEY                               TYPE    TTL
session:<id>                       HASH    86400
  user_id                         string
  user_email_hash                 string
  encrypted_access_token          bytes
  encrypted_refresh_token         bytes
  access_token_expires_at         int (unix)
  issued_at                       int
  last_active                     int
  ip_addr_hash                    string
  user_agent_hash                 string

session:<id>:refresh-lock          string  5    (transient, only during refresh)

ratelimit:<userId|ip>:<bucket>     ZSET    60   (sliding window timestamps)

csrf:<sessionId>                   string  86400
```

## Alternatives considered

- **Postgres.** Heavier, slower, schema ceremony, wrong shape for rate-limit counters.
- **Signed cookies (no server state).** Rejected: revocation becomes impossible without DB anyway (we'd need a deny-list), and the session payload would be too large once encrypted.
- **In-memory with sticky sessions.** Rejected: Railway doesn't guarantee sticky routing; failover scenarios break sessions.
