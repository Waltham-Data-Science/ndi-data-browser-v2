import {
  BookOpen,
  Code2,
  FileText,
  FolderOpen,
  Globe,
  LayoutDashboard,
  Quote,
  Table2,
  Users,
} from 'lucide-react';
import { useState } from 'react';
import {
  Link,
  Navigate,
  NavLink,
  Outlet,
  useParams,
} from 'react-router-dom';

import {
  useDataset,
  useDatasetProvenance,
  useDatasetSummary,
  type DatasetRecord,
} from '@/api/datasets';
import { CiteModal } from '@/components/datasets/CiteModal';
import { DatasetProvenanceCard } from '@/components/datasets/DatasetProvenanceCard';
import { DatasetSummaryCard } from '@/components/datasets/DatasetSummaryCard';
import { UseThisDataModal } from '@/components/datasets/UseThisDataModal';
import { ErrorState } from '@/components/errors/ErrorState';
import { ExternalAnchor } from '@/components/ExternalAnchor';
import { Badge } from '@/components/ui/Badge';
import { Button } from '@/components/ui/Button';
import {
  Card,
  CardBody,
  CardHeader,
} from '@/components/ui/Card';
import { CardSkeleton, Skeleton } from '@/components/ui/Skeleton';
import { cn } from '@/lib/cn';
import { formatBytes, formatDate, formatNumber } from '@/lib/format';
import { normalizeOrcid } from '@/lib/orcid';
import type { DatasetSummary } from '@/types/dataset-summary';

const COMMON_CLASSES = [
  'subject',
  'element',
  'element_epoch',
  'treatment',
  'openminds_subject',
  'probe_location',
];

/**
 * DatasetDetailPage — `/datasets/:id`
 *
 * Shell for the three tabs a user cares about on a dataset:
 *   - Overview (default): dataset summary + details/abstract + document-class
 *     index — the "read this dataset" view.
 *   - Summary tables: the curated per-class summary grids (subjects, probes,
 *     epochs, treatments, openminds, ontology).
 *   - Document explorer: raw per-document list filterable by NDI class.
 *
 * The page is intentionally NOT a sidebar-plus-main layout anymore. The
 * previous design crammed summary / details / class-counts into a 340px
 * sidebar beside whatever tab the user opened, which left the tables
 * column at ~860px (cramped) and let the sidebar scroll a screen or two
 * past the main content. Splitting each concern into its own tab lets
 * every view use the full 1200px, and the shell just owns the hero +
 * tab bar + outlet.
 *
 * Sub-routes render into the <Outlet /> below the tab bar. The index
 * redirect lands you on /overview so a bookmark of /datasets/:id is
 * stable. The document-detail route lives OUTSIDE this shell (own hero
 * band) because it's a drill-down on a single document — not a tab on
 * the dataset.
 */
export function DatasetDetailPage() {
  const { id } = useParams();
  const ds = useDataset(id);

  if (!id) return <Navigate to="/datasets" replace />;

  return (
    <>
      <DetailHero ds={ds.data} isLoading={ds.isLoading} />
      <DatasetTabBar datasetId={id} />

      {/* `min-w-0` on the outlet wrapper keeps wide inner tables honest
          — CSS Grid items default to min-width: auto, so without this a
          table wider than the viewport would push the whole page wider
          instead of triggering its own overflow-x-auto scroll. */}
      <section className="mx-auto max-w-[1200px] px-7 py-7 min-w-0">
        <Outlet />
      </section>
    </>
  );
}

/* ─── Tab bar ────────────────────────────────────────────────────── */

/**
 * DatasetTabBar — three-tab nav under the hero. Uses react-router
 * NavLinks so the active tab reflects the URL (deep-linkable +
 * browser back/forward works). Sticky just under the page header so
 * when the user scrolls through a long Overview or document list, the
 * tab bar stays accessible.
 */
