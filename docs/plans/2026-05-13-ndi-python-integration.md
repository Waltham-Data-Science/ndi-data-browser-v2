# NDI-python integration plan — backend signal/edit layer

**Status:** Draft for user review. **No backend code has been written yet.**
**Audience:** Audri. Companion to `ndi-cloud-app/apps/web/docs/specs/2026-05-13-ask-checkpoint-pre-compact.md`.
**Author:** Claude Sonnet 4.5 (1M context), 2026-05-13.

## TL;DR

Three phases, escalating risk. All work happens on `ndi-data-browser-v2` (the Railway FastAPI). The `feat/experimental-ask-chat` branch in `ndi-cloud-app` is **NOT** touched — it stays draft + DO NOT MERGE. The benefit chain is bottom-up: every phase that lands on ndb-v2 main flows automatically to (a) the live Document Explorer / QuickPlot, (b) the experimental Ask chat preview, and (c) the upcoming Data Browser product.

1. **Phase A — vlt-only install (~1 day, low risk).** Add `vhlab-toolbox-python` to the Railway image (which pulls `vlt`), `apt-get install -y git` so pip can fetch the git-sourced source, and ~10 LOC in `binary_service.py` to call `vlt.file` instead of returning the `"vlt library not available"` soft error. **Unlocks Haley VHSB position-trace plotting — immediately benefits the live Document Explorer's QuickPlot for every VHSB dataset, and unblocks the Ask chat's chart prompt for Haley.** No architectural change; existing routes and tests untouched.
2. **Phase B — replace inline parsers with `database_openbinarydoc` (~1 week, medium-high risk).** Install full NDI-python (which pulls `did`, `ndr`, `vhlab-toolbox-python`, `ndi-compress`). Add a startup/cron job to call `ndi.cloud.orchestration.downloadDataset(dataset_id, /data/ndi/{id})` against a Railway persistent volume. Refactor `BinaryService.get_timeseries` to call `dataset.database_openbinarydoc(doc, filename)`. Feature-flag the swap for an A/B week; rollback = flag flip.
3. **Phase C — new rich endpoints (~1-2 weeks, low risk because additive).** New routes that *only* exist because we have NDI-python: `POST /api/datasets/:id/ndiquery` (Mongo-style structured queries via `ndi.query.Query`), `POST /api/datasets/:id/documents/:docId/edit` (auth-gated, foundation for the Data Browser product), and `GET /api/datasets/:id/elements/:elementId/native` (`ndi.element`-backed). Existing routes unchanged.

**Phase A is the only phase that should ship before Audri reviews this spec.** Phases B & C need design buy-in on the cache + volume strategy (and Phase C scope).

## Pre-flight state (2026-05-13, ~21:20 UTC)

