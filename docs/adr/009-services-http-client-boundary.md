# ADR 009 — Services HTTP client boundary + OntologyService carve-out

**Status:** Accepted, 2026-04-17

## Context

CLAUDE.md workflow rule #3 states: "Services are pure business logic. They never import fastapi and never do HTTP directly. Cloud I/O lives in `clients/ndi_cloud.py`." This rule existed to keep the proxy-to-cloud boundary narrow and testable.

Independent code reviews (Karpathy P1 BLATANT, Karpathy P4 BLATANT) flagged that `backend/services/ontology_service.py` directly imports `httpx` and makes HTTP calls to five external providers: EBI OLS4, SciCrunch, WormBase, PubChem. This violates the rule's letter.

The intent of rule #3 was specifically about the *ndi-cloud-node* boundary — ensuring one client (`NdiCloudClient`) owns that conversation so auth, retries, circuit breaker, and typed errors stay consistent. External ontology providers have a materially different profile: different base URLs, no Cognito bearer, different retry semantics (they're public APIs, not our cloud), no per-user auth, and the provider set (EBI/SciCrunch/WormBase/PubChem) is deliberately heterogeneous.

## Decision

Keep `OntologyService`'s direct `httpx` usage as a documented exception. Rewrite CLAUDE.md rule #3 to reflect the true intent: "Services never do HTTP to ndi-cloud-node directly; external ontology lookups are an exception documented in ADR-009." Enforce the narrower rule via a ruff `flake8-tidy-imports` ban that forbids `httpx` under `backend/services/` with an explicit `per-file-ignores` carve-out for `ontology_service.py`.

## Rationale

1. **The rule's intent is about the cloud boundary, not all external HTTP.** The narrower rule matches the original ADR-001 (proxy backend) concerns without over-constraining external lookups.
2. **Migrating ontology to `clients/` would be wasted motion.** `NdiCloudClient`'s design (bearer token, circuit breaker configured against the cloud's latency profile, shared retry logic) doesn't fit external ontology providers. A separate `clients/ontology_providers.py` would have a completely different shape — effectively duplicating `OntologyService`'s current responsibilities in a new location for no win.
3. **Enforcement via ruff prevents the rule from being quietly broken elsewhere.** Without the ruff rule, a future contributor could add `import httpx` to any other service module, creating the exact drift Karpathy flagged. The ban + carve-out names the exception explicitly.

## Consequences

- `backend/services/ontology_service.py` continues to import `httpx` and make direct calls to external providers.
- The ruff rule fails CI if any OTHER file under `backend/services/` imports `httpx`.
- Future additions to OntologyService (new providers, retry tuning) remain in-service.
- If a future service genuinely needs external HTTP that isn't ontology, the carve-out list must be updated — which forces the tradeoff to be surfaced in an ADR update.

## Alternatives considered

- **Move OntologyService to `backend/clients/ontology_providers.py`.** Rejected: the module isn't shaped like a typed cloud client. It's a fetch-and-cache service that orchestrates five different upstreams with different retry/timeout profiles. Forcing it into the `clients/` shape would either duplicate machinery or water down what `clients/` means.
- **Remove the rule entirely.** Rejected: the rule's intent (keep the cloud boundary narrow) is correct; just over-stated.

## References

- CLAUDE.md rule #3 (updated in this PR to reflect the narrower form)
- ADR-001 (proxy backend)
- Karpathy P1 BLATANT finding, 2026-04-17
- Karpathy P4 BLATANT finding, 2026-04-17 ("services never do HTTP" assertion vs. reality)
