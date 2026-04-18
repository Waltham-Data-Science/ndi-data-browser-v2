/**
 * Normalize an ORCID identifier to a full `https://orcid.org/…` URL.
 *
 * The cloud API's contributor records are documented to carry a full ORCID
 * URL, but in practice some records ship only the bare identifier
 * (`0000-0001-9012-7420`) or a scheme-less `orcid.org/…` form. Handing any
 * of those directly to `<a href>` is buggy:
 *
 *   - `new URL("0000-0001-9012-7420", location.origin)` resolves to our own
 *     app origin, so the link looks valid to `safeHref` but navigates back
 *     to the app (Steve's 2026-04-18 feedback on the Griswold/Van Hooser
 *     contributor row).
 *   - `new URL("orcid.org/…", location.origin)` has the same problem.
 *
 * This helper returns:
 *   - A full `https://orcid.org/<id>` URL when the input is a recognized
 *     ORCID form.
 *   - A pass-through of the raw string when it already starts with
 *     `http://` / `https://` — we let `safeHref` validate the scheme at
 *     render time.
 *   - `undefined` for empty, whitespace-only, or unrecognized input —
 *     callers should hide the ORCID affordance entirely in that case so
 *     the user doesn't click a stub link.
 *
 * ORCID id syntax per the official spec: 16 digits grouped 4-4-4-4 with
 * hyphens, the last character may be `X` (ISO 7064 Mod 11-2 check digit).
 * We match that shape exactly; anything shorter or garbled is rejected so
 * a typo doesn't quietly synthesize a dead orcid.org URL.
 */
const BARE_ORCID_RE = /^\d{4}-\d{4}-\d{4}-\d{3}[\dX]$/i;
const ORCID_HOST_RE = /^(?:www\.)?orcid\.org\/(\d{4}-\d{4}-\d{4}-\d{3}[\dX])$/i;

export function normalizeOrcid(raw: string | undefined | null): string | undefined {
  if (!raw) return undefined;
  const trimmed = raw.trim();
  if (!trimmed) return undefined;

  // Already a full http(s) URL — pass through; `safeHref` validates.
  if (/^https?:\/\//i.test(trimmed)) {
    return trimmed;
  }

  // `orcid.org/XXXX-XXXX-XXXX-XXXX` (scheme-less) — synthesize https://.
  const hostMatch = ORCID_HOST_RE.exec(trimmed);
  if (hostMatch) {
    return `https://orcid.org/${hostMatch[1].toUpperCase()}`;
  }

  // Bare `XXXX-XXXX-XXXX-XXXX` — wrap in https://orcid.org/.
  if (BARE_ORCID_RE.test(trimmed)) {
    return `https://orcid.org/${trimmed.toUpperCase()}`;
  }

  // Unknown shape — don't guess.
  return undefined;
}
