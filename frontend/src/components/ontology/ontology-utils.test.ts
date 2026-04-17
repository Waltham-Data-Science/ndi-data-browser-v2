import { describe, expect, it } from 'vitest';
import {
  isOntologyTerm,
  normalizeOntologyTerm,
  providerFromTerm,
} from './ontology-utils';

describe('isOntologyTerm', () => {
  it('matches prefixed ontology IDs', () => {
    expect(isOntologyTerm('NCBITaxon:6239')).toBe(true);
    expect(isOntologyTerm('PATO:0001340')).toBe(true);
    expect(isOntologyTerm('WBStrain:00000001')).toBe(true);
    expect(isOntologyTerm('UBERON:0002436')).toBe(true);
    expect(isOntologyTerm('CL:0000540')).toBe(true);
    expect(isOntologyTerm('CHEBI:73328')).toBe(true);
    expect(isOntologyTerm('EMPTY:0000198')).toBe(true);
    expect(isOntologyTerm('RRID:RGD_70508')).toBe(true);
  });

  it('matches bare numeric IDs (Van Hooser species)', () => {
    expect(isOntologyTerm('9669')).toBe(true);
  });

  it('rejects non-ontology strings', () => {
    expect(isOntologyTerm('')).toBe(false);
    expect(isOntologyTerm('hello world')).toBe(false);
    expect(isOntologyTerm('  ')).toBe(false);
    // Lab-prefixed subject identifiers must NOT match — otherwise the
    // table renders a popover chip for a guaranteed-404 lookup.
    expect(isOntologyTerm('PR811_4144@chalasani-lab.salk.edu')).toBe(false);
    expect(isOntologyTerm('ferret_395.1664@vhlab.org')).toBe(false);
    // NDI ndiIds look like `41269430c7aece37_40a0c675929cea61` — also not
    // ontology terms.
    expect(isOntologyTerm('41269430c7aece37_40a0c675929cea61')).toBe(false);
  });

  it('handles non-string values', () => {
    expect(isOntologyTerm(null)).toBe(false);
    expect(isOntologyTerm(undefined)).toBe(false);
    expect(isOntologyTerm(42)).toBe(false);
    expect(isOntologyTerm({})).toBe(false);
  });
});

describe('normalizeOntologyTerm', () => {
  it('passes prefixed terms through unchanged', () => {
    expect(normalizeOntologyTerm('NCBITaxon:6239')).toBe('NCBITaxon:6239');
    expect(normalizeOntologyTerm('WBStrain:00000001')).toBe('WBStrain:00000001');
  });

  it('adds NCBITaxon prefix to bare numeric IDs', () => {
    expect(normalizeOntologyTerm('9669')).toBe('NCBITaxon:9669');
    expect(normalizeOntologyTerm('10090')).toBe('NCBITaxon:10090');
  });

  it('trims whitespace', () => {
    expect(normalizeOntologyTerm('  NCBITaxon:6239  ')).toBe('NCBITaxon:6239');
  });

  it('returns null for unrecognizable shapes', () => {
    expect(normalizeOntologyTerm('')).toBeNull();
    expect(normalizeOntologyTerm('   ')).toBeNull();
    expect(normalizeOntologyTerm('hello')).toBeNull();
    expect(normalizeOntologyTerm('ferret-A1')).toBeNull();
  });
});

describe('providerFromTerm', () => {
  it('returns the prefix for normalized terms', () => {
    expect(providerFromTerm('NCBITaxon:6239')).toBe('NCBITaxon');
    expect(providerFromTerm('WBStrain:00000001')).toBe('WBStrain');
    expect(providerFromTerm('9669')).toBe('NCBITaxon'); // via normalization
  });

  it('returns null for unrecognizable terms', () => {
    expect(providerFromTerm('hello')).toBeNull();
    expect(providerFromTerm('')).toBeNull();
  });
});
