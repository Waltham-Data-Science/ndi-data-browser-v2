import { test, expect } from './_fixtures/test';

test.describe('Authentication (W13, W15)', () => {
  test('login page renders', async ({ page }) => {
    await page.goto('/login');
    await expect(page.getByRole('heading', { name: 'Sign in' })).toBeVisible();
    await expect(page.getByLabel('Email')).toBeVisible();
    await expect(page.getByLabel('Password')).toBeVisible();
  });

  test('invalid credentials show typed error', async ({ page }) => {
    await page.goto('/login');
    await page.getByLabel('Email').fill('noone@example.com');
    await page.getByLabel('Password').fill('nope');
    await page.getByRole('button', { name: 'Sign in' }).click();
    // Expect an alert role to surface the error.
    await expect(page.getByRole('alert')).toBeVisible({ timeout: 15000 });
  });

  test('unauthenticated visit to /my redirects to login', async ({ page }) => {
    await page.goto('/my');
    // MyDatasetsPage routes the login-required error state via <ErrorState>.
    await expect(page.getByText(/sign in|log in/i).first()).toBeVisible();
  });
});
