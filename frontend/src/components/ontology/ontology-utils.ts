/**
 * Ontology term utilities.
 *
 * Ported verbatim from v1 (frontend/src/components/ontology/ontology-utils.ts)
 * with a small extension to normalize bare unprefixed NCBI taxon IDs: Van
 * Hooser emits `openminds.fields.preferredOntologyIdentifier = "9669"`
 * where Haley emits the prefixed `"NCBITaxon:6239"`. The projection layer
 * passes both through unchanged; normalization happens here so the popover
 * lookup always gets a `PROVIDER:ID`-shaped string.
 *
 * This file is the single source of truth for "is this a clickable ontology
 * term" — referenced by both SummaryTableView (batch prefetch) and
 * OntologyPopover (single-term lookup).
 */

const ONTOLOGY_PATTERN = /^[A-Z]+[a-z]*:?\d{4,}$/;
const BARE_NUMERIC_ID = /^\d{3,}$/;
const PREFIXED_TERM = /^[A-Za-z][A-Za-z_]*:[A-Za-z0-9_.:-]+$/;

/** Returns true if `value` looks like an ontology term ID.
 *
 * Accepts three shapes:
 * - Prefixed: `NCBITaxon:6239`, `PATO:0001340`, `RRID:RGD_70508`, etc.
 * - Concat (no colon): `NCBITaxon6239` — legacy v1 shape, tolerated.
 * - Bare numeric (3+ digits): `9669` — Van Hooser species encoding;
 *   normalizeOntologyTerm() maps this to `NCBITaxon:9669`.
 *
 * Deliberately strict: subject local identifiers like
 * `PR811_4144@chalasani-lab.salk.edu` must NOT match, or the table
 * column would render as an ontology chip with a guaranteed-404 lookup.
 */
export function isOntologyTerm(value: unknown): value is string {
  if (!value || typeof value !== 'string') return false;
  const trimmed = value.trim();
  if (!trimmed) return false;
  if (BARE_NUMERIC_ID.test(trimmed)) return true;
  if (PREFIXED_TERM.test(trimmed)) return true;
  return ONTOLOGY_PATTERN.test(trimmed);
}

/** Normalize a term ID to `PROVIDER:ID` form. Returns null if the value
 * doesn't look like any known shape. */
export function normalizeOntologyTerm(value: string): string | null {
  if (!value) return null;
  const trimmed = value.trim();
  if (!trimmed) return null;
  if (trimmed.includes(':')) return trimmed;
  // Bare numeric ID — default to NCBITaxon. This matches Van Hooser's
  // openminds_subject Species docs which emit `"9669"` without the
  // `NCBITaxon:` prefix.
  if (/^\d+$/.test(trimmed)) return `NCBITaxon:${trimmed}`;
  return null;
}

/** Extract the provider prefix from a term, or null. */
export function providerFromTerm(value: string): string | null {
  const normalized = normalizeOntologyTerm(value);
  if (!normalized) return null;
  const idx = normalized.indexOf(':');
  return idx > 0 ? normalized.slice(0, idx) : null;
}
