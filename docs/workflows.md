# User Workflows

Every workflow has a happy path, all failure modes, observability notes, and a Playwright E2E test. This document is the source of truth — the E2E suite and the error catalog are both derived from here.

## Conventions

- `→` = HTTP call
- `⟳` = retry / circuit-breaker state
- `[N]` = corresponds to error code N in [error-catalog.md](error-catalog.md)

---

## W1. Public dataset catalog browsing (unauthenticated)

**Trigger:** user visits `/datasets`.

**Happy path:**
1. Frontend issues `GET /api/datasets/published?page=1&pageSize=20`.
2. Backend checks in-process cache (`published_list:p1:ps20`, TTL 1m). On hit, returns.
3. On miss, backend → cloud `GET /v1/datasets/published?page=1&pageSize=20`.
4. Backend caches, returns.
5. Frontend renders list with pagination, search box, filters.

**Failure modes:**
- Cloud 5xx after retries → [`CLOUD_UNREACHABLE`] → `<RetryPanel>`
- Cloud timeout → [`CLOUD_TIMEOUT`] → `<RetryPanel>`
- Our rate limit hit → [`RATE_LIMITED`] → toast + auto-backoff

**Observability:**
- Log: `{ event: "datasets.list", page, pageSize, cacheHit, durationMs }`
- Metric: `ndb_cloud_call_seconds{endpoint="datasets_published"}`

**E2E:** `public-catalog.spec.ts` — visits page, asserts >0 datasets rendered, search filters, pagination works.

---

## W2. Private dataset catalog browsing (authenticated)

**Trigger:** logged-in user visits `/my`.

