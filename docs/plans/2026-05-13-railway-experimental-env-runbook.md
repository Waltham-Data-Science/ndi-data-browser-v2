# Railway experimental environment — setup runbook

Companion to `2026-05-13-ndi-python-integration.md`. The Phase A backend changes live on `feat/ndi-python-phase-a`; this doc walks through the **dashboard-only steps** that are required to spin up an "experimental" Railway environment pointing at that branch, so the audit can compare it against production byte-for-byte.

**Why manual?** Railway's MCP / API doesn't expose environment-creation. Environments are a project-level construct that has to be set up via the dashboard. Once it exists, all subsequent deploys + redeploys CAN be triggered programmatically.

## Pre-flight

- [ ] You're logged into Railway on the audrib's-Projects workspace
- [ ] The `feat/ndi-python-phase-a` branch is pushed to GitHub (Claude will commit + push as the last step of the implementation pass)
- [ ] You have ~10 minutes for the dashboard walk-through plus ~5 min for the first build to run

## Step-by-step

### 1. Open the project

Navigate to:

```
https://railway.com/project/81a57456-ae9a-47d0-98ef-2b5463f4815b
```

You should see the `ndi-data-browser-v2` project with three services: **ndb-v2**, **Postgres**, **Redis**, and the environment dropdown showing **"production"** in the top-left.

### 2. Create the new environment

1. Click the environment dropdown (top-left, currently "production")
2. Click **"+ New Environment"**
3. Name it **`experimental`** (lowercase, no spaces)
4. Choose **"Fork from production"** when prompted — this copies the existing services and env vars as starting points (saves us from re-entering NDI_CLOUD_USERNAME etc.). DO NOT pick "Create empty" — that's much more work.
5. Click **Create**

You should now be inside the new `experimental` environment, with copies of all three services.

### 3. Point `ndb-v2` at the feature branch

1. Click into the **`ndb-v2`** service (still in the `experimental` environment)
2. Go to **Settings** → **Service** → **Source**
3. Change the **Branch** from `main` → **`feat/ndi-python-phase-a`**
4. Save / confirm

Railway will trigger a deploy. **Wait ~3-5 minutes** for the new image (with NDI-python + git deps) to build. The Dockerfile's added `RUN python -c "from vlt.file..."` sanity check will fail the build if anything is missing, so a successful deploy = the import chain works end-to-end.

### 4. Verify Postgres + Redis are shared (or not)

The forked environment SHOULD have its own logical instances of Postgres + Redis under the same project umbrella. **Open each service in the experimental env and confirm**:

- The Postgres service inside `experimental` is a separate instance from production's. If it's NOT (i.e., it's the same `DATABASE_URL`), you have two options:
  - **(a) Share — accept the risk**: experimental writes to production's Postgres. Acceptable IF the experimental backend is read-only on Postgres (which the NDI-python paths are — they don't write).
  - **(b) Isolate — recommended**: in experimental's Postgres service settings, click **"Create new database"**. This adds a fresh empty Postgres instance for experimental only.
- Same checkbox for Redis. Redis is the cache layer; sharing it is mostly fine (cache poisoning is the only risk; experimental writes the same shape of data as production).

**My recommendation:** isolate Postgres, share Redis. Cheapest cost, lowest risk.

### 5. Get the public URL

1. Inside the `experimental` env's **ndb-v2** service, go to **Settings** → **Networking**
2. Under **Public Networking**, click **"Generate Domain"** (or similar — Railway sometimes auto-assigns)
3. Copy the resulting URL — should look like `ndb-v2-experimental-production.up.railway.app` or `ndb-v2-experimental.up.railway.app`
4. Verify it responds: `curl https://<url>/api/health` should return `{"status":"ok"}` (or similar)

### 6. Set the cloud-app preview to point at this URL

This step is on the Vercel side, NOT Railway. Two ways to do it:

**Option A — Branch-scoped env vars (recommended):**

1. Go to https://vercel.com/your-team/ndi-cloud-app/settings/environment-variables
2. For each of these vars, **add a new entry** scoped to the **Preview** environment for the **`feat/experimental-ask-chat`** branch:

```
UPSTREAM_API_URL=https://<your-experimental-railway-url>
INTERNAL_API_URL=https://<your-experimental-railway-url>
```

3. Hit **Save** for each
4. Trigger a fresh build of `feat/experimental-ask-chat` (push any commit, or click "Redeploy" in Vercel's Deployments tab)

**Option B — Just override at deploy time:**

If you don't want persistent env-var entries, you can pass them inline when triggering a redeploy from Vercel CLI:

```
vercel --prod=false env add UPSTREAM_API_URL <experimental-url> preview feat/experimental-ask-chat
```

Either way, the Vercel preview that comes out the other side should now serve the experimental backend's responses to anonymous public page requests.

### 7. Smoke-check before running the audit

Open the Vercel preview URL in an incognito browser:

- `/datasets` should load with the catalog (8 datasets)
- `/datasets/682e7772cdf3f24938176fac/documents` (Haley) should load
- Pick a Haley binary doc → expand QuickPlot → **should now render the position trace** (previously soft-errored with the vlt_library message — this is the Phase A unblock)

If any of those fail, check the Railway logs for the `ndb-v2` service in the `experimental` env via:

```
gh api /repos/Waltham-Data-Science/ndi-data-browser-v2/actions  # (or the railway-agent MCP)
```

Or pull logs directly from the dashboard.

### 8. Tell Claude the audit is ready

Reply with the two URLs and Claude will run the audit:

```
LIVE_URL=https://ndi-cloud.com
EXPERIMENTAL_URL=<your Vercel preview URL>
```

Claude will also need the experimental Railway URL (for the Layer 1 backend-API diff):

```
EXPERIMENTAL_API_URL=https://<your-experimental-railway-url>
```

## Expected cost

For the `experimental` environment with 2 replicas of ndb-v2 + isolated Postgres + shared Redis, while the audit is running:

- **ndb-v2**: ~$1-3/mo while actively serving traffic, much less idle
- **Postgres (new instance)**: ~$3-5/mo for the smallest tier
- **Redis (shared)**: $0 — already in production

**Total marginal: ~$5-10/mo while the env exists.** Pro plan's $20 monthly credit absorbs this if you're not already near the ceiling.

**Tear down after the audit:** once Phase A is decided (either merged to main or rejected), you can delete the `experimental` environment to stop the meter:

1. Project page → environment dropdown → "experimental" → "Delete Environment"

The Postgres data + Redis content go with it (the production env is untouched).

## Rollback / abort

If at any step you decide not to proceed:

- **Easiest**: delete the `experimental` environment per above. Zero impact on production.
- **More cautious**: pause the deploy on the experimental ndb-v2 service via Settings → Service → Pause. This stops the meter but preserves the setup for resumption.
