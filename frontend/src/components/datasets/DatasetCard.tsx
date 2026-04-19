import { BookOpen, FileText, Users } from 'lucide-react';
import { Link } from 'react-router-dom';

import type { DatasetRecord } from '@/api/datasets';
import { Badge } from '@/components/ui/Badge';
import {
  Card,
  CardBody,
  CardDescription,
  CardHeader,
  CardTitle,
} from '@/components/ui/Card';
import { formatBytes, formatDate, truncate } from '@/lib/format';
import type {
  CompactDatasetSummary,
  OntologyTerm,
} from '@/types/dataset-summary';

import { OntologyTermPill } from './DatasetSummaryCard';

interface DatasetCardProps {
  dataset: DatasetRecord;
}

/** Rich catalog card.
 *
 * When the backend embeds a compact :interface:`CompactDatasetSummary`
 * (Plan B B2), render it as species / brain-region pills and a subject
 * count above the existing raw-record metadata. When it's ``null`` or
 * ``undefined`` (backend synth failed or pre-B2 deploy), fall back to the
 * original v1-style card using `DatasetRecord` fields only — no layout
 * shift, no missing content.
 *
 * Pills reuse `OntologyTermPill` from `DatasetSummaryCard.tsx` — no
 * duplicated pill component, no drift between detail view and catalog.
 */
export function DatasetCard({ dataset }: DatasetCardProps) {
  const abstract = dataset.abstract ?? dataset.description;
  const contributors = (dataset.contributors ?? [])
    .map((c) => [c.firstName, c.lastName].filter(Boolean).join(' '))
    .filter(Boolean);

  const summary = dataset.summary ?? null;

  return (
    <Link
      to={`/datasets/${dataset.id}`}
      className="block group focus:outline-none focus-visible:ring-2 focus-visible:ring-brand-500 rounded-lg"
      aria-label={`Open dataset ${dataset.name}`}
    >
      <Card className="h-full transition-shadow group-hover:shadow-md group-hover:ring-brand-400">
        <CardHeader className="pb-2">
          {/* line-clamp-4 not -2 — scientific dataset titles are long
              (post-Steve feedback 2026-04-18, "We scientists tend to use
              long titles"). Grid uses h-full so cards in a row align
              to the tallest; four lines leaves room for ~150 chars
              before any clamp kicks in. */}
          <CardTitle className="line-clamp-4">{dataset.name}</CardTitle>
          {abstract && (
            <CardDescription className="line-clamp-2 text-xs">
              {truncate(abstract, 220)}
            </CardDescription>
          )}
        </CardHeader>
        <CardBody className="pt-0 space-y-2">
          {summary && <CompactSummarySection summary={summary} />}

          <div className="flex flex-wrap gap-1.5">
            {dataset.license && (
              <Badge variant="outline">{dataset.license}</Badge>
            )}
            {dataset.organizationId && (
              <Badge variant="secondary" className="font-mono normal-case">
                {dataset.organizationId.length > 14
                  ? `${dataset.organizationId.slice(0, 14)}…`
                  : dataset.organizationId}
              </Badge>
            )}
            {dataset.publishStatus && dataset.publishStatus !== 'published' && (
              <Badge variant="secondary">{dataset.publishStatus}</Badge>
            )}
          </div>

          {contributors.length > 0 && (
            <p className="text-xs text-slate-500 dark:text-slate-400 line-clamp-1">
              {contributors.slice(0, 3).join(', ')}
              {contributors.length > 3 && ` +${contributors.length - 3}`}
            </p>
          )}

          <div className="flex flex-wrap items-center gap-3 text-[11px] text-slate-500 dark:text-slate-400 font-mono">
            {/* Doc count: prefer the synthesizer's count (always authoritative,
                comes from indexed class-counts) over the cloud's documentCount
                (can drift per the IDataset schema). Fall back to raw record. */}
            {summary?.counts.totalDocuments != null ? (
              <span className="inline-flex items-center gap-1">
                <FileText className="h-3 w-3" />
                {summary.counts.totalDocuments.toLocaleString()} docs
              </span>
            ) : dataset.documentCount != null ? (
              <span className="inline-flex items-center gap-1">
                <FileText className="h-3 w-3" />
                {dataset.documentCount.toLocaleString()} docs
              </span>
            ) : null}
            {dataset.contributors && dataset.contributors.length > 0 && (
              <span className="inline-flex items-center gap-1">
                <Users className="h-3 w-3" />
                {dataset.contributors.length} contributors
              </span>
            )}
            {dataset.totalSize != null && dataset.totalSize > 0 && (
              <span>{formatBytes(dataset.totalSize)}</span>
            )}
            {dataset.doi && (
              <span className="inline-flex items-center gap-1">
                <BookOpen className="h-3 w-3" />
                <span className="truncate max-w-[160px] md:max-w-[240px] lg:max-w-[360px]">{dataset.doi}</span>
              </span>
            )}
          </div>

          {/* Date metadata uses slate-500 on white (ratio 4.78:1) rather
              than slate-400 (2.63:1) to satisfy WCAG AA; darker dark-mode
              pairing kept as slate-400 since the contrast ratio against
              slate-900 is already fine. */}
          <div className="flex items-center gap-2 text-[10px] text-slate-500 dark:text-slate-400">
            {dataset.createdAt && <span>Created {formatDate(dataset.createdAt)}</span>}
            {dataset.updatedAt && dataset.updatedAt !== dataset.createdAt && (
              <span>Updated {formatDate(dataset.updatedAt)}</span>
            )}
          </div>
        </CardBody>
      </Card>
    </Link>
  );
}

