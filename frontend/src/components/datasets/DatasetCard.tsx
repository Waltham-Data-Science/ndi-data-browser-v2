import { Link } from 'react-router-dom';

import type { DatasetRecord } from '@/api/datasets';
import { Badge } from '@/components/ui/Badge';
import {
  Card,
  CardBody,
  CardTitle,
} from '@/components/ui/Card';
import { formatBytes, formatDate, truncate } from '@/lib/format';

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
      className="block group focus:outline-none focus-visible:ring-2 focus-visible:ring-brand-blue-3 rounded-lg"
      aria-label={`Open dataset ${dataset.name}`}
    >
      {/*
        Wide-format catalog card. Two-column grid on md+:
          - Left column: title, abstract clamp, contributors, species pills.
          - Right column: 5-item metadata fact strip (species, region, DOI,
            subjects, license) styled like the search.html mockup's
            `.meta-row` — each key tag stacked above its mono value.
        Hover lifts the card 1px with a shadow-md for tactile feedback,
        matching the marketing site's card hover treatment.
      */}
      <Card
        className="transition-all group-hover:-translate-y-[1px] group-hover:shadow-md group-hover:ring-border-strong"
        style={{ transitionDuration: 'var(--dur-base)', transitionTimingFunction: 'var(--ease-out)' }}
      >
        <CardBody className="p-6 md:p-7">
          {/* Tag row — status pill + license + branch (when meaningful) +
              draft override. Matches the `.c-tags` pattern in
              search.html; `branchName` here plays the role of the
              `v1.2` version tag in the mockup. */}
          <div className="flex items-center gap-2 mb-3 flex-wrap">
            <Badge variant="pub">&#9679; Published</Badge>
            {dataset.license && (
              <Badge variant="outline" className="font-mono normal-case">
                {dataset.license}
              </Badge>
            )}
            {dataset.branchName && dataset.branchName !== 'original' && (
              <Badge variant="teal" className="font-mono normal-case">
                {dataset.branchName}
              </Badge>
            )}
            {dataset.publishStatus && dataset.publishStatus !== 'published' && (
              <Badge variant="secondary">{dataset.publishStatus}</Badge>
            )}
          </div>

          {/* Title — no clamp: with 1200px width and 3-line scientific
              titles, the card stays reasonable. Truncate only if >6 lines
              which rarely happens. */}
          <CardTitle
            as="h3"
            className="text-[1.2rem] leading-snug mb-2 group-hover:text-ndi-teal transition-colors"
          >
            {dataset.name}
          </CardTitle>

          {/* Byline: contributors + date */}
          {(contributors.length > 0 || dataset.uploadedAt || dataset.createdAt) && (
            <p className="text-[13px] text-fg-secondary mb-4">
              {contributors.length > 0 && (
                <>
                  {contributors.slice(0, 3).join(', ')}
                  {contributors.length > 3 && ` +${contributors.length - 3}`}
                </>
              )}
              {contributors.length > 0 && (dataset.uploadedAt || dataset.createdAt) && (
                <span className="mx-2 text-fg-muted">·</span>
              )}
              {(dataset.uploadedAt || dataset.createdAt) && (
                <span className="text-fg-muted">
                  {formatDate(dataset.uploadedAt || dataset.createdAt!)}
                </span>
              )}
            </p>
          )}

          {/* Metadata fact strip — 5 columns on wide, wrap on narrow */}
          <div className="border-t border-b border-border-subtle/70 py-3 mb-4 flex flex-wrap gap-x-8 gap-y-3 text-[13px]">
            <MetaCell label="Species">
              {summary?.species && summary.species.length > 0 ? (
                <span className="font-mono">
                  {truncate(summary.species.map((s) => s.label).join(', '), 40)}
                </span>
              ) : dataset.species ? (
                <span className="font-mono">{truncate(dataset.species, 40)}</span>
              ) : (
                <span className="text-fg-muted">&mdash;</span>
              )}
            </MetaCell>
            <MetaCell label="Region">
              {summary?.brainRegions && summary.brainRegions.length > 0 ? (
                <span className="font-mono">
                  {truncate(summary.brainRegions.map((r) => r.label).join(', '), 40)}
                </span>
              ) : dataset.brainRegions ? (
                <span className="font-mono">{truncate(dataset.brainRegions, 40)}</span>
              ) : (
                <span className="text-fg-muted">&mdash;</span>
              )}
            </MetaCell>
            <MetaCell label="Documents">
              {summary?.counts.totalDocuments != null ? (
                <span className="font-mono">
                  {summary.counts.totalDocuments.toLocaleString()}
                </span>
              ) : dataset.documentCount != null ? (
                <span className="font-mono">
                  {dataset.documentCount.toLocaleString()}
                </span>
              ) : (
                <span className="text-fg-muted">&mdash;</span>
              )}
            </MetaCell>
            {summary && summary.counts.subjects > 0 && (
              <MetaCell label="Subjects">
                <span className="font-mono">
                  {summary.counts.subjects.toLocaleString()}
                </span>
              </MetaCell>
            )}
            {dataset.totalSize != null && dataset.totalSize > 0 && (
              <MetaCell label="Size">
                <span className="font-mono">{formatBytes(dataset.totalSize)}</span>
              </MetaCell>
            )}
            {dataset.doi && (
              <MetaCell label="DOI">
                {/* Strip the scheme only — keep the `doi.org/` registrar
                    path so users can recognize it's a persistent DOI
                    identifier and not an arbitrary URL. */}
                <span className="font-mono truncate inline-block max-w-[260px] align-bottom">
                  {dataset.doi.replace(/^https?:\/\//, '')}
                </span>
              </MetaCell>
            )}
          </div>

          {/* Abstract — 2-line clamp for scannability */}
          {abstract && (
            <p className="text-[13.5px] text-fg-secondary leading-relaxed line-clamp-2">
              {abstract}
            </p>
          )}
        </CardBody>
      </Card>
    </Link>
  );
}

/** Metadata cell — stacked UPPERCASE label over the value, matching the
 *  `.meta-row .m` pattern in search.html. */
function MetaCell({
  label,
  children,
}: {
  label: string;
  children: React.ReactNode;
}) {
  return (
    <div className="flex flex-col gap-1 min-w-0">
      <span className="text-[10px] font-bold tracking-[0.08em] uppercase text-fg-muted">
        {label}
      </span>
      <span className="text-fg-primary font-medium">{children}</span>
    </div>
  );
}

// CompactSummarySection and PillRow lived here in the narrow-card era
// to condense species + brain-region pills into a single row above the
// metadata. The new wide-format card bakes species + region into the
// MetaCell fact strip directly, so those helpers have been removed.
// If we ever re-introduce a compact variant, restore the pill helpers
// from `OntologyTermPill` in ./DatasetSummaryCard.
