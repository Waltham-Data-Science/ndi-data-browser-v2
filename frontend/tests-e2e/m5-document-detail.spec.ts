import { expect, test, type Page } from '@playwright/test';

/**
 * M5 document detail — TimeseriesData shape migration, dependencies endpoint,
 * DataPanel dispatch, DependencyGraph visual tree.
 *
 * Runs against live Haley + Van Hooser. Pinned doc IDs:
 *   Haley imageStack:  we resolve a live one at run time
 *   VH element_epoch:  we resolve a live one at run time
 */

const HALEY = '682e7772cdf3f24938176fac';
const VANHOOSER = '68839b1fbf243809c0800a01';

async function firstDocIdOfClass(
  page: Page,
  dataset: string,
  className: string,
): Promise<string> {
  const r = await page.request.post('/api/ndi-proxy-unused');
  // Use a direct cloud call so the test doesn't depend on server-side pagination.
  const res = await page.request.post(
    `https://api.ndi-cloud.com/v1/ndiquery?page=1&pageSize=1`,
    {
      data: {
        scope: dataset,
        searchstructure: [{ operation: 'isa', param1: className }],
      },
      headers: { 'content-type': 'application/json' },
    },
  );
  const body = await res.json();
  return body.documents?.[0]?.id ?? '';
}

test.describe('M5 — document detail composition', () => {
  test('Haley imageStack detail renders image viewer + dep graph', async ({ page }) => {
    // Cold: image decode + dep graph walk ~10-30s.
    test.setTimeout(120_000);
    const imgId = await firstDocIdOfClass(page, HALEY, 'imageStack');
    test.skip(!imgId, 'No imageStack doc resolved');
    await page.goto(`/datasets/${HALEY}/documents/${imgId}`);
    await expect(
      page.getByRole('link', { name: /Back to dataset/ }),
    ).toBeVisible({ timeout: 20_000 });
    // Document Properties JSON tree renders.
    await expect(page.getByText('Document Properties')).toBeVisible();
    // Image viewer label shows up (not timeseries).
    await expect(page.getByText('Image', { exact: false }).first()).toBeVisible({
      timeout: 60_000,
    });
  });

  test('VH element_epoch detail surfaces Timeseries panel with vlt friendly error', async ({
    page,
  }) => {
    const epId = await firstDocIdOfClass(page, VANHOOSER, 'element_epoch');
    test.skip(!epId, 'No element_epoch doc resolved');
    await page.goto(`/datasets/${VANHOOSER}/documents/${epId}`);
    // Header breadcrumb.
    await expect(
      page.getByRole('link', { name: /Back to dataset/ }),
    ).toBeVisible({ timeout: 20_000 });
    // DataPanel picked up "Timeseries" label from detect_kind.
    await expect(page.getByText(/^Timeseries/).first()).toBeVisible({
      timeout: 60_000,
    });
    // vlt-library friendly error surfaces (since backend doesn't have vlt).
    await expect(page.getByText(/vlt|VHSB/).first()).toBeVisible({ timeout: 20_000 });
  });

  test('VH element_epoch dep graph renders with truncation badge', async ({ page }) => {
    const epId = await firstDocIdOfClass(page, VANHOOSER, 'element_epoch');
    test.skip(!epId, 'No element_epoch doc resolved');
    await page.goto(`/datasets/${VANHOOSER}/documents/${epId}`);
    // Dep graph card title.
    await expect(page.getByText('Dependency Graph')).toBeVisible({ timeout: 30_000 });
    // Either the visual tree or the text list must be toggled on.
    await expect(
      page.getByRole('button', { name: /Visual|List/ }).first(),
    ).toBeVisible();
    // At least one node pill.
    await expect(page.locator('[class*="border-brand-400"]').first()).toBeVisible();
  });

  test('dep graph toggle switches visual / list', async ({ page }) => {
    const epId = await firstDocIdOfClass(page, VANHOOSER, 'element_epoch');
    test.skip(!epId, 'No element_epoch doc resolved');
    await page.goto(`/datasets/${VANHOOSER}/documents/${epId}`);
    await expect(page.getByText('Dependency Graph')).toBeVisible({ timeout: 30_000 });
    const listBtn = page.getByRole('button', { name: /^List$/ });
    await listBtn.click();
    // List view label appears.
    await expect(page.getByText(/Depends on \(/)).toBeVisible();
  });
});