- PR #111 merged to ndb-v2 main (commit `c5b02884`) — Railway auto-deploying the `?file=` param fix
- Ask RAG index re-baked with `binarySignalExample` sidecar (staging v3 → production, atomic)
- ndi-cloud-app `feat/experimental-ask-chat` branch (PR #160, draft) is "demo-ready" for the NBF chart path; Haley VHSB still soft-errors
- 8 published NDI Commons datasets: 3 of them are tutorial-having (Bhar, Haley, Dabrowska); Haley is VHSB-formatted

## Day 0 spike findings (research-only, no code)

### F1. The chatbots are general-purpose by design — NDI-python integration is exactly the new capability the Ask chat (and the data browser) needs

`vh-lab-chatbot` and `shrek-lab-chatbot` **don't import NDI-python.** They're general-purpose lab-document RAG systems (PDF / HTML / xlsx → pgvector + Voyage + Claude). That's deliberate. The Ask chat in `ndi-cloud-app` is the first product that *will* use NDI-python — both for richer chatbot answers (plotting, provenance walks, structured queries) AND to power richer data-browser interactions (public QuickPlot expansion, private dataset editing). The canonical "how to use NDI-python with cloud datasets" reference therefore comes from NDI-python's own surface: **`src/ndi/cloud/` package + `tests/test_cloud_*.py`** suite + the published tutorials.

### F2. Cloud connectivity = `ndi.cloud.orchestration.downloadDataset`

There is **no `ndi.cloud.Dataset(dataset_id)` lazy constructor**. The entry point is:

```python
from ndi.cloud.orchestration import downloadDataset
from ndi.cloud.client import CloudClient

dataset = downloadDataset(
    cloud_dataset_id="682e7772cdf3f24938176fac",  # Haley
    target_folder="/data/ndi/682e7772cdf3f24938176fac",
    sync_files=False,   # binaries lazy
    client=CloudClient.from_env(),
)
```

It performs (per `NDI-python/src/ndi/cloud/orchestration.py:23-186`):

1. Eagerly downloads ALL JSON documents from Mongo (chunked bulk ZIPs) — **multi-minute for real datasets** (Haley = 78K docs, ~16 GB; Carbon-fiber test = 743 docs, ~9.7 GB)
2. Rewrites each binary-file `location` in document properties to an `ndic://{dataset_id}/{file_uid}` URI
3. Materializes a local `ndi_dataset_dir` under `target_folder/{cloud_dataset_id}`
4. Stashes the authenticated `CloudClient` on the returned object as `dataset.cloud_client`

After that, binary files materialize **lazily** on the first `database_openbinarydoc(doc, filename)` call via presigned S3 URLs (`session/session_base.py:553-628` → `cloud/filehandler.py:121-177`). No `boto3` — direct `requests.get(url, stream=True)`.

**Implication:** `downloadDataset` cannot be a per-request operation. It has to run at startup or on a cron, against a persistent volume.

### F3. `vlt` is provided by `vhlab-toolbox-python`

Verified at `NDI-python/src/ndi/check.py:72-74`:

```python
# vhlab-toolbox-python
ok, detail = _try_import("vlt")
check("vhlab-toolbox-python (vlt)", ok, detail)
```

The git-pin is `vhlab-toolbox-python @ git+https://github.com/VH-Lab/vhlab-toolbox-python.git@main` (from `NDI-python/pyproject.toml:39`). Installing it alone gives us `vlt` without pulling all of NDI-python.

### F4. NDI-python git-sourced deps need `git` in the Docker image

NDI-python's four git-sourced pip deps (`did`, `ndr`, `vhlab-toolbox-python`, `ndi-compress`) need `git` available at install time. Current `infra/Dockerfile` does NOT install git (only `libjpeg62-turbo libtiff6 curl`). One `apt-get install` line fixes it.

### F5. Current `binary_service.py` has an early-return for text-VHSB

`backend/services/binary_service.py:164-184` (post-PR-#111):

```python
head = payload[:5] if len(payload) >= 5 else b""
if head.startswith(b"This "):
    return _timeseries_error(
        "vlt_library",
        "vlt library is not available on this server — full VHSB "
        "decoding requires the DID-python `vlt` extension. ...",
    )
try:
    if name.endswith(".vhsb") or (payload[:4] == b"VHSB"):
        return _parse_vhsb(payload)
    return _parse_nbf(payload)
except Exception as e:
    ...
```

So the current code:
- **Handles binary-magic VHSB** (`b"VHSB"` prefix, 24-byte header, float32 body) via the inline `_parse_vhsb`
- **Bails on text-header VHSB** (`This is a VHSB file, http://github.com/VH-Lab\n...`) with a soft error — this is the variant vlt handles

**Critical:** Audri's "Phase A is just `pip install vlt` with zero code changes" assumption is **off by one short edit**. The current code never tries to import vlt — the soft error is an early return on payload prefix. We'd need ~10 LOC to actually call `vlt.file` after installing vhlab-toolbox-python.

### F6. ndb-v2 already has numpy + scipy

`backend/requirements.txt` already pins `numpy>=2.0.0` and `scipy>=1.14.0`. These are the heavy NDI-python deps. Image growth from adding vhlab-toolbox-python alone is modest (~10-20 MB). Full NDI-python adds ~80-150 MB (did, ndr, ndi-compress + their numpy/networkx/jsonschema/openMINDS overlap with what's already there).

### F7. ndb-v2's ADR-009 bans `httpx`/`requests`/`aiohttp` in `services/`

NDI-python uses `requests` internally. The ADR-009 ban (per `backend/pyproject.toml:90-94`) is **path-scoped** — it forbids importing these libs *inside* `backend/services/`. NDI-python's own use of `requests` is fine (it's a sub-package import, not a direct service import). But if we wrap NDI-python in a `backend/services/ndi_python_service.py`, that wrapper can't directly `import requests` — only NDI-python can. Per-file carve-outs are possible if needed; this is a containable lint problem.

---

## Phase A — vlt-only install (the "free win" with one small caveat)

**Goal:** Unblock text-header VHSB decoding so Haley's position traces become plottable in the Document Explorer and the Ask chat. Everything else stays exactly as it is today.

**Scope:** the smallest possible change.

### A.1 Files to modify

| File | Change | LOC | Why |
|---|---|---|---|
| `infra/Dockerfile` | `apt-get install -y git` in the Stage 2 system-deps line | +1 | Required for pip to fetch the git-sourced `vhlab-toolbox-python` |
| `backend/requirements.txt` | Add `vhlab-toolbox-python @ git+https://github.com/VH-Lab/vhlab-toolbox-python.git@main` | +1 | Brings in `vlt` |
| `backend/pyproject.toml` | Same addition to `dependencies` | +1 | Mirror — pyproject is the source of truth for dev installs |
| `backend/services/binary_service.py` | Replace lines 164-171 (the soft-error early return) with a vlt call | ~10-15 | Actually use vlt to decode text-VHSB |
| `backend/tests/unit/test_binary_shape.py` | Add a text-VHSB fixture + decode test | +30-50 | Regression coverage |

### A.2 Concrete `binary_service.py` change

Current:
```python
head = payload[:5] if len(payload) >= 5 else b""
if head.startswith(b"This "):
    return _timeseries_error(
        "vlt_library",
        "vlt library is not available on this server — ..."
    )
try:
    if name.endswith(".vhsb") or (payload[:4] == b"VHSB"):
        return _parse_vhsb(payload)
    return _parse_nbf(payload)
```

Proposed:
```python
head = payload[:5] if len(payload) >= 5 else b""
if head.startswith(b"This "):
    # Text-header VHSB ("This is a VHSB file, http://github.com/VH-Lab\n…")
    # — DID-python's vlt extension parses the typed binary slots that
    # follow the text header. Lazy import so a missing vlt downgrades
    # cleanly rather than blowing up the worker.
    try:
        from vlt.file import vhsb_read  # type: ignore
    except ImportError:
        return _timeseries_error(
            "vlt_library",
            "vlt library is not available — install vhlab-toolbox-python.",
        )
    try:
        return _from_vlt_vhsb(vhsb_read(io.BytesIO(payload)))
    except Exception as e:
        log.warning("binary.vlt_decode_failed", error=str(e))
        return _timeseries_error("decode", f"vlt VHSB decode failed: {e}")
try:
    if name.endswith(".vhsb") or (payload[:4] == b"VHSB"):
        return _parse_vhsb(payload)
    return _parse_nbf(payload)
```

Plus a small private helper `_from_vlt_vhsb()` that converts vlt's output (likely a numpy array + sample-rate + channel-name list) into the existing `{channels, timestamps, sample_count, format, error}` envelope. Exact shape needs verification against vlt's actual API — Phase A *first action* is to read `vlt/file.py` upstream.

### A.3 Test plan

- **Unit**: synthesize a minimal text-header VHSB payload (or pull one from the Haley dataset by hand) and feed it through `BinaryService.get_timeseries` against a mocked cloud-download. Assert non-empty `channels`, correct `format == "vhsb"`, sane `sample_count`.
- **Integration**: extend `backend/tests/integration/test_routes.py` with a route test for `/api/datasets/.../documents/.../signal` against a Haley doc — requires either a recorded fixture or live cloud creds in CI. Recorded fixture is preferred (faster + no creds in CI).
- **Smoke (manual, post-deploy)**: hit `GET /api/datasets/682e7772cdf3f24938176fac/documents/<a-Haley-doc-id>/signal` against the deployed Railway URL and confirm a JSON response with non-empty channels.
- **Backward-compat**: the NBF + binary-VHSB paths are untouched. The existing 56 binary-service tests must still pass.

### A.4 Risk + rollback

| Concern | Mitigation |
|---|---|
| Image grows by ~10-20 MB | Acceptable. Heavy deps (numpy, scipy) already in. |
| `git` in image adds ~30 MB | Acceptable. One-time cost. |
| vlt's API may not match our envelope | Phase A's *first* concrete action is to read `vlt/file.py` upstream and write the helper. If the API doesn't fit, we adapt or abort Phase A; no production impact. |
| New Dockerfile layer cache miss | First Railway build will be slow (~3-5 min). Subsequent builds re-use the apt layer. |
| Text-header VHSB variant has multiple sub-formats | The vlt library handles them all (that's its job). If we discover a sub-format vlt doesn't handle, we fall through to the existing `_parse_vhsb` (binary magic path). |

**Rollback**: `git revert` the merge commit. The change is isolated to one branch / one merge; nothing else depends on it.

### A.5 Pre-flight verification needed

Before writing Phase A code:

1. **Read `vhlab-toolbox-python/src/vlt/file.py` upstream** (GitHub) and confirm the public API surface — exact function names, return shapes.
2. **Confirm Railway's `pip install` step has internet access to github.com**. (It almost certainly does, since redis/etc come from PyPI which is GitHub-backed by some mirrors, but `git+https://github.com/...` is a different code path.)
3. **Pick one Haley VHSB doc** as the smoke-test target and note its docId + filename.

### A.6 Estimated wall-clock

- 2-3 hours: read vlt's API, write the binary_service change, write the unit test
- 1 hour: smoke-test locally against a saved Haley payload (or via `httpx` against the live cloud)
- 30 min: open PR, wait for CI
- 30 min: merge, wait for Railway deploy, smoke against live URL

**Total: ~half a day.** Lower bound assumes vlt's API is well-documented and matches the shape we need.

---

## Phase B — full NDI-python (`database_openbinarydoc` swap)

**Goal:** Replace the two inline binary parsers (`_parse_nbf`, `_parse_vhsb` + the new vlt path) with a single canonical call: `dataset.database_openbinarydoc(doc, filename) → file_handle`. One source of truth for binary parsing, native multi-file selection (eliminates the `?file=` workaround entirely), and forward compatibility with any new binary formats NDI adds upstream.

### B.1 The cache + volume design question (THE main thing to decide)

`downloadDataset` is **not per-request**. It eagerly fetches Mongo metadata for a whole dataset (minutes for big ones). Three workable patterns:

**Option B-1: Persistent volume + warm cache on startup**
- Mount a Railway persistent volume at `/data/ndi/`
- On worker startup, for each of the 8 published datasets, run `downloadDataset(id, /data/ndi/{id})`
- Cache survives across deploys (volume is persistent)
- First boot is slow (potentially 30-60 min for big datasets); subsequent boots are fast (already-cached metadata)
- **Multi-replica caveat**: if Railway scales to N workers, each one re-downloads to its own volume slice. Shared-volume solutions need RWX (NFS-class). Otherwise: download once via a separate one-shot job / cron, share via S3-backed `mount`.

**Option B-2: Lazy + LRU**
- No startup work. First request for dataset X triggers `downloadDataset(X)` and the response waits.
- Sub-Pattern: a separate background job warms the top-K most-queried datasets while the worker is otherwise idle.
- Eviction: LRU on disk usage; when over budget, delete oldest dataset's `/data/ndi/{id}` dir.
- **Failure mode**: cold first-request latency is intolerable for chat UX (10-30 min). Mitigated by warming.

**Option B-3: Hybrid — startup-warm the demo datasets only**
- Audri has 8 published datasets. Pre-warm the 3 tutorial-having ones (Bhar, Haley, Dabrowska) on startup.
- For the other 5, fall through to Option B-2 (lazy + LRU).
- **Best risk/reward** for the demo era — known-good warm path for the demo prompts, fallback for everything else.

**My recommendation: Option B-3 with a `NDI_PREWARM_IDS` env var listing the dataset IDs to fetch on startup.** Cheap to implement; doesn't paint us into a corner.

### B.2 Files to modify (rough)

- `infra/Dockerfile`: install full NDI-python + add `/data` volume directive (the volume itself is configured in Railway, the Dockerfile just creates the mount-point dir)
- `infra/railway.toml`: declare the persistent volume
- `backend/requirements.txt` + `pyproject.toml`: add `ndi @ git+...`
- `backend/services/ndi_python_service.py` (NEW): wraps `ndi.cloud.orchestration.downloadDataset` + manages the in-memory `{dataset_id: ndi_dataset_dir}` cache
- `backend/services/binary_service.py`: refactor `get_timeseries` to call `ndi_python_service.open_binary(dataset_id, doc, filename)` behind a feature flag
- `backend/app.py`: startup hook that pre-warms `NDI_PREWARM_IDS` datasets in a background task
- `backend/auth/ndi_cloud.py` (NEW or extension of existing): manage the NDI Cloud JWT lifecycle (currently the FastAPI is using its own session auth; NDI-python needs `NDI_CLOUD_USERNAME` + `NDI_CLOUD_PASSWORD` env vars)
- Tests: characterization test that compares old-vs-new outputs for a known set of NBF + VHSB docs

### B.3 Feature flag + rollback plan

- Add `NDI_PYTHON_BINARY=on|off` env var (default `off` initially)
- Branch `get_timeseries`:
  - `off`: keep the existing inline parser path (today's code)
  - `on`: route to `ndi_python_service.open_binary`
- A/B for one week. Track:
  - Latency P50/P95 for `/data/timeseries`
  - Response-shape diff rate (should be 0)
  - Error rate
- Rollback: flip flag back to `off`. Worst case `git revert` the merge.

### B.4 Open questions

1. **Multi-replica strategy**: Railway's persistent volume model — is RWX supported? How does it interact with `WEB_CONCURRENCY=4`? Currently each uvicorn worker is in the same container, so they share the volume trivially. If Railway autoscales to N containers, that breaks.
2. **NDI Cloud auth lifetime**: JWT exp is ~1h per `NDI-python/src/ndi/cloud/auth.py`. We need a refresh strategy (probably refresh-on-401 via the username/password fallback path).
3. **Image build time**: full NDI-python install with 4 git-sourced deps will lengthen CI build time. Cacheable via Docker layer ordering but worth measuring.
4. **Test creds in CI**: `NDI-python/tests/test_cloud_*.py` skip when `NDI_CLOUD_USERNAME` / `PASSWORD` aren't set. Should our own integration tests require live creds, or use a recorded fixture?
5. **AWS Lambda gateway flakiness**: `test_cloud_live.py:42-68` notes the cloud API returns frequent 504s. Need retry + backoff in `ndi_python_service`.

### B.5 What this unlocks

- Eliminates the `?file=` param workaround entirely (`database_openbinarydoc` takes the filename natively)
- Supports any future binary format NDI adds (we inherit decoders for free)
- The QuickPlot in the Document Explorer now reads the same upgraded outputs (same `{channels, timestamps, ...}` envelope) → public data-browser users see VHSB decoded too, without any frontend change
- The same `ndi.dataset.Dataset` handle becomes available to the upcoming **private data browser** — same Python API researchers use locally is the cloud read/edit surface
- Lays the groundwork for Phase C

---

## Phase C — new rich endpoints

**Goal:** Add capabilities the existing REST passthrough can't provide. Purely additive; existing routes stay byte-identical.

### C.1 Proposed endpoints

- **`POST /api/datasets/:id/ndiquery`** — accepts an `ndi.query.Query`-style JSON filter. Powers the killer cross-dataset chatbot question in Ask ("compare patch-clamp in V1 across mouse and rat datasets") AND surfaces in the public data browser as a richer query builder than today's class-table filter. Backed by `dataset.database_search(q)`.
- **`POST /api/datasets/:id/documents/:docId/edit`** (auth-gated) — uses `Dataset.database_add` / `_remove` for validated document edits. Foundation for the upcoming **private Data Browser** product where logged-in users can edit their own datasets through a UI. Reuses NDI's schema validation + provenance machinery — we don't reimplement either.
- **`GET /api/datasets/:id/elements/:elementId/native`** — wraps `ndi.element` for richer single-element responses (epoch lists with native typing, probe definitions, etc.). Used by Ask chat + public data browser's element detail view.

### C.2 Risk

Low. Each is a new route. If buggy, only Ask chat (which is opt-in feature-flagged on the frontend) and the upcoming Data Browser product (which isn't shipped yet) are affected. Public Document Explorer + catalog APIs untouched.

### C.3 Out of scope for this spec

The detailed contracts (request/response shapes, error mapping, rate limits, auth gating) deserve their own spec when we get to them. Phase A + B groundwork has to land first.

---

## Concerns + mitigations (matrix)

| Concern | Phase | Mitigation |
|---|---|---|
| Docker image size grows ~150-200 MB | B | Worth it. Phase A is just ~10-20 MB. |
| Cold-start adds ~500ms for the ndi import | B | Lazy import (existing pattern in `binary_service.py`). |
| NDI-python version drift | B/C | Pin `ndi==X.Y.Z` once stable; track upstream PRs. |
| Cloud-dataset volume strategy unknown | B | THIS spec's main open design decision. My recommendation: Option B-3 (warm 3 demo IDs at startup, lazy for the rest). Audri to confirm. |
| Multi-replica scaling on Railway | B | Need to research Railway's RWX volume support. If unavailable, use a separate one-shot warmer + S3-mounted shared dir. |
| Performance regression on public Document Explorer | B | Feature flag for week-long A/B; rollback is one flag flip. |
| AWS Lambda gateway 504s | B | Retry-with-backoff wrapper around every cloud call. NDI-python's tests already document this pattern. |
| Existing ADR-009 service-layer HTTP ban | B/C | Per-file carve-out for `services/ndi_python_service.py` (precedent: `services/ontology_service.py` already has one). |
| Test creds in CI | B/C | Use recorded fixtures; reserve live-cred tests for nightly or manual. |

## Recommended sequence

1. **Now**: Audri reads this spec, signs off on the Phase A code change + the Option B-3 cache strategy (or proposes an alternative).
2. **Phase A (today / tomorrow)**: ~half-day work. New branch on ndb-v2, ~10-15 LOC change in `binary_service.py`, +1 dep, +1 apt line, +1 test. PR → CI → merge → Railway deploys → smoke against Haley. **Done.**
3. **Demo**: re-run the chart prompt against the Vercel preview. With Phase A landed, *both* Dabrowska NBF and Haley VHSB voltage traces render in the chat. **This is the moment the demo gets the second "wow" datapoint.**
4. **Phase B research week**: write a Phase B detailed spec (separate doc) with the volume + auth + multi-replica answers nailed. Audri reviews before any Phase B code.
5. **Phase B implementation week**: feature-flagged refactor, week-long A/B, then flip.
6. **Phase C**: scope each endpoint individually as a separate PR. No rush.

## Critical file pointers (so the next session can continue)

- **This spec**: `ndi-data-browser-v2/docs/plans/2026-05-13-ndi-python-integration.md` (you're reading it)
- **Companion checkpoint**: `ndi-cloud-app/apps/web/docs/specs/2026-05-13-ask-checkpoint-pre-compact.md`
- **NDI-python cloud module**: `/Users/audribhowmick/Documents/ndi-projects/NDI-python/src/ndi/cloud/`
- **NDI-python cloud tests** (the canonical "how to use it" examples): `/Users/audribhowmick/Documents/ndi-projects/NDI-python/tests/test_cloud_*.py`
- **vhlab-toolbox-python (vlt)**: `https://github.com/VH-Lab/vhlab-toolbox-python` (not cloned locally yet — need to fetch for Phase A code)
- **Current binary parser**: `ndi-data-browser-v2/backend/services/binary_service.py` (lines 164-184 are the edit target for Phase A)
- **NDI-python tutorials** (real usage patterns): `/Users/audribhowmick/Documents/ndi-projects/NDI-python/tutorials/tutorial_*.py`

## Open questions for Audri

1. **Phase A approval?** ~10 LOC + 1 dep + 1 apt line + 1 test. Risk is low. Land it before any further architectural moves?
2. **Volume strategy for Phase B?** Option B-3 (warm 3 demo IDs on startup, lazy for the rest) — agreed, or different preference?
3. **Phase B feature flag** — fine to default `off` for a week of A/B, then flip?
4. **Phase C scope** — same set of three endpoints (`ndiquery`, `edit`, `element/native`), or different priorities?
5. **NDI Cloud test creds** — should our integration tests require live creds, or recorded fixtures only?
6. **Timing** — Phase A this week, Phase B research next week, Phase B implementation week after? Or different cadence?

---

*No production code has been written for any of these phases. This document is a planning artifact only. The Phase A change is small and well-scoped; the Phase B refactor needs more design work; Phase C waits on Phase B.*
