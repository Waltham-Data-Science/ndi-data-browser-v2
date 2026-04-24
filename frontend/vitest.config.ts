import { defineConfig } from 'vitest/config';
import react from '@vitejs/plugin-react-swc';
import path from 'node:path';

export default defineConfig({
  plugins: [react()],
  resolve: {
    alias: {
      '@': path.resolve(__dirname, './src'),
    },
  },
  test: {
    environment: 'jsdom',
    include: ['src/**/*.{test,spec}.{ts,tsx}'],
    exclude: ['tests-e2e/**', 'node_modules/**', 'dist/**'],
    setupFiles: ['src/test/setup.ts'],
    passWithNoTests: true,
    // Coverage gate (audit 2026-04-23, #73). Backend has a hard 70%
    // floor via --cov-fail-under; before this change the frontend had
    // zero regression protection — a PR could delete every test file
    // and CI stayed green. Thresholds are set just below the measured
    // baseline so this PR doesn't need to raise numbers; ratchet up
    // deliberately as coverage improves, matching the backend pattern
    // (fail_under = 70 is also a deliberate floor, not aspirational).
    // Measured 2026-04-24: statements 37.46, branches 27.63,
    // functions 35.19, lines 38.05.
    coverage: {
      provider: 'v8',
      reporter: ['text', 'lcov', 'json-summary'],
      include: ['src/**/*.{ts,tsx}'],
      exclude: [
        'src/**/*.test.{ts,tsx}',
        'src/**/*.spec.{ts,tsx}',
        'src/test/**',
        'src/**/*.d.ts',
        'src/main.tsx',
      ],
      thresholds: {
        statements: 37,
        branches: 27,
        functions: 35,
        lines: 38,
      },
    },
  },
});
