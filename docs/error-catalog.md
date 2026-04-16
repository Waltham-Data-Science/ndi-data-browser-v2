# Error Catalog

Every error the system can raise. Types are canonical; the code/message shape is what clients see.

## Response shape

```json
{
  "error": {
    "code": "AUTH_EXPIRED",
    "message": "Your session has expired. Please log in again.",
    "recovery": "login",
    "requestId": "7f3a...",
    "details": null
  }
}
```

- `code`: stable string enum. Clients route on this.
- `message`: human-readable, localizable. Safe to display.
- `recovery`: `"retry" | "login" | "contact_support" | "none"`.
- `requestId`: propagated from `X-Request-ID` header.
- `details`: optional extra context (e.g., which field failed validation).

## Catalog

| Code | HTTP | User message | Recovery | Fires when |
|---|---|---|---|---|
| `AUTH_REQUIRED` | 401 | Please log in to view this content. | login | Unauthenticated request to a private resource |
| `AUTH_EXPIRED` | 401 | Your session has expired. Please log in again. | login | Cloud returned 401 even after refresh attempt |
| `AUTH_INVALID_CREDENTIALS` | 401 | Invalid username or password. | none | Login failed at Cognito |
| `AUTH_RATE_LIMITED` | 429 | Too many login attempts. Please wait {n} minutes. | none | Login rate limit hit |
| `FORBIDDEN` | 403 | You don't have access to this resource. | none | Cloud returned 403 |
| `NOT_FOUND` | 404 | This dataset or document doesn't exist or you can't access it. | none | Cloud returned 404 |
| `VALIDATION_ERROR` | 400 | Your request was invalid: {details}. | none | Pydantic validation failure or invalid query DSL |
| `RATE_LIMITED` | 429 | You're making requests too quickly. Please wait a moment. | retry | Our limiter or cloud's limiter |
| `CLOUD_UNREACHABLE` | 502 | NDI Cloud is temporarily unavailable. We'll retry automatically. | retry | All retries exhausted or breaker open |
| `CLOUD_TIMEOUT` | 504 | The request took too long. Please try again. | retry | Cloud exceeded per-request timeout |
| `CLOUD_INTERNAL_ERROR` | 502 | NDI Cloud returned an error. We've been notified. | retry | Cloud 500 after retries |
| `BINARY_DECODE_FAILED` | 502 | Could not read the binary data for this document. | contact_support | NBF/VHSB parse error |
| `BINARY_NOT_FOUND` | 404 | The binary data for this document is not available. | none | No file in registry |
| `QUERY_TIMEOUT` | 504 | Your query took too long. Please narrow the scope or add an isa filter. | retry | Cloud ndiquery hit Lambda 29s ceiling |
| `QUERY_TOO_LARGE` | 400 | Your query matched too many documents. Please narrow your search. | none | Result set exceeds cap (default 50k) |
| `QUERY_INVALID_NEGATION` | 400 | The \`~or\` operation isn't supported. Please restructure your query. | none | User submits `~or` — cloud rejects |
| `BULK_FETCH_TOO_LARGE` | 400 | You can fetch at most {n} documents at a time. | none | >500 IDs requested |
| `ONTOLOGY_LOOKUP_FAILED` | 502 | Could not look up ontology term. | retry | Provider unreachable |
| `CSRF_INVALID` | 403 | Your session is out of sync. Please refresh the page. | retry | Missing/mismatched CSRF token on mutation |
| `INTERNAL` | 500 | Something went wrong. We've been notified. | contact_support | Unhandled exception — fallback |

## Frontend UI mapping

| Recovery | UI component | Behavior |
|---|---|---|
| `retry` | `<RetryPanel>` | Message + primary button "Try again" → triggers refetch |
| `login` | `<LoginRequired>` | Message + auto-redirect after 2s, preserves `returnTo` |
| `contact_support` | `<SupportPanel>` | Message + copyable request ID + mailto link |
| `none` | `<ErrorMessage>` | Inline, no action |

For React render errors: `<ErrorBoundary>` catches and shows "Something went wrong. Please refresh." with a bug-report link.

## Invariants

1. **No unclassified errors.** Any handler raising a bare `Exception` is caught by the fallback `INTERNAL` mapper.
2. **No Python tracebacks in responses.** Tracebacks are in logs only.
3. **Error codes are stable.** Changing a code is a breaking API change (versioned).
4. **Every code is tested.** `backend/tests/unit/test_error_catalog.py` instantiates and serializes each.
5. **Every UI recovery is E2E tested.** `frontend/tests-e2e/error-recovery.spec.ts` triggers each code and asserts the correct UI state.
6. **Error messages are localization-ready.** Each `user_message` is a format-string with named placeholders; no concatenation.
