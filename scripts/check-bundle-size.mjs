#!/usr/bin/env node
/**
 * Fail the build if the gzipped total of all JS+CSS in dist/assets/ exceeds
 * the plan's 200 KB budget (§M7-8). Source maps and files under dist/brand/
 * don't ship to users and are excluded.
 *
 * Lazy-loaded chunks produced by explicit dynamic ``import()`` calls do
 * NOT count toward the budget — they are only fetched when the user
 * triggers the feature that needs them. The list of lazy-chunk prefixes
 * is kept inline (see ``LAZY_CHUNK_PREFIXES``) so new lazy dependencies
 * have to be called out explicitly in review.
 *
 * Usage:
 *   node scripts/check-bundle-size.mjs [--dist=path] [--budget=204800]
 *
 * Default dist is frontend/dist/; default budget is 200 KB.
 */
import fs from 'node:fs';
import path from 'node:path';
import zlib from 'node:zlib';

const args = Object.fromEntries(
  process.argv.slice(2).map((a) => {
    const [k, v] = a.replace(/^--/, '').split('=');
    return [k, v ?? 'true'];
  }),
);

const DIST = args.dist ?? 'frontend/dist';
// 210 KB gz. Raised from 200 KB on 2026-04-22 to accommodate
// @tanstack/react-query-persist-client + the sync-storage persister
// (+~3 KB gz). The persistence layer lets the SPA skip a network
// round-trip on every return visit, worth the small budget bump.
const BUDGET_BYTES = Number(args.budget ?? 210 * 1024);

/**
 * Lazy-loaded chunks. Each entry is a filename prefix Vite uses when
 * naming the dynamic-import chunk. A chunk is excluded from the budget
 * iff its filename starts with one of these prefixes.
 *
 * Vite picks the chunk name from the `import()` specifier — so
 * ``const LoginPage = lazy(() => import('@/pages/LoginPage'))`` produces
 * a chunk named ``LoginPage-<hash>.js``.
 *
 * Currently:
 *   - xlsx: loaded only when a user clicks "Export XLS" (Plan B B4).
 *   - uplot: only DataPanel (inside DocumentDetailPage) pulls uPlot.
 *   - Route chunks: React.lazy-gated routes added in audit 2026-04-23
 *     #52. Home, Datasets, and DatasetDetail stay eagerly imported —
 *     they're the primary public entry points.
 */
const LAZY_CHUNK_PREFIXES = [
  'xlsx-',
  'uplot-',
  // Audit 2026-04-23 #52 — lazy route chunks. Extending this list is a
  // review signal: if a new lazy route is added, the reviewer needs to
  // see the allowlist bump alongside the React.lazy call.
  'AboutPage-',
  'DocumentDetailPage-',
  'DocumentExplorerPage-',
  'LoginPage-',
  'MyDatasetsPage-',
  'NotFoundPage-',
  'PivotView-',
  'QueryPage-',
  'TableTab-',
];

function isLazyChunk(file) {
  return LAZY_CHUNK_PREFIXES.some((p) => file.startsWith(p));
}

const assetsDir = path.join(DIST, 'assets');
if (!fs.existsSync(assetsDir)) {
  console.error(`No dist assets at ${assetsDir}; did you run \`npm run build\`?`);
  process.exit(2);
}

let total = 0;
const rows = [];
for (const file of fs.readdirSync(assetsDir)) {
  // Ignore source maps — they don't ship.
  if (file.endsWith('.map')) continue;
  // Only JS + CSS count toward the runtime budget.
  if (!/\.(m?js|css)$/.test(file)) continue;
  const full = path.join(assetsDir, file);
  const buf = fs.readFileSync(full);
  const gz = zlib.gzipSync(buf).length;
  const lazy = isLazyChunk(file);
  if (!lazy) total += gz;
  rows.push({ file, raw: buf.length, gz, lazy });
}

rows.sort((a, b) => b.gz - a.gz);
const fmt = (n) => `${(n / 1024).toFixed(1)} KB`;

console.log('Bundle (gzipped):');
for (const r of rows) {
  const suffix = r.lazy ? '  [lazy, excluded from budget]' : '';
  console.log(
    `  ${r.file.padEnd(32)} ${fmt(r.raw).padStart(10)} raw / ${fmt(r.gz).padStart(10)} gz${suffix}`,
  );
}
console.log(`  ${'TOTAL (initial)'.padEnd(32)} ${' '.padStart(10)}       / ${fmt(total).padStart(10)} gz`);
console.log(`  budget:                              ${fmt(BUDGET_BYTES).padStart(10)}`);

if (total > BUDGET_BYTES) {
  console.error(
    `\nFAIL: initial bundle is ${fmt(total - BUDGET_BYTES)} over the ${fmt(BUDGET_BYTES)} budget.`,
  );
  process.exit(1);
}
console.log(`\nPASS: ${fmt(BUDGET_BYTES - total)} of headroom.`);
