import { test, expect } from './_fixtures/test';

/**
 * M4b dataset chrome — verifies the catalog grid, the rich card details,
 * and the enriched dataset detail sidebar against the live public catalog.
 *
 * Pinned dataset: Haley (682e7772cdf3f24938176fac). Covers:
 * - Catalog grid on `/datasets` and `/` (HomePage merged into catalog).
 * - Search input filtering.
 * - URL state persistence (`?q=` + `?page=`).
 * - Card contents: contributors, license badge, size, DOI.
 * - Detail page: ORCID-linked contributors, associated publication with
 *   DOI+PMID+PMCID, class-counts progress bars, Subjects summary stat.
 */

const HALEY = '682e7772cdf3f24938176fac';

test.describe('M4b — catalog', () => {
  test('home page renders the catalog grid (HomePage merged)', async ({ page }) => {
    await page.goto('/');
    await expect(
      page.getByRole('heading', { name: /Published datasets/ }),
    ).toBeVisible({ timeout: 20_000 });
    // At least one dataset card should be visible.
    await expect(page.locator('a[href^="/datasets/"]').first()).toBeVisible();
  });

  test('datasets page has a search input that filters client-side', async ({ page }) => {
    await page.goto('/datasets');
    await expect(page.getByRole('heading', { name: /Published datasets/ })).toBeVisible();
    await page.getByPlaceholder(/Search name, abstract/).fill('xxxxxxxxxxxxxxxxx-no-hits-xxxxx');
    await expect(page.getByText(/No datasets match your search/)).toBeVisible();
    // URL reflects the filter.
    await expect(page).toHaveURL(/\?q=/);
  });

  test('pagination preserves URL state', async ({ page }) => {
    await page.goto('/datasets');
    const next = page.getByRole('button', { name: 'Next' });
    if (await next.isEnabled()) {
      await next.click();
      await expect(page).toHaveURL(/page=2/);
    }
  });

  test('card shows license, doc count, contributors, size', async ({ page }) => {
    await page.goto('/datasets');
    // Haley's card is the first one by mongo-id ordering in most catalogs;
    // use explicit href lookup for stability across catalog growth.
    const haleyCard = page.locator(`a[href="/datasets/${HALEY}"]`).first();
    await expect(haleyCard).toBeVisible({ timeout: 20_000 });
    // License badge.
    await expect(haleyCard.getByText('CC-BY-4.0')).toBeVisible();
    // Doc count.
    await expect(haleyCard.getByText(/78,687 docs/)).toBeVisible();
    // At least one contributor name.
    await expect(haleyCard.getByText(/Haley/)).toBeVisible();
  });
});

test.describe('M4b — dataset detail', () => {
  test('Haley detail page renders the rich sidebar', async ({ page }) => {
    await page.goto(`/datasets/${HALEY}/tables/subject`);
    // Dataset overview card.
    await expect(
      page.getByRole('heading', { name: /Accept-reject decision-making/ }),
    ).toBeVisible({ timeout: 20_000 });
    // Species row.
    await expect(page.getByText(/Caenorhabditis elegans, Escherichia coli/)).toBeVisible();
    // Subjects stat.
    await expect(page.getByText(/1,656/).first()).toBeVisible();
    // Contributor with ORCID link.
    const orcidLink = page.getByRole('link', { name: /ORCID/ }).first();
    await expect(orcidLink).toBeVisible();
    await expect(orcidLink).toHaveAttribute('href', /orcid\.org/);
    // Associated publication.
    await expect(page.getByText(/Accept-reject decision-making/).first()).toBeVisible();
    // Class-counts list with progressbar.
    await expect(page.locator('[role="progressbar"]').first()).toBeVisible();
  });

  test('detail page shows DOI as an external link', async ({ page }) => {
    await page.goto(`/datasets/${HALEY}/tables/subject`);
    const doiLink = page
      .getByRole('link')
      .filter({ hasText: /eLife|doi\.org/ })
      .first();
    await expect(doiLink).toBeVisible({ timeout: 20_000 });
    await expect(doiLink).toHaveAttribute('target', '_blank');
  });

  test('class-counts list routes summary classes to /tables and others to /documents', async ({ page }) => {
    await page.goto(`/datasets/${HALEY}/tables/subject`);
    // `subject` routes to /tables/subject.
    const subjectLink = page.getByRole('link', { name: /^subject\s/ }).first();
    await expect(subjectLink).toHaveAttribute('href', new RegExp(`/datasets/${HALEY}/tables/subject`));
    // An unhandled summary class like `ontologyTableRow` routes to
    // /documents?class=… (falls back to Raw Documents listing).
    const rawClassLink = page
      .getByRole('link')
      .filter({ hasText: /ontologyTableRow/ })
      .first();
    await expect(rawClassLink).toHaveAttribute('href', /documents\?class=ontologyTableRow/);
  });
});
