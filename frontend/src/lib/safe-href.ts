/**
 * Validate that a string is a safe, renderable URL for an <a href>.
 * Rejects javascript:, data:, vbscript:, and any other non-navigational
 * scheme. React 19 does not block these at render time (only warns in dev).
 *
 * Returns the canonical URL string if safe, or undefined if the input is
 * missing or the protocol is not one of http:, https:, mailto:. The caller
 * should treat undefined as "do not render as a clickable link".
 */
export function safeHref(raw: string | undefined | null): string | undefined {
  if (!raw) return undefined;
  // Reject whitespace-only. Per the WHATWG URL spec jsdom/browsers collapse
  // leading/trailing whitespace before parsing, so `new URL("  ", origin)`
  // resolves to the origin itself — which would turn a blank field into a
  // clickable link back to the current page. Trim-check up front.
  if (!raw.trim()) return undefined;
  try {
    const u = new URL(raw, window.location.origin);
    if (u.protocol === 'http:' || u.protocol === 'https:' || u.protocol === 'mailto:') {
      return u.toString();
    }
    return undefined;
  } catch {
    return undefined;
  }
}
