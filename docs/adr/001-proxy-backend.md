# ADR 001 — FastAPI proxy backend (not direct browser-to-cloud)

**Status:** Accepted, 2026-04-16

## Context

The v2 could talk to ndi-cloud-node directly from the browser, skipping our backend entirely. That would save ~20-50 ms per request and one service to operate.

## Decision

Keep FastAPI as a proxy + enricher between the browser and ndi-cloud-node.

## Rationale

1. **Token safety.** Direct-to-cloud requires exposing a Cognito JWT to JavaScript, which is an XSS liability. With the proxy, only an opaque session cookie touches the browser.
2. **Enrichment.** Binary decoding (NBF/VHSB/image/video), ontology lookups, and violin-plot math are all Python-dependent server-side work. They cannot move to the browser.
3. **Refresh flow.** Transparent Cognito refresh token exchange requires server-side state (Redis, encrypted refresh token, refresh-lock). Moving this to the browser means either re-login every hour or shipping refresh logic to JavaScript with all its risks.
4. **Single pane of glass.** Rate limiting, observability, error mapping, and CSRF protection all live in one place. A split architecture duplicates this surface.

## Consequences

- Extra ~20-50 ms per request (mostly negligible given cloud is already seconds-fast for queries).
- One more service to operate (but it was going to exist anyway for binary/ontology).
- One API surface for the frontend to target; no conditionals on which backend to call.

## Alternatives considered

- **Direct browser to cloud.** Rejected: token safety and enrichment requirements preclude.
- **Split: reads direct, binary/ontology via proxy.** Rejected: introduces two auth paths, two error models, and context-switching in the frontend code.
- **Node.js proxy instead of FastAPI.** Rejected: Python ecosystem (NDI-python, Pillow, numpy, scipy for distributions) is required for enrichment.
