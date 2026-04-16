# ADR 006 — React Router 7 (not TanStack Router)

**Status:** Accepted, 2026-04-16

## Context

An earlier plan draft proposed migrating to TanStack Router for compile-time route checking, typed params, and parallel data loading. v1 uses React Router 7, which has similar features (loaders, typed search params via typegen, parallel data loading).

## Decision

Stay on React Router 7. Do not migrate to TanStack Router in v2.

## Rationale

1. **v1 familiarity.** The team already knows React Router 7. TanStack Router is a different mental model (file-based routing, lazy route boundaries).
2. **Gain is marginal.** React Router 7 supports typed loaders, loader-level data fetching, typed search params, and nested layouts. The "compile-time route check" improvement with TanStack is nice but not a deal-breaker.
3. **Migration is risk.** Every `<Link>`, every `useNavigate`, every loader would need rewriting. That's pure churn for v2.
4. **TanStack Query already used.** We stay in the TanStack family for data fetching, we just don't need their router.

## Consequences

- No migration work.
- Frontend developers already productive.

## Alternatives considered

- **TanStack Router migration.** Rejected: cost > benefit for v2.
- **Stay on React Router 6.** Rejected: v7 has improvements we want (better SSR support, typegen), and v1 already uses 7.
