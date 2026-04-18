/**
 * DatasetSummaryCard — Plan B B1 landing card.
 *
 * Labeled-facts block (no prose sentence) rendering the synthesized
 * :class:`DatasetSummary`. Section layout:
 *   - Counts (6 count chips)
 *   - Biology (species / strains / sexes as ontology pills)
 *   - Anatomy (brainRegions pills)
 *   - Scale (date range + total size)
 *   - Citation (title / license / DOIs / contributors / year)
 *
 * ``[]`` renders an em-dash ("—"). ``null`` renders "Not applicable" —
 * these signals carry different meaning (amendment §3) and the UI must
 * preserve the distinction.
 *
 * Full strings are preserved, never truncated (amendment §4.B1).
 */
import { Info } from 'lucide-react';
import { useMemo, useState } from 'react';

import { ExternalAnchor } from '@/components/ExternalAnchor';
import { Badge } from '@/components/ui/Badge';
import {
  Card,
  CardBody,
  CardDescription,
  CardHeader,
  CardTitle,
} from '@/components/ui/Card';
import { cn } from '@/lib/cn';
import { formatBytes } from '@/lib/format';
import type {
  DatasetSummary,
  DatasetSummaryContributor,
  OntologyTerm,
} from '@/types/dataset-summary';

export interface DatasetSummaryCardProps {
  summary: DatasetSummary;
  className?: string;
}

export function DatasetSummaryCard({
  summary,
  className,
}: DatasetSummaryCardProps) {
  return (
    <Card className={className}>
      <CardHeader>
        <CardTitle className="text-base">Dataset summary</CardTitle>
        <CardDescription className="text-xs">
          Synthesized from the cloud-indexed classes for dataset{' '}
          <span className="font-mono">{summary.datasetId}</span>.
        </CardDescription>
      </CardHeader>
      <CardBody className="space-y-6 text-sm">
        <CountsSection counts={summary.counts} />
        <BiologySection
          species={summary.species}
          strains={summary.strains}
          sexes={summary.sexes}
        />
        <AnatomySection brainRegions={summary.brainRegions} />
        <ProbeTypesSection probeTypes={summary.probeTypes} />
        <ScaleSection
          dateRange={summary.dateRange}
          totalSizeBytes={summary.totalSizeBytes}
        />
        <CitationSection citation={summary.citation} />
        <SummaryFooter
          computedAt={summary.computedAt}
          extractionWarnings={summary.extractionWarnings}
        />
      </CardBody>
    </Card>
  );
}

// ---------------------------------------------------------------------------
// Section primitives
// ---------------------------------------------------------------------------

function SectionHeading({ children }: { children: React.ReactNode }) {
  return (
    <h2 className="text-xs font-semibold uppercase tracking-wide text-slate-500 dark:text-slate-400">
      {children}
    </h2>
  );
}

function CountsSection({
  counts,
}: {
  counts: DatasetSummary['counts'];
}) {
  const items: Array<{ label: string; value: number; testId: string }> = [
    { label: 'Sessions', value: counts.sessions, testId: 'counts-sessions' },
    { label: 'Subjects', value: counts.subjects, testId: 'counts-subjects' },
    { label: 'Probes', value: counts.probes, testId: 'counts-probes' },
    { label: 'Elements', value: counts.elements, testId: 'counts-elements' },
    { label: 'Epochs', value: counts.epochs, testId: 'counts-epochs' },
    {
      label: 'Documents',
      value: counts.totalDocuments,
      testId: 'counts-total-documents',
    },
  ];
  return (
    <section aria-label="Counts" className="space-y-2">
      <SectionHeading>Counts</SectionHeading>
      <dl
        className="grid grid-cols-2 gap-2 sm:grid-cols-3"
        data-testid="dataset-summary-counts"
      >
        {items.map((i) => (
          <div
            key={i.label}
            className="rounded-md border border-slate-200 bg-slate-50 px-2 py-1.5 dark:border-slate-700 dark:bg-slate-900"
            data-testid={i.testId}
          >
            <dt className="text-[10px] uppercase tracking-wide text-slate-500 dark:text-slate-400">
              {i.label}
            </dt>
            <dd className="font-mono text-sm text-slate-800 dark:text-slate-100">
              {new Intl.NumberFormat().format(i.value)}
            </dd>
          </div>
        ))}
      </dl>
    </section>
  );
}