function DatasetTabBar({ datasetId }: { datasetId: string }) {
  const base = `/datasets/${datasetId}`;
  return (
    <div
      className="sticky top-[58px] z-30 bg-bg-surface border-b border-border-subtle"
      style={{ boxShadow: 'var(--shadow-xs)' }}
    >
      <nav
        role="tablist"
        aria-label="Dataset sections"
        className="mx-auto flex max-w-[1200px] items-center gap-1 px-7"
      >
        <TabLink to={`${base}/overview`} icon={<LayoutDashboard className="h-3.5 w-3.5" />}>
          Overview
        </TabLink>
        {/* `isActive` needs to be true for any tables/* or pivot/*
            path. NavLink's end={false} + a custom matcher handles
            that; the `tables/subject` default keeps first-click
            behavior identical to the legacy /datasets/:id → tables
            redirect we used to do. */}
        <TabLink
          to={`${base}/tables/subject`}
          matchPath={`${base}/tables`}
          altMatchPath={`${base}/pivot`}
          icon={<Table2 className="h-3.5 w-3.5" />}
        >
          Summary tables
        </TabLink>
        <TabLink
          to={`${base}/documents`}
          icon={<FolderOpen className="h-3.5 w-3.5" />}
        >
          Document explorer
        </TabLink>
      </nav>
    </div>
  );
}

function TabLink({
  to,
  matchPath,
  altMatchPath,
  icon,
  children,
}: {
  to: string;
  matchPath?: string;
  altMatchPath?: string;
  icon: React.ReactNode;
  children: React.ReactNode;
}) {
  // NavLink's default `isActive` matches exact-or-prefix at the end of
  // the path segment. We override with a custom matcher for the tables
  // tab because `Summary tables` must light up for /tables/subject,
  // /tables/probe, AND /pivot/session etc.
  return (
    <NavLink
      to={to}
      role="tab"
      className={({ isActive }: { isActive: boolean }) => {
        const active =
          isActive ||
          (!!matchPath &&
            typeof window !== 'undefined' &&
            window.location.pathname.startsWith(matchPath)) ||
          (!!altMatchPath &&
            typeof window !== 'undefined' &&
            window.location.pathname.startsWith(altMatchPath));
        return cn(
          '-mb-px inline-flex items-center gap-1.5 border-b-2 px-4 py-3 text-[13.5px] font-medium transition-colors',
          'focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-ndi-teal',
          active
            ? 'border-ndi-teal text-ndi-teal'
            : 'border-transparent text-fg-secondary hover:text-brand-navy',
        );
      }}
    >
      {icon}
      <span>{children}</span>
    </NavLink>
  );
}

/* ─── Overview tab ───────────────────────────────────────────────── */

/**
 * OverviewTab — the "read this dataset" view. Three sections stacked
 * top-to-bottom, each rendered in a content block sized to its own
 * natural length so nothing competes with a neighboring column for
 * vertical space:
 *
 *   1. Summary (synthesized facts: species, regions, counts, probe types)
 *   2. Details (abstract, contributors, funding, publications, identifiers,
 *      cite / use-this-data buttons)
 *   3. Document classes (clickable class-count list → Raw Documents /
 *      Summary tables per the dispatch rules in ClassCountsList)
 *
 * Provenance (when present) renders between Summary and Details — it's
 * contextual metadata about how the dataset was derived, and belongs
 * near the other factsheet content.
 *
 * The two-column wrapper on ≥lg screens gives the abstract a readable
 * 60% width while keeping the summary + class-counts in a right
 * sidecar that doesn't scroll independently because everything sits
 * in the normal page flow — no mismatched heights because there's no
 * parallel scrolling child.
 */
