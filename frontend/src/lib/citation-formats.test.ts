/**
 * citation-formats — unit tests for BibTeX, RIS, and plain-text
 * generators. The generators are pure functions so these are cheap
 * snapshot-style tests against canonical input.
 */
import { describe, expect, it } from 'vitest';

import {
  bibtexAuthors,
  bibtexCiteKey,
  bibtexEscape,
  stripDoiPrefix,
  toBibtex,
  toPlainText,
  toRis,
} from './citation-formats';
import type { DatasetSummaryCitation } from '@/types/dataset-summary';

function base(
  overrides: Partial<DatasetSummaryCitation> = {},
): DatasetSummaryCitation {
  return {
    title: 'Acute slice recordings from BNST neurons',
    license: 'CC-BY-4.0',
    datasetDoi: 'https://doi.org/10.63884/abc123',
    paperDois: ['https://doi.org/10.1038/sdata.2026.1'],
    contributors: [
      { firstName: 'Ada', lastName: 'Lovelace', orcid: 'https://orcid.org/0000-0001' },
      { firstName: 'Steve', lastName: 'Van Hooser', orcid: null },
    ],
    year: 2026,
    ...overrides,
  };
}

describe('stripDoiPrefix', () => {
  it('strips https://doi.org/ prefix', () => {
    expect(stripDoiPrefix('https://doi.org/10.63884/abc')).toBe('10.63884/abc');
  });
  it('strips http://dx.doi.org/ prefix case-insensitively', () => {
    expect(stripDoiPrefix('HTTP://DX.DOI.ORG/10.1/xyz')).toBe('10.1/xyz');
  });
  it('passes bare DOIs through unchanged', () => {
    expect(stripDoiPrefix('10.63884/abc')).toBe('10.63884/abc');
  });
});

describe('bibtexCiteKey', () => {
  it('composes lastname + year + first stemword', () => {
    expect(bibtexCiteKey(base())).toBe('lovelace2026_acute');
  });
  it('handles missing contributor gracefully', () => {
    expect(bibtexCiteKey(base({ contributors: [], year: 2025 }))).toBe(
      'ndidataset2025_acute',
    );
  });
  it('handles missing year', () => {
    expect(bibtexCiteKey(base({ year: null }))).toBe('lovelacend_acute');
  });
  it('strips accents + non-ASCII from surname', () => {
    const c = base({
      contributors: [{ firstName: 'Erika', lastName: 'Müller-Çelik', orcid: null }],
    });
    // NFD strips accents but leaves the ASCII base letter (Ç→C), then the
    // non-letter hyphen is dropped.
    expect(bibtexCiteKey(c)).toBe('mullercelik2026_acute');
  });
});

describe('bibtexEscape', () => {
  it('escapes braces, ampersand, percent, dollar, hash, underscore, tilde, caret', () => {
    const raw = 'A{B}C & D% E$ F# G_ H~ I^';
    const out = bibtexEscape(raw);
    expect(out).toContain('\\{B\\}');
    expect(out).toContain('\\&');
    expect(out).toContain('\\%');
    expect(out).toContain('\\$');
    expect(out).toContain('\\#');
    expect(out).toContain('\\_');
    expect(out).toContain('\\~{}');
    expect(out).toContain('\\^{}');
  });
});

describe('bibtexAuthors', () => {
  it('joins Last, First and Last, First', () => {
    expect(bibtexAuthors(base().contributors)).toBe(
      'Lovelace, Ada and Van Hooser, Steve',
    );
  });
  it('returns empty string for an empty list', () => {
    expect(bibtexAuthors([])).toBe('');
  });
});

describe('toBibtex', () => {
  it('emits a @dataset entry with dataset DOI primary', () => {
    const out = toBibtex(base());
    expect(out).toMatch(/^@dataset\{lovelace2026_acute,/);
    expect(out).toContain('title = {Acute slice recordings from BNST neurons}');
    expect(out).toContain('author = {Lovelace, Ada and Van Hooser, Steve}');
    expect(out).toContain('year = {2026}');
    expect(out).toContain('publisher = {NDI Cloud}');
    expect(out).toContain('doi = {10.63884/abc123}');
    expect(out).toContain('url = {https://doi.org/10.63884/abc123}');
    expect(out).toContain('note = {License: CC-BY-4.0}');
    expect(out).toContain('addendum = {Associated paper DOI: 10.1038/sdata.2026.1}');
    expect(out).toMatch(/\}\n$/);
  });
  it('preserves paper DOI when user picks it as the primary', () => {
    const out = toBibtex(base(), { doi: 'paper' });
    expect(out).toContain('doi = {10.1038/sdata.2026.1}');
  });
  it('omits doi field when no DOI is available', () => {
    const out = toBibtex(base({ datasetDoi: null, paperDois: [] }));
    expect(out).not.toContain('doi = {');
  });
});

describe('toRis', () => {
  it('emits TY  - DATA record with AU / PY / DO / UR / ER', () => {
    const out = toRis(base());
    expect(out.startsWith('TY  - DATA\n')).toBe(true);
    expect(out).toContain('T1  - Acute slice recordings from BNST neurons');
    expect(out).toContain('AU  - Lovelace, Ada');
    expect(out).toContain('AU  - Van Hooser, Steve');
    expect(out).toContain('PY  - 2026');
    expect(out).toContain('PB  - NDI Cloud');
    expect(out).toContain('DO  - 10.63884/abc123');
    expect(out).toContain('DO  - 10.1038/sdata.2026.1');
    expect(out).toContain('UR  - https://doi.org/10.63884/abc123');
    expect(out).toContain('C1  - License: CC-BY-4.0');
    // terminator must be present
    expect(out).toMatch(/\nER {2}- \n$/);
  });
  it('skips missing optional fields', () => {
    const out = toRis(
      base({ year: null, license: null, datasetDoi: null, paperDois: [] }),
    );
    expect(out).not.toContain('PY  -');
    expect(out).not.toContain('C1  -');
    expect(out).not.toContain('DO  -');
    expect(out).not.toContain('UR  -');
  });
});

describe('toPlainText', () => {
  it('formats per the amendment pattern', () => {
    const out = toPlainText(base());
    expect(out).toBe(
      'Lovelace A and Van Hooser S. Acute slice recordings from BNST neurons. NDI Cloud. doi:10.63884/abc123. (Upload year: 2026). License: CC-BY-4.0.',
    );
  });
  it('handles a single contributor', () => {
    const out = toPlainText(
      base({ contributors: [{ firstName: 'Ada', lastName: 'Lovelace', orcid: null }] }),
    );
    expect(out.startsWith('Lovelace A.')).toBe(true);
  });
  it('handles three contributors with Oxford comma before and', () => {
    const out = toPlainText(
      base({
        contributors: [
          { firstName: 'A', lastName: 'One', orcid: null },
          { firstName: 'B', lastName: 'Two', orcid: null },
          { firstName: 'C', lastName: 'Three', orcid: null },
        ],
      }),
    );
    expect(out.startsWith('One A, Two B, and Three C.')).toBe(true);
  });
  it('labels year as "Upload year" (not publication year)', () => {
    const out = toPlainText(base());
    expect(out).toContain('Upload year: 2026');
    expect(out).not.toContain('Publication year');
  });
  it('skips DOI clause when datasetDoi is missing', () => {
    const out = toPlainText(base({ datasetDoi: null }));
    expect(out).not.toContain('doi:');
  });
});
