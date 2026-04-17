import { expect, test } from '@playwright/test';

/**
 * M6 query + viz — QueryBuilder (simple + advanced + scope + negation),
 * ontology cross-link from popover to QueryPage, QUERY_TOO_LARGE narrowing,
 * QuickPlot embedded in SummaryTableView.
 */

const HALEY = '682e7772cdf3f24938176fac';
const VANHOOSER = '68839b1fbf243809c0800a01';

test.describe('M6 — QueryBuilder', () => {
  test('simple search for `subject` on Haley returns rows', async ({ page }) => {
    test.setTimeout(120_000);
    await page.goto('/query');
    await expect(page.getByPlaceholder(/Search by class/)).toBeVisible();
    const input = page.getByPlaceholder(/Search by class/);
    await input.fill('subject');
    // Narrow the scope via the dropdown (dataset-scoped = fastest).
    // Just use the default public scope — plenty fast with indexed isa.
    await page.getByRole('button', { name: /^Search$/ }).click();
    await expect(page.getByRole('heading', { name: /Results —/ })).toBeVisible({
      timeout: 60_000,
    });
  });

  test('advanced negation (~isa=subject) loads URL into the builder', async ({ page }) => {
    test.setTimeout(60_000);
    await page.goto(`/query?op=~isa&param1=subject`);
    // Pre-loaded from URL → advanced panel visible.
    await expect(page.getByText(/Advanced Filters/)).toBeVisible();
    // The select has the negated form selected.
    const opSelect = page.locator('select').filter({ hasText: /NOT is a/ }).first();
    await expect(opSelect).toBeVisible();
    // URL persists across re-render.
    await expect(page).toHaveURL(/op=~isa/);
    await expect(page).toHaveURL(/param1=subject/);
  });

  test('QUERY_TOO_LARGE surfaces narrowing hint (mocked)', async ({ page }) => {
    test.setTimeout(30_000);
    // Intercept the POST and return the typed QUERY_TOO_LARGE error body
    // so the test doesn't depend on the cloud's actual cap being triggered.
    await page.route('**/api/query', async (route) => {
      const req = route.request();
      if (req.method() !== 'POST') return route.fallback();
      await route.fulfill({
        status: 400,
        contentType: 'application/json',
        body: JSON.stringify({
          error: {
            code: 'QUERY_TOO_LARGE',
            message: 'Matched 120000 documents. Please narrow your query.',
            recovery: 'retry',
            requestId: 'test-1234',
          },
        }),
      });
    });
    await page.goto(`/query?op=isa&param1=ndi_document&scope=all`);
    await page.getByRole('button', { name: /Run query/ }).click();
    // Hint panel renders with one of the bullet phrases.
    await expect(
      page.getByText(/Narrow the query|isa.*clause|Restrict the scope/).first(),
    ).toBeVisible({ timeout: 10_000 });
  });
});

test.describe('M6 — ontology cross-link', () => {
  test('Find everywhere link on popover points at QueryPage with term', async ({
    page,
  }) => {
    test.setTimeout(120_000);
    await page.goto(`/datasets/${HALEY}/tables/subject`);
    await expect(page.locator('text=/1656 \\/ 1656 rows/')).toBeVisible({
      timeout: 60_000,
    });
    const ncbi = page.locator('[data-ontology-term="NCBITaxon:6239"]').first();
    await ncbi.scrollIntoViewIfNeeded();
    await page.waitForTimeout(1500);
    await ncbi.locator('button').click();
    // Popover "Find everywhere" link appears.
    const link = page.getByRole('link', { name: /Find everywhere/ }).first();
    await expect(link).toBeVisible({ timeout: 10_000 });
    const href = await link.getAttribute('href');
    expect(href).toMatch(/\/query\?op=contains_string/);
    expect(href).toMatch(/param1=NCBITaxon%3A6239/);
  });
});

test.describe('M6 — QuickPlot', () => {
  test('QuickPlot card is present under the VH subject table', async ({ page }) => {
    test.setTimeout(120_000);
    await page.goto(`/datasets/${VANHOOSER}/tables/subject`);
    await expect(page.locator('text=/32 \\/ 32 rows/')).toBeVisible({
      timeout: 60_000,
    });
    // Card collapse header.
    await expect(page.getByRole('button', { name: /Quick plot/ })).toBeVisible();
  });
});
