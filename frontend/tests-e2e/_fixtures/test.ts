/**
 * Drop-in replacement for `@playwright/test`. Specs import `test` + `expect`
 * from here; the `test` fixture installs JSON route interception for the
 * pinned endpoints unless `PLAYWRIGHT_LIVE=1` is set.
 *
 * Only endpoints whose live responses we've explicitly saved under
 * `./responses/` are mocked. Everything else falls through to the backend
 * the spec is running against, matching v1-era "live dataset" behavior
 * but without being at the mercy of cloud catalog drift for the bits we
 * assert on (dataset list + detail + class counts for Haley/VH).
 *
 * Plan §M7-6: "Prevents live-data drift breakage."
 */
import fs from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

import { test as base, type Page } from '@playwright/test';

export { expect } from '@playwright/test';

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const RESPONSES = path.join(__dirname, 'responses');

const HALEY_ID = '682e7772cdf3f24938176fac';
const VH_ID = '68839b1fbf243809c0800a01';

function readJson(name: string): string {
  return fs.readFileSync(path.join(RESPONSES, name), 'utf8');
}

/**
 * Ordered list of [matcher, response-body-file] pairs. First matching
 * route wins. We use URL regexes rather than glob strings so that the
 * query-string suffix on `?page=1&pageSize=20` doesn't get in the way.
 */
const ROUTES: Array<{ match: RegExp; file: string; status?: number }> = [
  // Dataset list (both paginated and unpaginated — page=1 pageSize=20 covers
  // the default view; no pagination in fixtures means page=2 falls through).
  { match: /\/api\/datasets\/published(\?.*)?$/, file: 'datasets-published.json' },

  // Dataset detail.
  { match: new RegExp(`/api/datasets/${HALEY_ID}(\\?.*)?$`), file: 'haley-detail.json' },
  { match: new RegExp(`/api/datasets/${VH_ID}(\\?.*)?$`), file: 'vh-detail.json' },

  // Class counts.
  {
    match: new RegExp(`/api/datasets/${HALEY_ID}/document-class-counts(\\?.*)?$`),
    file: 'haley-classcounts.json',
  },
  {
    match: new RegExp(`/api/datasets/${VH_ID}/document-class-counts(\\?.*)?$`),
    file: 'vh-classcounts.json',
  },
];

async function installMocks(page: Page): Promise<void> {
  await page.route('**/api/**', async (route, request) => {
    const url = request.url();
    for (const r of ROUTES) {
      if (r.match.test(url)) {
        await route.fulfill({
          status: r.status ?? 200,
          contentType: 'application/json',
          body: readJson(r.file),
        });
        return;
      }
    }
    // Unknown endpoint — let the real backend handle it.
    await route.continue();
  });
}

/**
 * `test` with a `page` fixture override. If `PLAYWRIGHT_LIVE=1`, behaves
 * identically to `@playwright/test`; otherwise installs the pinned-route
 * interceptor above before the test starts.
 */
export const test = base.extend({
  page: async ({ page }, use) => {
    if (!process.env.PLAYWRIGHT_LIVE) {
      await installMocks(page);
    }
    await use(page);
  },
});