function BiologySection({
  species,
  strains,
  sexes,
}: {
  species: OntologyTerm[] | null;
  strains: OntologyTerm[] | null;
  sexes: OntologyTerm[] | null;
}) {
  return (
    <section aria-label="Biology" className="space-y-2" data-testid="biology">
      <SectionHeading>Biology</SectionHeading>
      <dl className="space-y-1.5">
        <LabeledOntologyList label="Species" terms={species} />
        <LabeledOntologyList label="Strains" terms={strains} />
        <LabeledOntologyList label="Sex" terms={sexes} />
      </dl>
    </section>
  );
}

function AnatomySection({
  brainRegions,
}: {
  brainRegions: OntologyTerm[] | null;
}) {
  return (
    <section aria-label="Anatomy" className="space-y-2" data-testid="anatomy">
      <SectionHeading>Anatomy</SectionHeading>
      <dl className="space-y-1.5">
        <LabeledOntologyList label="Brain regions" terms={brainRegions} />
      </dl>
    </section>
  );
}

function ProbeTypesSection({
  probeTypes,
}: {
  probeTypes: string[] | null;
}) {
  return (
    <section
      aria-label="Probe types"
      className="space-y-2"
      data-testid="probe-types"
    >
      <SectionHeading>Probe types</SectionHeading>
      <dl className="space-y-1.5">
        <div className="grid grid-cols-[max-content_1fr] items-start gap-x-3 gap-y-1">
          <dt className="pt-0.5 text-xs font-medium text-slate-600 dark:text-slate-300">
            Types
          </dt>
          <dd className="flex flex-wrap gap-1.5">
            {renderStringListOrStatus(probeTypes)}
          </dd>
        </div>
      </dl>
    </section>
  );
}

function ScaleSection({
  dateRange,
  totalSizeBytes,
}: {
  dateRange: DatasetSummary['dateRange'];
  totalSizeBytes: number | null;
}) {
  const range = useMemo(() => {
    const { earliest, latest } = dateRange;
    if (!earliest && !latest) return '—';
    if (earliest && latest && earliest === latest) return earliest;
    const parts = [earliest ?? '?', latest ?? '?'];
    return parts.join(' → ');
  }, [dateRange]);
  return (
    <section aria-label="Scale" className="space-y-2" data-testid="scale">
      <SectionHeading>Scale</SectionHeading>
      <dl className="grid grid-cols-[max-content_1fr] items-start gap-x-3 gap-y-1 text-xs">
        <dt className="font-medium text-slate-600 dark:text-slate-300">
          Date range
        </dt>
        <dd className="font-mono text-slate-800 dark:text-slate-100">
          {range}
        </dd>
        <dt className="font-medium text-slate-600 dark:text-slate-300">
          Total size
        </dt>
        <dd className="font-mono text-slate-800 dark:text-slate-100">
          {totalSizeBytes == null ? '—' : formatBytes(totalSizeBytes)}
        </dd>
      </dl>
    </section>
  );
}

function CitationSection({
  citation,
}: {
  citation: DatasetSummary['citation'];
}) {
  return (
    <section
      aria-label="Citation"
      className="space-y-2"
      data-testid="citation"
    >
      <SectionHeading>Citation</SectionHeading>
      <div className="space-y-2 text-xs">
        <p
          className="text-sm font-medium text-slate-800 dark:text-slate-100"
          data-testid="citation-title"
        >
          {citation.title}
        </p>
        <div className="flex flex-wrap items-center gap-2">
          {citation.license && (
            <Badge variant="outline" data-testid="citation-license">
              {citation.license}
            </Badge>
          )}
          {citation.year != null && (
            <span
              className="text-[11px] text-slate-500 dark:text-slate-400"
              data-testid="citation-year"
            >
              {citation.year}
            </span>
          )}
        </div>
        {citation.datasetDoi && (
          <div
            className="flex items-center gap-1.5"
            data-testid="citation-dataset-doi"
          >
            <span className="text-[10px] uppercase tracking-wide text-slate-500 dark:text-slate-400">
              Dataset DOI
            </span>
            <ExternalAnchor
              href={citation.datasetDoi}
              label={citation.datasetDoi}
              className="text-[11px] font-mono"
            />
          </div>
        )}
        {citation.paperDois.length > 0 && (
          <div className="space-y-1" data-testid="citation-paper-dois">
            <span className="text-[10px] uppercase tracking-wide text-slate-500 dark:text-slate-400">
              Paper DOIs
            </span>
            <ul className="space-y-0.5">
              {citation.paperDois.map((doi) => (
                <li key={doi}>
                  <ExternalAnchor
                    href={doi}
                    label={doi}
                    className="text-[11px] font-mono"
                  />
                </li>
              ))}
            </ul>
          </div>
        )}
        {citation.contributors.length > 0 && (
          <div className="space-y-1" data-testid="citation-contributors">
            <span className="text-[10px] uppercase tracking-wide text-slate-500 dark:text-slate-400">
              Contributors
            </span>
            <ul className="space-y-0.5">
              {citation.contributors.map((c, i) => (
                <li
                  key={`${c.firstName}-${c.lastName}-${i}`}
                  className="flex items-center gap-1.5"
                >
                  <ContributorRow contributor={c} />
                </li>
              ))}
            </ul>
          </div>
        )}
      </div>
    </section>
  );
}

