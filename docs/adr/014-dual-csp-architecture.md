# ADR 014 — Dual CSP architecture (FastAPI + Vercel)

**Status:** Accepted — 2026-04-26
**Closes:** Audit synthesis §O8 (FastAPI/Vercel CSP reconciliation)

## Context

Both surfaces of the unified `ndi-cloud.com` deployment set their own
`Content-Security-Policy` (or `Content-Security-Policy-Report-Only`)
header:

| Surface | CSP set by | Applies to |
|---|---|---|
| **Vercel** (`apps/web/middleware.ts`) | Next.js Edge Middleware | HTML pages served from `ndi-cloud.com/*` (marketing + catalog + app routes) |
| **FastAPI** (`backend/middleware/security_headers.py`) | `SecurityHeadersMiddleware` | JSON responses from `ndb-v2-production.up.railway.app/api/*` |

The audit (synthesis §O8) flagged that the two CSPs differ in their
allowlists and asked whether they should be reconciled. This ADR
documents why the deliberate split is correct.

## Decision

Keep the two CSPs distinct. Document the rationale here and
cross-reference each header definition in code so future maintainers
don't try to "fix" the inconsistency.

## Rationale

### CSP applies to the page that originated the request

The browser's CSP enforcement is scoped to the **document context** —
the HTML page that loaded the script / made the fetch / opened the
WebSocket. CSP on a JSON response only matters when:

- A user opens the JSON URL directly in a browser tab (extremely rare
  in normal usage).
- An iframe embeds the JSON response (the `frame-ancestors 'none'`
  directive on the FastAPI side prevents this).

The 99.9% case is "the SPA fetches `/api/*` from a Vercel-rendered
page." In that case, the browser checks the SPA's CSP (Vercel's),
not the JSON response's CSP (FastAPI's). FastAPI's CSP is purely
defense-in-depth — it does not need to mirror Vercel's.

### Different threat models, different allowlists

**Vercel-side CSP** is the one that actually gates user-visible
script execution. Its allowlist must include:

- `https://www.googletagmanager.com`, `https://www.google-analytics.com`
  in `script-src` — Vercel Analytics + Speed Insights load tagging
  snippets from these origins.
- `https://vitals.vercel-insights.com`, `https://www.google-analytics.com`
  in `connect-src` — analytics beacons.
- The Railway API origin in `connect-src` — the SPA's `apiFetch`
  needs to reach `/api/*` via Vercel rewrite to Railway.

**FastAPI-side CSP** does not need any of those. JSON responses
don't load analytics scripts. The FastAPI CSP is a hardened minimum:

- `script-src 'self'` — JSON has no scripts.
- `frame-ancestors 'none'` — defense against iframe embedding of the
  raw API.
- `connect-src` only includes the cloud upstream + the configured
  CORS origins — meaningful only if a user opens `/api/*` directly
  in a browser tab.

A "reconciled" single CSP would either:

1. Bring Vercel's analytics origins into the FastAPI CSP (loosening
   the API surface for no benefit).
2. Strip them from the Vercel CSP (breaking analytics).

Neither is desirable. The split is correct.

## Cross-references

- Vercel CSP definition: `apps/web/middleware.ts` (search `CSP_POLICY`).
  - Currently set in `Content-Security-Policy-Report-Only` mode; flips
    to enforced on Phase 7 cutover after the soak window.
- FastAPI CSP definition: `backend/middleware/security_headers.py`.
  - Always enforced.
  - Optional CSP-violation reporting via `CSP_REPORT_URI`
    (synthesis §O2 / ADR — see `docs/operations.md` if added).

## Consequences

- Future audits should not flag the CSP differences as a
  "consistency" issue. Direct them here.
- If the Vercel side ever proxies HTML responses from FastAPI (e.g.
  a server-side render that pipes through Railway), THIS ADR no
  longer applies — the FastAPI CSP would then govern the document
  context and reconciliation would matter.
- If/when CSP report aggregation is wired (`CSP_REPORT_URI` set on
  Railway, equivalent endpoint set on Vercel), expect distinct
  violation patterns from each surface. Treat them as separate
  signal streams.