export function OverviewTab() {
  const { id } = useParams();
  const ds = useDataset(id);
  const summary = useDatasetSummary(id);
  const provenance = useDatasetProvenance(id);

  if (!id) return <Navigate to="/datasets" replace />;

  return (
    <div className="grid gap-5 lg:grid-cols-[1fr_360px] min-w-0">
      {/* ── Main column: details (abstract + authors + pubs + cite) ── */}
      <div className="space-y-4 min-w-0 order-2 lg:order-1">
        {ds.isLoading && <CardSkeleton />}
        {ds.isError && <ErrorState error={ds.error} onRetry={() => ds.refetch()} />}
        {ds.data && (
          <DatasetOverviewCard
            ds={ds.data}
            datasetId={id}
            summary={summary.data}
          />
        )}
      </div>

      {/* ── Sidecar: summary pills + provenance ─────────────────────── */}
      <aside className="space-y-4 min-w-0 order-1 lg:order-2">
        {summary.isLoading && <CardSkeleton />}
        {summary.isError && (
          <ErrorState error={summary.error} onRetry={() => summary.refetch()} />
        )}
        {summary.data && <DatasetSummaryCard summary={summary.data} />}

        {/* Plan B B5 — dataset provenance card (derivation graph,
            cross-dataset depends_on edges, branches). Errors on
            provenance degrade silently so a flaky aggregator never
            blocks the detail view. */}
        {provenance.data && (
          <DatasetProvenanceCard provenance={provenance.data} />
        )}

        {/* The "Document classes" card lives on the Document Explorer
            tab now (same ClassCountsList component, same progress-bar
            style) — surfacing it here too was redundant with that tab's
            purpose. Users who want to jump into the per-class list
            can click the Document Explorer tab above. */}
      </aside>
    </div>
  );
}

/* ─── Hero band ──────────────────────────────────────────────────── */

function DetailHero({
  ds,
  isLoading,
}: {
  ds?: DatasetRecord;
  isLoading: boolean;
}) {
  // Published = published. Draft/in-review = "DRAFT" eyebrow. Loading
  // shows skeleton to avoid layout shift when the dataset resolves.
  const status = ds?.isPublished === false ? 'DRAFT' : 'PUBLIC DATASET';
  const affiliation =
    ds?.affiliation ??
    ds?.contributors
      ?.slice(0, 1)
      .map((c) => [c.firstName, c.lastName].filter(Boolean).join(' '))
      .join('') ??
    '';

  return (
    <section
      className="relative overflow-hidden text-white"
      style={{ background: 'var(--grad-depth)' }}
      aria-labelledby="detail-hero"
    >
      <div
        aria-hidden
        className="absolute inset-0 pointer-events-none"
        style={{
          backgroundImage: "url('/brand/ndicloud-emblem.svg')",
          backgroundSize: '120px',
          backgroundRepeat: 'repeat',
          opacity: 0.05,
        }}
      />
      <div className="relative mx-auto max-w-[1200px] px-7 py-10 md:py-12">
        {/* Back link — always visible, not just on loading */}
        <div className="mb-3">
          <Link
            to="/datasets"
            className="inline-flex items-center gap-1.5 text-[12px] text-white/60 hover:text-white/90 transition-colors"
          >
            <span aria-hidden>&larr;</span> Back to Data Commons
          </Link>
        </div>

        {/* Eyebrow (status + DOI) */}
        <div className="eyebrow mb-4">
          <span className="eyebrow-dot" aria-hidden />
          {status}
          {ds?.doi && (
            <>
              <span className="mx-2 opacity-30" aria-hidden>
                |
              </span>
              <span className="font-mono normal-case tracking-normal text-[10.5px] text-white/85">
                DOI: {ds.doi}
              </span>
            </>
          )}
        </div>

        {/* Title */}
        {isLoading ? (
          <Skeleton className="h-9 w-3/4 max-w-[720px] bg-white/15" />
        ) : (
          <h1
            id="detail-hero"
            className="text-white font-display font-extrabold tracking-tight leading-[1.15] text-[1.8rem] md:text-[2.1rem] mb-2 max-w-4xl"
          >
            {ds?.name ?? 'Dataset'}
          </h1>
        )}

        {/* Affiliation sub-line */}
        {affiliation && (
          <p className="text-white/60 text-[13.5px] mb-5">
            {affiliation}
            {ds?.uploadedAt && (
              <span className="text-white/40 font-mono ml-2">
                &middot; {formatDate(ds.uploadedAt)}
              </span>
            )}
          </p>
        )}

        {/* Fact strip */}
        {ds && (
          <dl className="flex flex-wrap gap-x-8 gap-y-3 pt-4 border-t border-white/10 text-[11.5px]">
            {ds.species && <HeroFact label="Species" value={ds.species} />}
            {ds.brainRegions && (
              <HeroFact label="Region" value={ds.brainRegions} mono />
            )}
            {ds.documentCount != null && (
              <HeroFact
                label="Documents"
                value={formatNumber(ds.documentCount)}
                mono
              />
            )}
            {ds.numberOfSubjects != null && ds.numberOfSubjects > 0 && (
              <HeroFact
                label="Subjects"
                value={formatNumber(ds.numberOfSubjects)}
                mono
              />
            )}
            {ds.totalSize != null && ds.totalSize > 0 && (
              <HeroFact label="Size" value={formatBytes(ds.totalSize)} mono />
            )}
            {ds.license && <HeroFact label="License" value={ds.license} mono />}
          </dl>
        )}
      </div>
    </section>
  );
}

