import { test, expect } from './_fixtures/test';

test.describe('Public catalog browsing (W1)', () => {
  test('lists published datasets and paginates', async ({ page }) => {
    await page.goto('/datasets');
    await expect(page.getByRole('heading', { name: 'Published datasets' })).toBeVisible();
    // At least one dataset card should appear.
    const firstCard = page.locator('a[href^="/datasets/"]').first();
    await expect(firstCard).toBeVisible();
    // Pagination controls.
    await expect(page.getByRole('button', { name: 'Previous' })).toBeVisible();
    await expect(page.getByRole('button', { name: 'Next' })).toBeVisible();
  });

  test('search filters the list', async ({ page }) => {
    await page.goto('/datasets');
    await page.getByLabel('Search datasets').fill('nonsense-should-match-nothing-xyz');
    await expect(page.getByText('No datasets match your search.')).toBeVisible();
  });

  test('clicking a dataset loads the detail page', async ({ page }) => {
    await page.goto('/datasets');
    const firstCard = page.locator('a[href^="/datasets/"]').first();
    await firstCard.click();
    await expect(page).toHaveURL(/\/datasets\/[a-f0-9]{24}(\/|$)/);
  });
});