**Happy path:**
1. Frontend `GET /api/datasets/my`.
2. Backend reads session cookie, looks up Redis session, decrypts access token.
3. If access token expired → W14 transparent refresh.
4. Backend → cloud `GET /v1/datasets/unpublished` with user's JWT.
5. Returns merged list (org's private + related public).
6. Frontend renders with lock icons for private entries.

**Failure modes:**
- No session cookie → [`AUTH_REQUIRED`] → `<LoginRequired>` redirects to `/login?returnTo=/my`
- Refresh failed → [`AUTH_EXPIRED`] → same
- Cloud 403 → [`FORBIDDEN`] → inline error
- Same cloud failures as W1

**Observability:**
- Log: `{ event: "datasets.my", userId: <hashed>, ... }`

**E2E:** `private-catalog.spec.ts` — logs in, visits `/my`, asserts private datasets shown.

---

## W3. Dataset overview

**Trigger:** user clicks a dataset.

**Happy path:**
1. `GET /api/datasets/:id` (cached 1m) + `GET /api/datasets/:id/class-counts` (cached 5m) in parallel.
2. Backend forwards to cloud `GET /v1/datasets/:id` and `GET /v1/datasets/:id/document-class-counts`.
3. Frontend renders: metadata card, class-count bar chart, CTAs for documents/query.

**Failure modes:**
- Dataset 404 → [`NOT_FOUND`]
- Forbidden private → [`FORBIDDEN`]
- Class-counts unavailable (dataset being published) → render metadata, show skeleton on chart with retry

**E2E:** `dataset-overview.spec.ts`

---

## W4. Single-class summary table (e.g., subjects)

**Trigger:** user picks `Subjects` tab on a dataset detail page.

**Happy path (2-step orchestration):**
1. `GET /api/datasets/:id/tables/subjects?page=1&pageSize=50`
2. Backend → cloud `POST /v1/ndiquery {searchstructure:[{operation:"isa", param1:"subject"}], scope:":id"}`.
3. Cloud returns subject document IDs (indexed classLineage, <1s).
4. Backend batches IDs into groups of ≤500, sends parallel (max 3 concurrent) `POST /v1/datasets/:id/documents/bulk-fetch` calls.
5. Backend projects fields (name, species, sex, strain, age, etc.) from returned docs.
6. Backend ontology-enriches term IDs (species, strain often ontology refs) via local SQLite cache.
7. Returns `{columns:[...], rows:[...]}`. Frontend renders with sort/filter/paginate on client side.

**Failure modes:**
- Zero results → empty state, no error
- ndiquery fails → [`CLOUD_UNREACHABLE`] / [`CLOUD_TIMEOUT`] → retry
- bulk-fetch partial failure (some batches 5xx) → retry just those batches, degrade gracefully
- Ontology provider down → fall back to term ID display without label, log [`ONTOLOGY_LOOKUP_FAILED`] (never blocks the table)

**Observability:**
- Log: `{ event: "table.build", dataset, className, idsCount, batches, totalMs }`
- Metric: `ndb_table_build_seconds{className}`

**E2E:** `summary-table.spec.ts` — for each class, table renders with rows, sort works, filter works.

---

## W5. Combined summary table (subjects ⋈ probes ⋈ epochs)

**Trigger:** user picks `Combined` tab.

**Happy path (chained via indexed `depends_on`):**
1. `GET /api/datasets/:id/tables/combined`
2. Backend step 1: ndiquery isa=subject scope=:id → subject IDs.
3. Backend step 2: bulk-fetch subjects.
4. Backend step 3: ndiquery `{op:"and", q1:{isa:"probe"}, q2:{depends_on:"*", "subject_ids"}}` scope=:id → probe IDs. *(depends_on indexed)*
5. Backend step 4: bulk-fetch probes.
6. Backend step 5: similar for epochs depending on probes.
7. Backend joins in memory on `depends_on` arrays, projects columns.
8. Returns combined table. Typical total: 1–3s.

**Failure modes:**
- Same as W4.
- Large datasets: if any step returns >10k IDs, continue (bulk-fetch batches it). No SQLite fallback.

**E2E:** `combined-table.spec.ts` — table renders for a 500-subject dataset in <3s, joins correct.

---

## W6. Document list (paginated, optionally filtered by class)

**Trigger:** user visits `/datasets/:id/documents?class=subject&page=1`.

**Happy path:**
1. `GET /api/datasets/:id/documents?class=subject&page=1&pageSize=50`
2. Backend → cloud ndiquery isa=subject scope=:id (indexed).
3. Returns paginated IDs and minimal metadata.
4. Frontend renders.

**E2E:** `document-list.spec.ts`

---

## W7. Document detail + binary rendering

**Trigger:** user visits `/datasets/:id/documents/:docId`.

**Happy path:**
1. `GET /api/datasets/:id/documents/:docId` → cloud `GET /v1/datasets/:id/documents/:docId` (file URLs hydrated).
2. Frontend renders document data tree + binary preview tab.
3. On binary preview open: `GET /api/datasets/:id/documents/:docId/data/type` → backend inspects file extension → returns `timeseries|image|video|fitcurve`.
4. Frontend issues corresponding data request:
   - `GET .../data/timeseries` → backend downloads signed file URL → parses NBF/VHSB → streams JSON with time/samples.
   - `GET .../data/image` → backend → Pillow → base64 data URI.
   - `GET .../data/video` → backend returns signed cloud URL (frontend plays directly).
   - `GET .../data/fitcurve` → backend evaluates parametric curve, returns xy arrays.

**Failure modes:**
- Doc 404 → [`NOT_FOUND`]
- Binary parse fail → [`BINARY_DECODE_FAILED`] → show metadata still, binary panel shows support link
- File missing from S3 → [`BINARY_NOT_FOUND`]

**E2E:** `document-detail.spec.ts`

---

## W8. Query builder

**Trigger:** user opens `/query` or clicks "Build query" from a dataset.

**Happy path:**
1. User builds query in visual builder: picks op (`isa`, `contains_string`, `depends_on`, etc.), optional `~` negation.
2. User picks scope: this dataset / my datasets / all public / everywhere.
3. Frontend submits `POST /api/query { searchstructure, scope }`.
4. Backend translates to cloud schema, → `POST /v1/ndiquery`.
5. Returns results with documentId, className, datasetId, snippet.

**Failure modes:**
- `~or` → client-side rejection (UI won't let user submit) + server-side guard returns [`QUERY_INVALID_NEGATION`]
- Invalid operator → [`VALIDATION_ERROR`]
- Cloud 29s timeout on unbounded query → [`QUERY_TIMEOUT`] → hint "narrow scope or add isa"
- >50k results → [`QUERY_TOO_LARGE`] → hint to narrow

**E2E:** `query-builder.spec.ts` — per-operator flows, scope selector, negation, timeout handling.

---

## W9. "Appears elsewhere" (cross-cloud)

**Trigger:** user viewing subject/probe/epoch detail, clicks "Find references".

**Happy path:**
1. `POST /api/query/appears-elsewhere { documentId, excludeDatasetId }`
2. Backend → cloud `POST /v1/ndiquery { searchstructure:[{operation:"depends_on", param1:"*", param2:<docId>}], scope: "all" if authed else "public" }`.
3. Cloud returns docs across the entire cloud referencing this document (indexed `depends_on`).
4. Backend groups by datasetId, excludes `excludeDatasetId`, returns `[{datasetId, datasetName, count, sampleDocIds[]}, ...]`.
5. Frontend renders "Referenced by N docs across M other datasets" with drill-in.

**Failure modes:**
- Zero references → "Not referenced anywhere else."
- Cloud timeout → [`QUERY_TIMEOUT`]

**E2E:** `appears-elsewhere.spec.ts`

---

## W10. Ontology cross-linking

**Trigger:** user clicks an ontology term anywhere (in a summary table, document detail, etc.)

**Happy path:**
1. Popover shows the term's definition (W12).
2. "Find all docs with this term" link → `POST /api/query { searchstructure: [{operation:"contains_string", field:"ontology.term_id", param1:"<term>"}], scope: "public" or "all" }`.
3. Note: thanks to auto-isa injection on field queries, cloud narrows to `ontology` class first, indexed. Typical response <3s cloud-wide.

**E2E:** `ontology-cross-link.spec.ts`

---

## W11. Distribution visualizations (violin / box)

**Trigger:** on a summary table, user picks numeric column → Visualize.

**Happy path:**
1. Frontend `POST /api/visualize/distribution { datasetId, className, field }`.
2. Backend: if the table data is in the proxy cache (built recently via W4), re-use. Otherwise, re-run W4 to collect.
3. Backend computes stats server-side (quartiles, density).
4. Frontend uPlot renders.

**E2E:** `distribution-viz.spec.ts`

---

## W12. Ontology enrichment popover

**Trigger:** user hovers any term ID (format: `PROVIDER:NNNN`) in the UI.

**Happy path:**
1. Frontend `GET /api/ontology/lookup?term=CL:0000540`.
2. Backend checks local SQLite cache (TTL 30d). On hit, returns `{termId, label, definition, providerUrl}`.
3. On miss, backend queries provider (EBI OLS for CL, NCBI for Taxon, SciCrunch for RRID, etc.), caches, returns.
4. Frontend popover displays.

**Failure modes:**
- Provider unreachable → [`ONTOLOGY_LOOKUP_FAILED`] (non-blocking; popover shows "Definition unavailable. {term ID}")

**E2E:** `ontology-popover.spec.ts`

---

## W13. Login / logout

**Trigger:** user visits `/login`.

**Happy path:**
1. Frontend GETs `/api/auth/csrf` → returns `{csrfToken}`, sets `XSRF-TOKEN` cookie.
2. Frontend POSTs `/api/auth/login` with `{username, password}` and `X-XSRF-TOKEN` header.
3. Backend rate-limit check (5/IP/15min, 10/user/hour).
4. Backend → cloud `POST /v1/auth/login`.
5. Cloud returns `{accessToken, refreshToken, expiresIn}`.
6. Backend generates 128-bit sessionId, encrypts tokens, writes to Redis (TTL = absolute 24h).
7. Backend sets cookie `session=<id>; HttpOnly; Secure; SameSite=Lax`.
8. Frontend redirects to `returnTo` or `/my`.

Logout:
1. `POST /api/auth/logout` with CSRF.
2. Backend → cloud `POST /v1/auth/logout` (best effort).
3. Backend deletes Redis session.
4. Backend clears cookie.
5. Frontend redirects to `/`.

**Failure modes:**
- Bad creds → [`AUTH_INVALID_CREDENTIALS`]
- Rate limited → [`AUTH_RATE_LIMITED`] with `Retry-After`
- CSRF missing → [`CSRF_INVALID`]

**E2E:** `login-logout.spec.ts`

---

## W14. Transparent access-token refresh (internal)

Not a user-visible workflow, but part of every authenticated request.

**Trigger:** authenticated request; access token within 60s of expiry or already expired.

**Flow:**
1. Auth dependency loads session from Redis.
2. Checks `access_token_expires_at`.
3. If stale:
   a. `SET NX EX 5` on key `session:<id>:refresh-lock` (prevents thundering herd).
   b. If lock acquired: `POST /v1/auth/refresh { refreshToken }`.
   c. On success: update Redis session with new `access_token`, new `expires_at`. Release lock.
   d. On failure (refresh token rejected): delete session, release lock, raise [`AUTH_EXPIRED`].
   e. If lock NOT acquired (another worker refreshing): `WAIT` on pubsub / poll with exponential backoff up to 5s, then re-read session.
4. Continue with fresh access token.

**E2E:** `session-refresh.spec.ts` — force stale token, trigger request, assert no 401 reaches browser.

---

## W15. Session expiry mid-session

**Trigger:** user has been browsing, refresh token also expires (>30d session or revoked).

**Flow:**
1. Authenticated request → W14 step 3d fires → [`AUTH_EXPIRED`].
2. Frontend API client intercepts 401 + code `AUTH_EXPIRED`.
3. Frontend stores current URL as `returnTo`, clears query cache, redirects to `/login?returnTo=...`.
4. On successful login, frontend navigates to `returnTo`.

**E2E:** `session-expiry.spec.ts`

---

## W16. Rate-limited feedback

**Trigger:** user issues requests faster than limit.

**Flow:**
1. Backend returns 429 + [`RATE_LIMITED`] + `Retry-After` header.
2. Frontend shows non-blocking toast: "Slow down, trying again in {n}s."
3. API client pauses query refetches for `Retry-After` seconds, then retries.

**E2E:** `rate-limit.spec.ts`

---

## W17. Deep link entry

**Trigger:** user clicks an email link → lands on `/datasets/:id` while unauthenticated.

**Flow:**
1. Page tries to fetch private resource → 401 + [`AUTH_REQUIRED`].
2. Frontend captures current URL as `returnTo`, redirects to `/login?returnTo=/datasets/:id`.
3. After login, redirects to `/datasets/:id`.

Same path if the dataset is public: the page renders without login.

**E2E:** `deep-link.spec.ts`

---

## Notes

- Every workflow is idempotent for GETs. Mutations (login, logout) require CSRF.
- No workflow requires local SQLite. No workflow requires offline data.
- `returnTo` is validated to only accept same-origin paths (open-redirect protection).
