/**
 * Accessibility gate — `@axe-core/playwright` scans four critical routes
 * (home, dataset detail, document-explorer, query page) for axe violations.
 * Uses the pinned fixtures for deterministic scans independent of live data.
 *
 * Plan §M7-8: "axe-core zero violations."
 *
 * Rule slice: we scan the default "wcag2a, wcag2aa, wcag21a, wcag21aa,
 * best-practice" rule set. If a rule produces false positives for our UI,
 * disable it explicitly here (with a comment explaining why) rather than
 * widening the failure threshold.
 */
import AxeBuilder from '@axe-core/playwright';

import { test, expect } from './_fixtures/test';

const HALEY_ID = '682e7772cdf3f24938176fac';

// Rule exceptions (empty). Add rule IDs here only after a principled
// judgment that the rule requires a design-system change beyond the scope
// of a CI fix. Each exception MUST be tracked as a follow-up task — the
// purpose of the axe gate is to catch *new* violations; old ones should
// still be actively closed.
const DISABLED_RULES: string[] = [];

async function expectNoViolations(page: import('@playwright/test').Page): Promise<void> {
  const builder = new AxeBuilder({ page }).withTags([
    'wcag2a',
    'wcag2aa',
    'wcag21a',
    'wcag21aa',
    'best-practice',
  ]);
  if (DISABLED_RULES.length > 0) {
    builder.disableRules(DISABLED_RULES);
  }
  const { violations } = await builder.analyze();
  // Pretty-print on failure so CI logs show which rule triggered on which node.
  if (violations.length > 0) {
    console.error('axe violations:', JSON.stringify(violations, null, 2));
  }
  expect(violations).toEqual([]);
}

test.describe('a11y gate', () => {
  test('home / catalog page has no axe violations', async ({ page }) => {
    await page.goto('/');
    await page.waitForLoadState('networkidle');
    await expectNoViolations(page);
  });

  test('dataset detail page has no axe violations', async ({ page }) => {
    await page.goto(`/datasets/${HALEY_ID}/tables/subject`);
    await page.waitForLoadState('networkidle');
    await expectNoViolations(page);
  });

  test('document explorer page has no axe violations', async ({ page }) => {
    await page.goto(`/datasets/${HALEY_ID}/documents`);
    await page.waitForLoadState('networkidle');
    await expectNoViolations(page);
  });

  test('query page has no axe violations', async ({ page }) => {
    await page.goto('/query');
    await page.waitForLoadState('networkidle');
    await expectNoViolations(page);
  });
});
