## E2E fixtures

Pinned JSON responses for the endpoints most likely to drift under us:
dataset list, detail, and class counts for **Haley**
(`682e7772cdf3f24938176fac`) and **Van Hooser**
(`68839b1fbf243809c0800a01`).

Specs import `test` from `./test` instead of `@playwright/test`. That adds
a `beforeEach` which calls `page.route()` for the pinned endpoints and
serves the JSON out of `./responses/`. Anything not in the list falls
through to the real backend (in CI that backend is launched by
`.github/workflows/e2e.yml`; locally it's your `make backend`).

**Re-record a fixture**: hit prod and pipe through the included refresh
script:

```sh
make fixtures-refresh
```

This assumes `https://ndb-v2-production.up.railway.app` is reachable and
trims the bulky `documents` + `files` arrays out of dataset detail (the
frontend only reads metadata fields).

**Run against the real API, no mocks (drift-detection)**:

```sh
PLAYWRIGHT_LIVE=1 npx playwright test
```

Do that before pinning a new fixture — if the live response matches what
we have saved, the pin is safe.