function ContributorRow({
  contributor,
}: {
  contributor: DatasetSummaryContributor;
}) {
  const name = [contributor.firstName, contributor.lastName]
    .filter(Boolean)
    .join(' ')
    .trim();
  return (
    <>
      <span className="text-[11px] text-slate-700 dark:text-slate-300">
        {name || '—'}
      </span>
      {contributor.orcid && (
        <ExternalAnchor
          href={contributor.orcid}
          label="ORCID"
          iconSize={10}
          className="text-[10px]"
        />
      )}
    </>
  );
}

// ---------------------------------------------------------------------------
// Labeled ontology list + "Not applicable" / "—" states
// ---------------------------------------------------------------------------

function LabeledOntologyList({
  label,
  terms,
}: {
  label: string;
  terms: OntologyTerm[] | null;
}) {
  return (
    <div
      className="grid grid-cols-[max-content_1fr] items-start gap-x-3 gap-y-1"
      data-testid={`biology-${slugify(label)}`}
    >
      <dt className="pt-0.5 text-xs font-medium text-slate-600 dark:text-slate-300">
        {label}
      </dt>
      <dd className="flex flex-wrap gap-1.5">
        {renderOntologyList(terms)}
      </dd>
    </div>
  );
}

function renderOntologyList(terms: OntologyTerm[] | null): React.ReactNode {
  if (terms === null) {
    return (
      <span
        className="text-[11px] italic text-slate-500 dark:text-slate-400"
        data-testid="value-not-applicable"
      >
        Not applicable
      </span>
    );
  }
  if (terms.length === 0) {
    return (
      <span
        className="text-[11px] text-slate-500 dark:text-slate-400"
        data-testid="value-empty"
      >
        —
      </span>
    );
  }
  return terms.map((t) => (
    <OntologyTermPill key={`${t.label}-${t.ontologyId ?? ''}`} term={t} />
  ));
}

function renderStringListOrStatus(values: string[] | null): React.ReactNode {
  if (values === null) {
    return (
      <span
        className="text-[11px] italic text-slate-500 dark:text-slate-400"
        data-testid="value-not-applicable"
      >
        Not applicable
      </span>
    );
  }
  if (values.length === 0) {
    return (
      <span
        className="text-[11px] text-slate-500 dark:text-slate-400"
        data-testid="value-empty"
      >
        —
      </span>
    );
  }
  return values.map((v) => (
    <Badge key={v} variant="secondary" className="font-mono">
      {v}
    </Badge>
  ));
}

// ---------------------------------------------------------------------------
// OntologyTermPill — hover >=600ms reveals ontologyId tooltip; click opens
// the resolver.
// ---------------------------------------------------------------------------

const HOVER_TOOLTIP_DELAY_MS = 600;

export function OntologyTermPill({ term }: { term: OntologyTerm }) {
  const [hovering, setHovering] = useState(false);
  const [tooltipVisible, setTooltipVisible] = useState(false);

  const resolverHref = term.ontologyId ? resolverUrl(term.ontologyId) : null;

  function onEnter() {
    setHovering(true);
    window.setTimeout(() => {
      setTooltipVisible((prev) => (hovering ? true : prev));
    }, HOVER_TOOLTIP_DELAY_MS);
  }
  function onLeave() {
    setHovering(false);
    setTooltipVisible(false);
  }

  const content = (
    <span
      className={cn(
        'inline-flex items-center rounded-full px-2 py-0.5 text-[11px] ring-1 ring-inset',
        'bg-brand-50 text-brand-800 ring-brand-200',
        'dark:bg-brand-900/40 dark:text-brand-200 dark:ring-brand-800',
      )}
      data-testid="ontology-term-pill"
    >
      {term.label}
    </span>
  );

  return (
    <span
      className="relative inline-flex"
      onMouseEnter={onEnter}
      onMouseLeave={onLeave}
      onFocus={onEnter}
      onBlur={onLeave}
      data-ontology-id={term.ontologyId ?? ''}
    >
      {resolverHref ? (
        <a
          href={resolverHref}
          target="_blank"
          rel="noopener noreferrer"
          className="focus:outline-none focus-visible:ring-2 focus-visible:ring-brand-500 rounded-full"
          data-testid="ontology-term-link"
        >
          {content}
        </a>
      ) : (
        content
      )}
      {tooltipVisible && term.ontologyId && (
        <span
          role="tooltip"
          data-testid="ontology-term-tooltip"
          className="absolute left-0 top-full z-10 mt-1 whitespace-nowrap rounded-md border border-slate-200 bg-white px-2 py-1 text-[10px] font-mono shadow-md dark:border-slate-700 dark:bg-slate-900"
        >
          {term.ontologyId}
        </span>
      )}
    </span>
  );
}

