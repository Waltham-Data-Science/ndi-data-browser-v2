import { expect, test } from '@playwright/test';

/**
 * M7 polish + brand + cutover. Focused on visible brand changes and the
 * new AboutPage route. Lighthouse + axe + load gates will land as CI-only
 * jobs (not blocking Playwright suite).
 */

test.describe('M7 — brand chrome', () => {
  test('header carries the NDICloud emblem', async ({ page }) => {
    await page.goto('/');
    // Emblem logo in the header.
    const emblem = page.locator('header img[src*="ndicloud-emblem"]').first();
    await expect(emblem).toBeVisible();
  });

  test('footer says Powered by NDICloud', async ({ page }) => {
    await page.goto('/');
    await expect(page.locator('footer').getByText(/Powered by NDICloud/)).toBeVisible();
  });

  test('About route renders', async ({ page }) => {
    await page.goto('/about');
    await expect(
      page.getByRole('heading', { name: /About NDICloud Data Browser/ }),
    ).toBeVisible();
    await expect(page.getByText(/The NDI data model/)).toBeVisible();
    await expect(page.getByRole('link', { name: /Browse datasets/ })).toBeVisible();
  });

  test('/api/health/version exposes rolloutPct', async ({ request }) => {
    const r = await request.get('/api/health/version');
    expect(r.ok()).toBeTruthy();
    const body = await r.json();
    expect(body.version).toBeTruthy();
    expect(typeof body.rolloutPct).toBe('number');
  });
});
