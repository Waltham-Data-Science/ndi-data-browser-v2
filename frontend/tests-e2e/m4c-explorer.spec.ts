import { expect, test } from './_fixtures/test';

/**
 * M4c DocumentExplorerPage — Summary Tables / Raw Documents toggle.
 * Pinned dataset: VH (smaller, faster cold-cache than Haley).
 */

const VANHOOSER = '68839b1fbf243809c0800a01';

test.describe('M4c — document explorer toggle', () => {
  test('toggle switches between Summary Tables and Raw Documents', async ({ page }) => {
    test.setTimeout(120_000);
    await page.goto(`/datasets/${VANHOOSER}/documents`);
    // Default mode = summary → embedded TableTab renders TableSelector.
    await expect(page.getByRole('tab', { name: /Subjects/ })).toBeVisible({
      timeout: 20_000,
    });
    // Flip to Raw Documents via the top-right toggle.
    await page.getByRole('button', { name: /Raw Documents/ }).click();
    await expect(page).toHaveURL(/\?mode=raw/);
    // Two "Document classes" headings (sidebar + raw pane) — use the
    // listbox role to target the raw-pane selector specifically.
    await expect(page.getByRole('listbox', { name: /Document classes/ })).toBeVisible({
      timeout: 20_000,
    });
    // And back.
    await page.getByRole('button', { name: /Summary Tables/ }).click();
    await expect(page.getByRole('tab', { name: /Subjects/ })).toBeVisible();
  });

  test('Raw Documents pane filters by class', async ({ page }) => {
    test.setTimeout(120_000);
    await page.goto(`/datasets/${VANHOOSER}/documents?mode=raw`);
    const listbox = page.getByRole('listbox', { name: /Document classes/ });
    await expect(listbox).toBeVisible({ timeout: 30_000 });
    // Scope the click to the raw-pane listbox so it can't accidentally
    // match the sidebar's class list in DatasetDetailPage.
    const subjectOption = listbox
      .locator('button')
      .filter({ hasText: /^subject\s*\d/ })
      .first();
    await expect(subjectOption).toBeVisible({ timeout: 10_000 });
    await subjectOption.click();
    await expect(page).toHaveURL(/class=subject/);
  });

  test('Raw Documents row-click navigates to M5 detail page', async ({ page }) => {
    test.setTimeout(120_000);
    await page.goto(`/datasets/${VANHOOSER}/documents?mode=raw&class=subject`);
    // Wait for the list.
    await expect(page.locator('table').first()).toBeVisible({ timeout: 30_000 });
    const firstRowLink = page.locator('tbody a[href*="/documents/"]').first();
    await expect(firstRowLink).toBeVisible({ timeout: 30_000 });
    const href = await firstRowLink.getAttribute('href');
    expect(href).toMatch(/\/datasets\/[a-f0-9]{24}\/documents\/[a-f0-9]{24}/);
  });
});