function HeroFact({
  label,
  value,
  mono,
}: {
  label: string;
  value: string | number;
  mono?: boolean;
}) {
  return (
    <div className="flex flex-col gap-1">
      <dt className="uppercase tracking-wider text-white/50 text-[10px] font-semibold">
        {label}
      </dt>
      <dd
        className={
          mono
            ? 'font-mono text-white text-[13px]'
            : 'text-white text-[13px] font-medium'
        }
      >
        {value}
      </dd>
    </div>
  );
}

/* ─── Sidebar overview card ──────────────────────────────────────── */

function DatasetOverviewCard({
  ds,
  datasetId,
  summary,
}: {
  ds: DatasetRecord;
  datasetId: string;
  summary?: DatasetSummary;
}) {
  const abstract = ds.description ?? ds.abstract;
  const [citeOpen, setCiteOpen] = useState(false);
  const [useDataOpen, setUseDataOpen] = useState(false);
  return (
    <Card>
      <CardHeader>
        {/* Card-scoped h2 (hero has the h1); keeps heading order clean for axe. */}
        <h2 className="text-[14px] font-bold text-brand-navy leading-tight">
          Details
        </h2>
        <div className="flex flex-wrap gap-1.5 pt-1">
          {ds.license && <Badge variant="outline">{ds.license}</Badge>}
          {ds.branchName && ds.branchName !== 'main' && (
            <Badge variant="secondary">{ds.branchName}</Badge>
          )}
          {ds.isPublished === false && <Badge variant="secondary">draft</Badge>}
        </div>
      </CardHeader>

      <CardBody className="space-y-4 text-sm">
        {abstract && (
          <p className="text-fg-secondary text-[13px] leading-relaxed">
            {abstract}
          </p>
        )}

        {(ds.contributors?.length ?? 0) > 0 && (
          <div className="space-y-1.5">
            <h3 className="text-xs font-semibold text-fg-muted flex items-center gap-1 uppercase tracking-wide">
              <Users className="h-3 w-3" /> Contributors
            </h3>
            <ul className="space-y-0.5 text-xs">
              {ds.contributors!.map((c, i) => (
                <ContributorRow key={`${c.firstName}-${c.lastName}-${i}`} c={c} />
              ))}
            </ul>
          </div>
        )}

        {(ds.correspondingAuthors?.length ?? 0) > 0 && (
          <div className="space-y-1.5">
            <h3 className="text-xs font-semibold text-fg-muted uppercase tracking-wide">
              Corresponding authors
            </h3>
            <ul className="space-y-0.5 text-xs">
              {ds.correspondingAuthors!.map((c, i) => (
                <ContributorRow key={`${c.firstName}-${c.lastName}-${i}`} c={c} />
              ))}
            </ul>
          </div>
        )}

        {(ds.funding?.length ?? 0) > 0 && (
          <div className="space-y-1">
            <h3 className="text-xs font-semibold text-fg-muted uppercase tracking-wide">
              Funding
            </h3>
            <p className="text-xs text-fg-secondary">
              {ds
                .funding!.map((f) => f.source)
                .filter(Boolean)
                .join('; ')}
            </p>
          </div>
        )}

        {(ds.associatedPublications?.length ?? 0) > 0 && (
          <div className="space-y-1.5">
            <h3 className="text-xs font-semibold text-fg-muted flex items-center gap-1 uppercase tracking-wide">
              <BookOpen className="h-3 w-3" /> Associated publications
            </h3>
            <ul className="space-y-1 text-xs">
              {ds.associatedPublications!.map((p, i) => (
                <PublicationRow key={p.DOI ?? p.PMID ?? i} p={p} />
              ))}
            </ul>
          </div>
        )}

        {/* Identifiers row */}
        <dl className="grid grid-cols-[auto_minmax(0,1fr)] gap-x-3 gap-y-1 text-[11px] text-fg-muted font-mono border-t border-border-subtle pt-3">
          {ds.doi && (
            <>
              <dt>DOI</dt>
              <dd className="truncate">
                <ExternalAnchor href={ds.doi} label={ds.doi} />
              </dd>
            </>
          )}
          {ds.pubMedId && (
            <>
              <dt>PubMed</dt>
              <dd>
                <ExternalAnchor
                  href={`https://pubmed.ncbi.nlm.nih.gov/${ds.pubMedId}/`}
                  label={ds.pubMedId}
                />
              </dd>
            </>
          )}
          {ds.organizationId && (
            <>
              <dt>Org</dt>
              <dd>{ds.organizationId}</dd>
            </>
          )}
          <dt>Created</dt>
          <dd>{formatDate(ds.createdAt)}</dd>
          <dt>Updated</dt>
          <dd>{formatDate(ds.updatedAt)}</dd>
        </dl>

        {/* Action buttons */}
        <div
          className="flex flex-wrap gap-2 border-t border-border-subtle pt-3"
          data-testid="dataset-actions"
        >
          <Button
            variant="secondary"
            size="sm"
            onClick={() => setCiteOpen(true)}
            disabled={!summary}
            data-testid="open-cite-modal"
            aria-label="Open citation formats"
          >
            <Quote className="h-3 w-3" aria-hidden />
            Cite
          </Button>
          <Button
            variant="secondary"
            size="sm"
            onClick={() => setUseDataOpen(true)}
            data-testid="open-use-data-modal"
            aria-label="Open code snippets for local analysis"
          >
            <Code2 className="h-3 w-3" aria-hidden />
            Use this data
          </Button>
        </div>
      </CardBody>
      {summary && (
        <CiteModal
          open={citeOpen}
          onClose={() => setCiteOpen(false)}
          citation={summary.citation}
        />
      )}
      <UseThisDataModal
        open={useDataOpen}
        onClose={() => setUseDataOpen(false)}
        datasetId={datasetId}
      />
    </Card>
  );
}