/**
 * Resolver URL for PROVIDER:ID style ontology references. Falls back to the
 * raw ID when we don't know a canonical resolver.
 */
export function resolverUrl(ontologyId: string): string | null {
  if (!ontologyId.includes(':')) return null;
  const [provider, rest] = ontologyId.split(':', 2) as [string, string];
  const id = rest.trim();
  if (!provider || !id) return null;
  const upper = provider.toUpperCase();
  switch (upper) {
    case 'NCBITAXON':
    case 'UBERON':
    case 'CL':
    case 'CHEBI':
    case 'PATO':
    case 'EFO':
      return `http://purl.obolibrary.org/obo/${upper}_${id}`;
    case 'RRID':
      return `https://scicrunch.org/resolver/RRID:${id}`;
    case 'WBSTRAIN':
      return `https://wormbase.org/species/c_elegans/strain/${id}`;
    case 'PUBCHEM':
      return `https://pubchem.ncbi.nlm.nih.gov/compound/${id}`;
    default:
      return null;
  }
}

// ---------------------------------------------------------------------------
// Footer — computedAt + extractionWarnings debug tooltip
// ---------------------------------------------------------------------------

function SummaryFooter({
  computedAt,
  extractionWarnings,
}: {
  computedAt: string;
  extractionWarnings: string[];
}) {
  const age = useMemo(() => humanizeAge(computedAt), [computedAt]);
  const [warningsOpen, setWarningsOpen] = useState(false);
  return (
    <footer
      className="flex items-center justify-between border-t border-slate-200 pt-2 text-[10px] text-slate-500 dark:border-slate-700 dark:text-slate-400"
      data-testid="summary-footer"
    >
      <span data-testid="summary-computed-at">Last computed {age}</span>
      {extractionWarnings.length > 0 && (
        <button
          type="button"
          className="inline-flex items-center gap-1 rounded hover:text-slate-700 dark:hover:text-slate-200 focus:outline-none focus-visible:ring-2 focus-visible:ring-brand-500"
          onClick={() => setWarningsOpen((v) => !v)}
          aria-expanded={warningsOpen}
          data-testid="summary-warnings-toggle"
        >
          <Info className="h-3 w-3" aria-hidden />
          {extractionWarnings.length} warning
          {extractionWarnings.length === 1 ? '' : 's'}
        </button>
      )}
      {warningsOpen && extractionWarnings.length > 0 && (
        <ul
          className="absolute right-6 z-10 mt-1 w-80 max-w-[90vw] space-y-1 rounded-md border border-slate-200 bg-white p-2 text-[10px] shadow-md dark:border-slate-700 dark:bg-slate-900"
          data-testid="summary-warnings-tooltip"
        >
          {extractionWarnings.map((w, i) => (
            <li
              key={i}
              className="text-slate-700 dark:text-slate-200"
              data-testid="summary-warning"
            >
              {w}
            </li>
          ))}
        </ul>
      )}
    </footer>
  );
}

function humanizeAge(iso: string): string {
  const then = Date.parse(iso);
  if (Number.isNaN(then)) return `at ${iso}`;
  const ms = Date.now() - then;
  if (ms < 0) return `just now`;
  const sec = Math.round(ms / 1000);
  if (sec < 60) return `${sec}s ago`;
  const min = Math.round(sec / 60);
  if (min < 60) return `${min}m ago`;
  const hr = Math.round(min / 60);
  if (hr < 24) return `${hr}h ago`;
  const d = Math.round(hr / 24);
  return `${d}d ago`;
}

function slugify(s: string): string {
  return s.toLowerCase().replace(/[^a-z0-9]+/g, '-');
}
