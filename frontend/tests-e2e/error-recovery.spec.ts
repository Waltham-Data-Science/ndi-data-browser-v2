import { test, expect } from './_fixtures/test';

test.describe('Error catalog UI mapping', () => {
  test('unknown dataset shows NOT_FOUND inline', async ({ page }) => {
    // A 24-char hex id that won't exist.
    await page.goto('/datasets/0000000000000000deadbeef');
    await expect(page.getByRole('alert').first()).toBeVisible({ timeout: 15000 });
  });

  test('404 route renders NotFoundPage', async ({ page }) => {
    await page.goto('/this/does/not/exist');
    await expect(page.getByRole('heading', { name: '404' })).toBeVisible();
  });
});