/* ─── Sub-row renderers (unchanged semantics, token-migrated colors) ─── */

function ContributorRow({ c }: { c: import('@/api/datasets').Contributor }) {
  const name = [c.firstName, c.lastName].filter(Boolean).join(' ').trim();
  if (!name && !c.contact) return null;
  // `normalizeOrcid` returns undefined for unrecognized shapes so we
  // simply don't render the affordance — the cloud API sometimes ships
  // bare `NNNN-NNNN-NNNN-NNNN` ids which would resolve against our own
  // origin if we linked naively.
  const orcidHref = normalizeOrcid(c.orcid);
  return (
    <li className="flex items-center gap-1.5">
      <span className="text-fg-secondary">{name || c.contact}</span>
      {orcidHref && (
        <ExternalAnchor
          href={orcidHref}
          label="ORCID"
          className="text-[10px]"
          iconSize={10}
        />
      )}
    </li>
  );
}

function PublicationRow({ p }: { p: import('@/api/datasets').AssociatedPublication }) {
  const title = p.title || p.DOI || p.PMID || 'Publication';
  // `min-w-0 overflow-hidden` on the <li> so the ExternalAnchor / long
  // title can truncate with ellipsis rather than pushing the sidebar
  // card wider. Publication titles are a full sentence; DOIs are long
  // URLs. Same class of bug Steve caught on the dataset DOI row.
  return (
    <li className="min-w-0 space-y-0.5 overflow-hidden">
      {p.DOI ? (
        <ExternalAnchor
          href={p.DOI}
          label={title}
          className="text-xs leading-snug"
        />
      ) : (
        <span className="block truncate text-fg-secondary">{title}</span>
      )}
      <div className="flex flex-wrap gap-2 text-[10px] text-fg-muted font-mono">
        {p.DOI && <span>DOI</span>}
        {p.PMID && (
          <ExternalAnchor
            href={`https://pubmed.ncbi.nlm.nih.gov/${p.PMID}/`}
            label={`PMID ${p.PMID}`}
            iconSize={10}
            className="text-[10px]"
          />
        )}
        {p.PMCID && (
          <ExternalAnchor
            href={`https://www.ncbi.nlm.nih.gov/pmc/articles/${p.PMCID}/`}
            label={p.PMCID}
            iconSize={10}
            className="text-[10px]"
          />
        )}
      </div>
    </li>
  );
}

