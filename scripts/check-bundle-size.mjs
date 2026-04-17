#!/usr/bin/env node
/**
 * Fail the build if the gzipped total of all JS+CSS in dist/assets/ exceeds
 * the plan's 200 KB budget (§M7-8). Source maps and files under dist/brand/
 * don't ship to users and are excluded.
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
const BUDGET_BYTES = Number(args.budget ?? 200 * 1024);

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
  total += gz;
  rows.push({ file, raw: buf.length, gz });
}

rows.sort((a, b) => b.gz - a.gz);
const fmt = (n) => `${(n / 1024).toFixed(1)} KB`;

console.log('Bundle (gzipped):');
for (const r of rows) {
  console.log(`  ${r.file.padEnd(32)} ${fmt(r.raw).padStart(10)} raw / ${fmt(r.gz).padStart(10)} gz`);
}
console.log(`  ${'TOTAL'.padEnd(32)} ${' '.padStart(10)}       / ${fmt(total).padStart(10)} gz`);
console.log(`  budget:                              ${fmt(BUDGET_BYTES).padStart(10)}`);

if (total > BUDGET_BYTES) {
  console.error(
    `\nFAIL: bundle is ${fmt(total - BUDGET_BYTES)} over the ${fmt(BUDGET_BYTES)} budget.`,
  );
  process.exit(1);
}
console.log(`\nPASS: ${fmt(BUDGET_BYTES - total)} of headroom.`);
