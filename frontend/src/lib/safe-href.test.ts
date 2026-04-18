import { describe, expect, it } from 'vitest';

import { safeHref } from './safe-href';

describe('safeHref', () => {
  describe('rejects dangerous schemes', () => {
    it('rejects javascript:', () => {
      expect(safeHref('javascript:alert(1)')).toBeUndefined();
    });

    it('rejects uppercase JAVASCRIPT: (URL normalizes case)', () => {
      expect(safeHref('JAVASCRIPT:alert(1)')).toBeUndefined();
    });

    it('rejects data: URLs', () => {
      expect(safeHref('data:text/html,<script>alert(1)</script>')).toBeUndefined();
    });

    it('rejects vbscript:', () => {
      expect(safeHref('vbscript:msgbox')).toBeUndefined();
    });

    it('rejects file:', () => {
      expect(safeHref('file:///etc/passwd')).toBeUndefined();
    });
  });

  describe('accepts safe schemes', () => {
    it('accepts https:', () => {
      expect(safeHref('https://doi.org/10.1/abc')).toBe('https://doi.org/10.1/abc');
    });

    it('accepts http:', () => {
      expect(safeHref('http://example.com/')).toBe('http://example.com/');
    });

    it('accepts mailto:', () => {
      expect(safeHref('mailto:foo@bar.com')).toBe('mailto:foo@bar.com');
    });

    it('resolves relative paths against origin', () => {
      const result = safeHref('/relative/path');
      expect(result).toBeDefined();
      expect(result).toMatch(/^https?:\/\/.+\/relative\/path$/);
    });
  });

  describe('handles missing / empty input', () => {
    it('returns undefined for undefined', () => {
      expect(safeHref(undefined)).toBeUndefined();
    });

    it('returns undefined for null', () => {
      expect(safeHref(null)).toBeUndefined();
    });

    it('returns undefined for empty string', () => {
      expect(safeHref('')).toBeUndefined();
    });

    it('returns undefined for whitespace-only string', () => {
      // URL ctor throws on whitespace-only with no base that parses — caught.
      expect(safeHref('  ')).toBeUndefined();
    });
  });
});