export function ClassCountsList({
  datasetId,
  data,
}: {
  datasetId: string;
  data: { totalDocuments: number; classCounts: Record<string, number> };
}) {
  const sorted = Object.entries(data.classCounts).sort((a, b) => b[1] - a[1]);
  const total = Math.max(1, data.totalDocuments);
  return (
    <>
      <p className="mb-2 text-[11px] text-fg-muted font-mono">
        {formatNumber(data.totalDocuments)} documents total
      </p>
      <ul className="space-y-1">
        {sorted.slice(0, 25).map(([cls, n]) => {
          const pct = (n / total) * 100;
          const isSummary = COMMON_CLASSES.includes(cls);
          // Route subject/element/epoch to the rich table view; the rest
          // go through the Raw Documents list.
          const href = isSummary
            ? `/datasets/${datasetId}/tables/${cls}`
            : `/datasets/${datasetId}/documents?class=${encodeURIComponent(cls)}`;
          return (
            <li key={cls} className="text-xs">
              <Link
                to={href}
                className="flex items-center gap-2 hover:text-ndi-teal transition-colors"
              >
                <span className="font-mono truncate flex-1">{cls}</span>
                <span className="text-fg-muted">{formatNumber(n)}</span>
                {isSummary && (
                  <FileText className="h-3 w-3 text-fg-muted" aria-hidden />
                )}
                {!isSummary && (
                  <Globe className="h-3 w-3 text-fg-muted" aria-hidden />
                )}
              </Link>
              <div
                className="mt-0.5 h-1 rounded bg-bg-muted overflow-hidden"
                role="progressbar"
                aria-label={`${cls} ${formatNumber(n)} of ${formatNumber(data.totalDocuments)}`}
              >
                <div
                  className="h-1 rounded bg-ndi-teal"
                  style={{ width: `${Math.max(2, pct)}%` }}
                />
              </div>
            </li>
          );
        })}
      </ul>
    </>
  );
}
