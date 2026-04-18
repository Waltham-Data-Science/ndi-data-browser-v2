/**
 * Citation format generators — client-side, pure functions.
 *
 * Produces BibTeX, RIS, and a plain-text citation string from a
 * :class:`DatasetSummaryCitation`. Established conventions, not
 * invented:
 *   - **BibTeX** uses the ``@dataset`` entry type (DataCite/biblatex
 *     canonical for cite-a-dataset). Fields follow the biblatex-style
 *     ``title``, ``author``, ``year``, ``doi``, ``url``, ``note``.
 *   - **RIS** uses ``TY  - DATA`` (dataset) per the RIS 1.0 spec. Tags
 *     are two-letter, followed by two spaces, a hyphen, and a single
 *     space — exactly `<TAG>  - <value>`. Terminator: ``ER  -``.
 *   - **Plain-text** follows the NDI Cloud convention flagged in the
 *     amendment: surname + initial + title + NDI Cloud + doi + (upload
 *     year).
 *
 * Both DOIs are exposed as independently generated strings so the
 * caller can render the dataset DOI as the preferred primary and the
 * paper DOIs as the secondary alternative. No automatic "prefer dataset
 * over paper" logic happens in the generator — that's a UI concern.
 */
import type { DatasetSummaryCitation, DatasetSummaryContributor } from '@/types/dataset-summary';

/** Canonical BibTeX entry type for datasets (biblatex / DataCite). */
const BIBTEX_ENTRY_TYPE = 'dataset';

/** Fallback publisher string when the citation shape does not carry one. */
const PUBLISHER = 'NDI Cloud';

