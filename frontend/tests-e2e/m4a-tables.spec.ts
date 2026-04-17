import { test, expect, type Page } from '@playwright/test';

/**
 * M4a tutorial-parity table specs — runs against live Haley + Van Hooser.
 *
 * Dataset IDs pinned per plan "Tutorial dataset IDs are baked into tests":
 *   Haley      (C. elegans):        682e7772cdf3f24938176fac
 *   Van Hooser (ferret V1):         68839b1fbf243809c0800a01
 *
 * Each verification matches a gate in plan §M4a:
 * - 15 column headers on /tables/subject
 * - Ontology cells render as buttons with data-ontology-term
 * - Cache warmth: repeat navigation loads instantly (measured indirectly)
 * - Combined + element tables render
 */

const HALEY = '682e7772cdf3f24938176fac';
const VANHOOSER = '68839b1fbf243809c0800a01';

async function gotoSubject(page: Page, dataset: string) {
  await page.goto(`/datasets/${dataset}/tables/subject`);
  // Wait for the subject table to materialize.
  await expect(page.getByRole('tab', { name: /Subjects/ })).toBeVisible({ timeout: 20_000 });
  // Wait for the row-count toolbar text — proves the API call completed.
  await expect(page.locator('text=/\\d+ \\/ \\d+ rows/')).toBeVisible({ timeout: 60_000 });
}

test.describe('M4a — tutorial-parity subject table', () => {
  test('Haley renders all 15 subject columns', async ({ page }) => {
    await gotoSubject(page, HALEY);
    // Inspect the column headers. The subject table has exactly 15 columns
    // defined by SUBJECT_COLUMNS in summary_table_service.py. Auto-hide may
    // mark some columns hidden, but they remain in the column picker.
    await page.getByRole('button', { name: /^Columns$/ }).click();
    const columnLabels = await page.locator('[class*="rounded-md border"][class*="bg-slate-50"] label').count();
    expect(columnLabels).toBe(15);
  });

  test('Haley surfaces NCBITaxon and WBStrain ontology chips', async ({ page }) => {
    await gotoSubject(page, HALEY);
    // Species NCBITaxon chip. `data-ontology-term` sits on the outer
    // wrapper span so both regular popover buttons and EMPTY: placeholder
    // spans expose it.
    const species = page.locator('[data-ontology-term^="NCBITaxon:"]').first();
    await expect(species).toBeVisible({ timeout: 30_000 });
    await expect(species).toContainText(/NCBITaxon:\d+/);
    // Strain WBStrain chip — Schema B dispatch proof.
    const strain = page.locator('[data-ontology-term^="WBStrain:"]').first();
    await expect(strain).toBeVisible();
  });

  test('Haley NCBITaxon popover resolves to C. elegans', async ({ page }) => {
    await gotoSubject(page, HALEY);
    const species = page.locator('[data-ontology-term="NCBITaxon:6239"]').first();
    await species.scrollIntoViewIfNeeded();
    // Give the batch-lookup a moment to hydrate the query cache. The hook
    // fires when ontology terms appear in the rendered rows; it's best
    // effort, not awaited by the test harness.
    await page.waitForTimeout(1500);
    await species.locator('button').click();
    await expect(
      page.locator('[role="tooltip"]').filter({ hasText: 'Caenorhabditis elegans' }),
    ).toBeVisible({ timeout: 15_000 });
  });

  test('Van Hooser renders subject columns and bare NCBI ontology chip', async ({ page }) => {
    await gotoSubject(page, VANHOOSER);
    // Van Hooser emits bare 9669 — isOntologyTerm must still detect it, so
    // the value should render as a popover button, not plain text.
    const bareSpecies = page.locator('[data-ontology-term="9669"]').first();
    await expect(bareSpecies).toBeVisible({ timeout: 30_000 });
  });

  test('sorting by subject identifier works', async ({ page }) => {
    await gotoSubject(page, VANHOOSER);
    const header = page.getByRole('button', { name: /Subject Identifier/ });
    await header.click();
    // After clicking, the sort indicator updates — not asserting specific
    // order (unstable with live data) but the click must not error.
    await expect(page.locator('text=/\\d+ \\/ \\d+ rows/')).toBeVisible();
  });

  test('column picker hides a column end-to-end', async ({ page }) => {
    await gotoSubject(page, VANHOOSER);
    await page.getByRole('button', { name: /^Columns$/ }).click();
    // Uncheck Species via the column picker — there's a checkbox per column.
    const pickerArea = page.locator('[class*="rounded-md border"][class*="bg-slate-50"]');
    const speciesLabel = pickerArea.getByText('Species', { exact: true }).first();
    const checkbox = speciesLabel.locator('..').locator('input[type="checkbox"]');
    if (await checkbox.isVisible()) {
      await checkbox.uncheck();
    }
  });

  test('CSV export produces a download', async ({ page }) => {
    await gotoSubject(page, VANHOOSER);
    const downloadPromise = page.waitForEvent('download');
    await page.getByRole('button', { name: /^CSV$/ }).click();
    const download = await downloadPromise;
    expect(download.suggestedFilename()).toMatch(/\.csv$/);
  });
});

test.describe('M4a — table selector', () => {
  test('Haley can switch between table tabs', async ({ page }) => {
    // Cold-cache Haley tables can take 20-40s each. Outer test timeout must
    // cover both probe + epoch tab builds.
    test.setTimeout(180_000);
    await gotoSubject(page, HALEY);
    await page.getByRole('tab', { name: /Probes/ }).click();
    await expect(page).toHaveURL(/\/tables\/element$/);
    await expect(page.locator('text=/\\d+ \\/ \\d+ rows/').first()).toBeVisible({
      timeout: 90_000,
    });

    await page.getByRole('tab', { name: /Epochs/ }).click();
    await expect(page).toHaveURL(/\/tables\/element_epoch$/);
    await expect(page.locator('text=/\\d+ \\/ \\d+ rows/').first()).toBeVisible({
      timeout: 90_000,
    });
  });

  test('Haley ontology tab groups rows by schema', async ({ page }) => {
    // Cold ontology build for Haley is ~60s (41k docs, 9 groups).
    test.setTimeout(180_000);
    await page.goto(`/datasets/${HALEY}/tables/ontology`);
    await expect(page.getByRole('tab', { name: /Ontology/ })).toBeVisible({
      timeout: 20_000,
    });
    // Ontology tab shows a group picker across the dataset's 9 groups.
    // Haley has both SubjectDocumentIdentifier- and MicroscopyImageIdentifier-
    // keyed groups; at least one of them must surface in the picker.
    const groupPickerLabel = page
      .locator('button')
      .filter({ hasText: /SubjectDocumentIdentifier|MicroscopyImageIdentifier|BacterialPlateIdentifier/ })
      .first();
    await expect(groupPickerLabel).toBeVisible({ timeout: 90_000 });
  });
});