/** The synthesizer-driven strip: species + brain-region chips + subject
 *  count. Renders nothing extra when every fact is null/empty so a card
 *  without any synthesized content stays visually lean. */
function CompactSummarySection({
  summary,
}: {
  summary: CompactDatasetSummary;
}) {
  const species = summary.species ?? null;
  const regions = summary.brainRegions ?? null;
  const hasSpecies = species != null && species.length > 0;
  const hasRegions = regions != null && regions.length > 0;
  const hasSubjectCount = summary.counts.subjects > 0;

  if (!hasSpecies && !hasRegions && !hasSubjectCount) {
    // Synthesizer ran but every fact was null/zero — nothing to surface.
    return null;
  }

  return (
    <div
      className="flex flex-wrap items-center gap-x-2 gap-y-1.5 pb-1"
      data-testid="dataset-card-summary"
    >
      {hasSpecies && (
        <PillRow
          testId="dataset-card-summary-species"
          terms={species as OntologyTerm[]}
          limit={3}
        />
      )}
      {hasRegions && (
        <PillRow
          testId="dataset-card-summary-brain-regions"
          terms={regions as OntologyTerm[]}
          limit={3}
        />
      )}
      {hasSubjectCount && (
        <span
          className="inline-flex items-center gap-1 text-[11px] font-mono text-slate-600 dark:text-slate-300"
          data-testid="dataset-card-summary-subjects"
        >
          <Users className="h-3 w-3" />
          {summary.counts.subjects.toLocaleString()} subjects
        </span>
      )}
    </div>
  );
}

function PillRow({
  terms,
  limit,
  testId,
}: {
  terms: OntologyTerm[];
  limit: number;
  testId: string;
}) {
  const shown = terms.slice(0, limit);
  const extra = terms.length - shown.length;
  return (
    <span
      className="inline-flex flex-wrap items-center gap-1"
      data-testid={testId}
    >
      {shown.map((t) => (
        <OntologyTermPill
          key={`${t.label}-${t.ontologyId ?? ''}`}
          term={t}
          noLink
        />
      ))}
      {extra > 0 && (
        <span
          className="text-[10px] text-slate-500 dark:text-slate-400"
          data-testid={`${testId}-overflow`}
        >
          +{extra}
        </span>
      )}
    </span>
  );
}
