/**
 * Runtime config — exposed to the frontend at build time via Vite env vars.
 *
 * Set in deployment config:
 *   VITE_MARKETING_URL   → base URL of the marketing site (ndi-cloud.com)
 *                          Used for cross-domain nav links from the data browser
 *                          back to marketing pages (About, Platform, LabChat, etc.)
 *
 * Defaults assume production. For staging, set:
 *   VITE_MARKETING_URL=https://staging.ndi-cloud.com
 */

/** Base URL of the marketing/auth site. Used for cross-domain links. */
export const MARKETING_URL: string =
  import.meta.env.VITE_MARKETING_URL ?? 'https://ndi-cloud.com';

/** Build a full marketing-site URL for a given path. */
export function marketingHref(path: string): string {
  const base = MARKETING_URL.replace(/\/+$/, '');
  const p = path.startsWith('/') ? path : `/${path}`;
  return `${base}${p}`;
}

/** External documentation URL (NDI-matlab docs site). */
export const DOCS_URL = 'https://vh-lab.github.io/NDI-matlab/';
