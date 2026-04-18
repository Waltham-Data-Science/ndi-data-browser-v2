import { describe, expect, it } from 'vitest';

import { normalizeOrcid } from './orcid';

describe('normalizeOrcid', () => {
  it('returns undefined for null / undefined / empty / whitespace', () => {
    expect(normalizeOrcid(undefined)).toBeUndefined();
    expect(normalizeOrcid(null)).toBeUndefined();
    expect(normalizeOrcid('')).toBeUndefined();
    expect(normalizeOrcid('   ')).toBeUndefined();
  });

  it('wraps a bare ORCID id in https://orcid.org/', () => {
    expect(normalizeOrcid('0000-0001-9012-7420')).toBe(
      'https://orcid.org/0000-0001-9012-7420',
    );
  });

  it('accepts the ISO 7064 trailing-X checksum character', () => {
    expect(normalizeOrcid('0000-0001-9012-742X')).toBe(
      'https://orcid.org/0000-0001-9012-742X',
    );
    // Lowercase checksum is uppercased for canonicality.
    expect(normalizeOrcid('0000-0001-9012-742x')).toBe(
      'https://orcid.org/0000-0001-9012-742X',
    );
  });

  it('promotes scheme-less orcid.org URLs to https://', () => {
    expect(normalizeOrcid('orcid.org/0000-0001-9012-7420')).toBe(
      'https://orcid.org/0000-0001-9012-7420',
    );
    expect(normalizeOrcid('www.orcid.org/0000-0001-9012-7420')).toBe(
      'https://orcid.org/0000-0001-9012-7420',
    );
  });

  it('passes http/https URLs through verbatim', () => {
    expect(normalizeOrcid('https://orcid.org/0000-0001-9012-7420')).toBe(
      'https://orcid.org/0000-0001-9012-7420',
    );
    expect(normalizeOrcid('http://orcid.org/0000-0001-9012-7420')).toBe(
      'http://orcid.org/0000-0001-9012-7420',
    );
    // We deliberately do not validate the path here — `safeHref` is the
    // downstream scheme-check, and we want to stay schema-agnostic about
    // future ORCID URL shapes (e.g. fragment identifiers).
    expect(normalizeOrcid('https://example.com/foo')).toBe(
      'https://example.com/foo',
    );
  });

  it('trims leading/trailing whitespace before matching', () => {
    expect(normalizeOrcid('  0000-0001-9012-7420  ')).toBe(
      'https://orcid.org/0000-0001-9012-7420',
    );
    expect(normalizeOrcid('\t0000-0001-9012-7420\n')).toBe(
      'https://orcid.org/0000-0001-9012-7420',
    );
  });

  it('rejects malformed / non-ORCID shapes', () => {
    // Wrong group count.
    expect(normalizeOrcid('0001-9012-7420')).toBeUndefined();
    // Non-digit, non-X trailing char.
    expect(normalizeOrcid('0000-0001-9012-742Y')).toBeUndefined();
    // Extra group.
    expect(normalizeOrcid('0000-0001-9012-7420-0001')).toBeUndefined();
    // Name instead of id.
    expect(normalizeOrcid('Steve Van Hooser')).toBeUndefined();
    // Leading / embedded garbage.
    expect(normalizeOrcid('(0000-0001-9012-7420)')).toBeUndefined();
    expect(normalizeOrcid('ORCID: 0000-0001-9012-7420')).toBeUndefined();
  });

  it('does NOT resolve bare ids against the current page origin', () => {
    // This is the regression from Steve's 2026-04-18 feedback. Before the
    // fix, a bare id was passed through `safeHref`'s `new URL(raw, origin)`
    // and clicked links navigated to `<app-origin>/0000-…`. After the fix,
    // the normalizer synthesizes a full https://orcid.org URL first so the
    // click always lands on orcid.org.
    const href = normalizeOrcid('0000-0001-9012-7420');
    expect(href).toBeDefined();
    expect(new URL(href!).hostname).toBe('orcid.org');
  });
});