/** Strip a DOI URL prefix to bare `10.xxxx/yyy` form when present. */
export function stripDoiPrefix(doi: string): string {
  if (!doi) return doi;
  return doi.replace(/^https?:\/\/(?:dx\.)?doi\.org\//i, '');
}

/** Best-effort BibTeX citation key. Rule: lastname of first contributor,
 *  lowercased + ASCII-only, + year + first stem-word of title. Falls
 *  back to `ndidataset<year>` when no contributor is available. */
export function bibtexCiteKey(citation: DatasetSummaryCitation): string {
  const last = citation.contributors[0]?.lastName ?? '';
  const slug = last
    .normalize('NFD')
    .replace(/[\u0300-\u036f]/g, '')
    .replace(/[^A-Za-z0-9]/g, '')
    .toLowerCase();
  const year = citation.year ?? 'nd';
  const word = (citation.title.match(/[A-Za-z]{3,}/) ?? [''])[0]
    .toLowerCase()
    .replace(/[^a-z0-9]/g, '');
  if (!slug) return `ndidataset${year}${word ? `_${word}` : ''}`;
  return `${slug}${year}${word ? `_${word}` : ''}`;
}

/** Escape a BibTeX string value: brace-balance-safe `{…}` wrap and
 *  backslash-escape the small set of characters biblatex treats as
 *  special. We intentionally DO NOT escape into LaTeX-accented-letter
 *  form; biblatex ingests UTF-8 directly now. */
export function bibtexEscape(value: string): string {
  return value
    .replace(/\\/g, '\\textbackslash{}')
    .replace(/([{}])/g, '\\$1')
    .replace(/&/g, '\\&')
    .replace(/%/g, '\\%')
    .replace(/\$/g, '\\$')
    .replace(/#/g, '\\#')
    .replace(/\^/g, '\\^{}')
    .replace(/_/g, '\\_')
    .replace(/~/g, '\\~{}');
}

/** Format a list of contributors as a BibTeX `author` field value
 *  (`Last, First and Last, First`). Lossless — drops nothing. */
export function bibtexAuthors(contributors: DatasetSummaryContributor[]): string {
  if (contributors.length === 0) return '';
  return contributors
    .map((c) => {
      const last = c.lastName.trim();
      const first = c.firstName.trim();
      if (last && first) return `${last}, ${first}`;
      if (last) return last;
      if (first) return first;
      return '';
    })
    .filter(Boolean)
    .join(' and ');
}

export interface BibTexOptions {
  /** Which DOI to embed as the primary. Defaults to the dataset DOI
   *  when present, per amendment §4.B4. */
  doi?: 'dataset' | 'paper' | 'none';
}

/** Generate a BibTeX entry. Dataset DOI is preferred by default (the
 *  amendment calls it out explicitly). Paper DOIs are emitted as a
 *  ``note`` field so the record is lossless even when the dataset DOI
 *  is the active cite target. */
export function toBibtex(
  citation: DatasetSummaryCitation,
  options: BibTexOptions = {},
): string {
  const key = bibtexCiteKey(citation);
  const doiMode = options.doi ?? 'dataset';

  const fields: Array<[string, string]> = [];
  fields.push(['title', bibtexEscape(citation.title)]);
  const authors = bibtexAuthors(citation.contributors);
  if (authors) fields.push(['author', bibtexEscape(authors)]);
  if (citation.year != null) fields.push(['year', String(citation.year)]);
  fields.push(['publisher', bibtexEscape(PUBLISHER)]);

  const primaryDoi =
    doiMode === 'dataset' && citation.datasetDoi
      ? stripDoiPrefix(citation.datasetDoi)
      : doiMode === 'paper' && citation.paperDois[0]
        ? stripDoiPrefix(citation.paperDois[0])
        : null;
  if (primaryDoi) fields.push(['doi', bibtexEscape(primaryDoi)]);

  if (citation.datasetDoi) {
    fields.push(['url', citation.datasetDoi]);
  }

  if (citation.license) {
    fields.push(['note', bibtexEscape(`License: ${citation.license}`)]);
  }

  // Preserve paper DOIs when we emitted the dataset DOI as the primary.
  if (doiMode === 'dataset' && citation.paperDois.length > 0) {
    const relatedDois = citation.paperDois.map(stripDoiPrefix).join('; ');
    fields.push([
      'addendum',
      bibtexEscape(`Associated paper DOI: ${relatedDois}`),
    ]);
  }

  const body = fields.map(([k, v]) => `  ${k} = {${v}},`).join('\n');
  // Trim the trailing comma on the final field for tidy output.
  const trimmed = body.replace(/,\n?$/, '\n');
  return `@${BIBTEX_ENTRY_TYPE}{${key},\n${trimmed}}\n`;
}

/** Generate an RIS record. Uses `TY  - DATA` per RIS 1.0 for datasets.
 *  Dataset DOI is the primary `DO` line; paper DOIs emit as extra `DO`
 *  lines so downstream reference managers preserve them. */
export function toRis(citation: DatasetSummaryCitation): string {
  const lines: string[] = [];
  const push = (tag: string, value: string | number | null | undefined) => {
    if (value == null || value === '') return;
    lines.push(`${tag.padEnd(2, ' ')}  - ${value}`);
  };

  push('TY', 'DATA');
  push('T1', citation.title);
  for (const c of citation.contributors) {
    const last = c.lastName.trim();
    const first = c.firstName.trim();
    const formatted = [last, first].filter(Boolean).join(', ');
    if (formatted) push('AU', formatted);
  }
  if (citation.year != null) push('PY', citation.year);
  push('PB', PUBLISHER);
  if (citation.datasetDoi) push('DO', stripDoiPrefix(citation.datasetDoi));
  for (const d of citation.paperDois) {
    push('DO', stripDoiPrefix(d));
  }
  if (citation.datasetDoi) push('UR', citation.datasetDoi);
  if (citation.license) push('C1', `License: ${citation.license}`);
  lines.push('ER  - ');
  return `${lines.join('\n')}\n`;
}

/** Human-readable plain-text citation. Follows the pattern in the
 *  amendment:
 *
 *  "LastName F, LastName F. *Title*. NDI Cloud. doi:DOI. (upload year)."
 *
 *  A single contributor renders as "Last F." with an Oxford-comma-free
 *  "and" between the last two when there are multiple. Upload year is
 *  labelled deliberately — the year field is record-creation year, not
 *  publication year (FROZEN citation shape docstring). */
export function toPlainText(citation: DatasetSummaryCitation): string {
  const parts: string[] = [];

  const names = citation.contributors.map(formatAuthorShort).filter(Boolean);
  if (names.length > 0) {
    parts.push(joinWithAnd(names) + '.');
  }
  parts.push(citation.title + '.');
  parts.push(`${PUBLISHER}.`);
  if (citation.datasetDoi) {
    parts.push(`doi:${stripDoiPrefix(citation.datasetDoi)}.`);
  }
  if (citation.year != null) {
    parts.push(`(Upload year: ${citation.year}).`);
  }
  if (citation.license) {
    parts.push(`License: ${citation.license}.`);
  }
  return parts.join(' ');
}

function formatAuthorShort(c: DatasetSummaryContributor): string {
  const last = c.lastName.trim();
  const first = c.firstName.trim();
  if (!last && !first) return '';
  const initial = first ? ` ${first[0]!.toUpperCase()}` : '';
  return `${last}${initial}`;
}

function joinWithAnd(items: string[]): string {
  if (items.length === 0) return '';
  if (items.length === 1) return items[0]!;
  if (items.length === 2) return `${items[0]} and ${items[1]}`;
  return `${items.slice(0, -1).join(', ')}, and ${items[items.length - 1]}`;
}
