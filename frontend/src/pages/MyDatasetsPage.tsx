import {
  HardDrive,
  FileCheck,
  Layers,
  Quote,
  LayoutGrid,
  List,
  FileText,
} from 'lucide-react';
import { useState, useMemo } from 'react';
import { Link } from 'react-router-dom';

import { useMe } from '@/api/auth';
import { useMyDatasets, type DatasetRecord, type MyScope } from '@/api/datasets';
import { DatasetCard } from '@/components/datasets/DatasetCard';
import { ErrorState } from '@/components/errors/ErrorState';
import { Badge } from '@/components/ui/Badge';
import { CardSkeleton } from '@/components/ui/Skeleton';
import { cn } from '@/lib/cn';
import { formatBytes, formatDate, formatNumber } from '@/lib/format';

type StatusFilter = 'all' | 'published' | 'draft';
type ViewMode = 'grid' | 'table';

/**
 * Private workspace — `/my`. Logged-in-user view of every dataset
 * owned by the caller's org(s).
 *
 * Layout:
 *   1. Depth-gradient hero band with eyebrow, org-name h1, page sub, and
 *      a four-metric stat row (Datasets / Published / Storage / Orgs).
 *      The data browser.html mockup uses a lab-avatar workspace pill; we
 *      pull the same idea with an inline org identifier.
 *   2. Filter chip row (All / Published / Draft) — reads the loaded
 *      datasets' publishStatus; purely client-side for now (we already
 *      fetch the full list via /api/datasets/my).
 *   3. Admin scope toggle (mine / all) — surfaces only for `isAdmin`.
 *   4. Responsive card grid. Reuses the same DatasetCard as the public
 *      catalog; publishStatus badge distinguishes draft / in-review /
 *      published inline.
 *
 * Preserves all existing behavior:
 *   - `useMe()` + `useMyDatasets(enabled, scope)` React Query fetching
 *   - Admin-only scope toggle semantics (backend downgrades non-admins)
 *   - Legacy `scope=all` firehose stays accessible to admins
 */
export function MyDatasetsPage() {
  const me = useMe();
  const isAdmin = me.data?.isAdmin ?? false;
  const [scope, setScope] = useState<MyScope>('mine');
  const activeScope: MyScope = isAdmin ? scope : 'mine';
  const q = useMyDatasets(me.isSuccess, activeScope);

  const [statusFilter, setStatusFilter] = useState<StatusFilter>('all');
  // Table vs grid — mockup's data-browser.html has a dense row layout
  // (row icons, status tags, mono DOI, updated-at) that's much faster
  // for power users scanning a long workspace list. Grid stays the
  // default for visual browsability.
  const [viewMode, setViewMode] = useState<ViewMode>('grid');

  // Filter counts + visible list — computed from the loaded datasets.
  const { visible, counts, totalSize } = useMemo(() => {
    const datasets = q.data?.datasets ?? [];
    const byStatus = { all: datasets.length, published: 0, draft: 0 };
    let sizeSum = 0;
    for (const d of datasets) {
      sizeSum += d.totalSize ?? 0;
      if (d.publishStatus === 'published' || d.isPublished) byStatus.published += 1;
      else byStatus.draft += 1;
    }
    const visibleList = datasets.filter((d) => {
      if (statusFilter === 'all') return true;
      const published = d.publishStatus === 'published' || d.isPublished;
      return statusFilter === 'published' ? published : !published;
    });
    return { visible: visibleList, counts: byStatus, totalSize: sizeSum };
  }, [q.data, statusFilter]);

  if (me.isError) return <ErrorState error={me.error} />;

  const orgCount = me.data?.organizationIds?.length ?? 0;
  const isAllScope = activeScope === 'all';

  return (
    <>
      {/* ── Hero band ───────────────────────────────────────────────── */}
      <section
        className="relative overflow-hidden text-white"
        style={{ background: 'var(--grad-depth)' }}
        aria-labelledby="my-hero"
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
        <div className="relative mx-auto max-w-[1200px] px-7 py-12 md:py-14">
          <div className="flex flex-wrap items-start justify-between gap-4">
            <div>
              <div className="eyebrow mb-3">
                <span className="eyebrow-dot" aria-hidden />
                Your workspace
                {isAdmin && (
                  <Badge
                    variant="secondary"
                    className="ml-2 text-[10px] bg-white/15 text-white border-white/20"
                  >
                    admin
                  </Badge>
                )}
              </div>
              <h1
                id="my-hero"
                className="text-white font-display font-extrabold tracking-tight leading-tight text-[2rem] md:text-[2.25rem] mb-2"
              >
                {isAllScope
                  ? 'All in-review datasets, cloud-wide'
                  : 'My organization\u2019s datasets.'}
              </h1>
              <p className="text-white/70 text-[14.5px] leading-relaxed max-w-[620px]">
                {isAllScope
                  ? 'Admin debug view — every in-review dataset across every org in the cloud (legacy /datasets/unpublished firehose).'
                  : 'Every dataset owned by your organization — published, in-review, and drafts. Click a card to inspect subjects, probes, epochs, and raw documents.'}
              </p>
            </div>

            {isAdmin && (
              <ScopeToggle value={scope} onChange={setScope} />
            )}
          </div>

          {/* Stat strip */}
          {q.data && (
            <div className="mt-8 grid grid-cols-2 md:grid-cols-4 gap-4">
              <HeroStat
                icon={<Layers className="h-3.5 w-3.5" />}
                label="Total datasets"
                value={formatNumber(q.data.totalNumber ?? q.data.datasets.length)}
              />
              <HeroStat
                icon={<FileCheck className="h-3.5 w-3.5" />}
                label="Published"
                value={formatNumber(counts.published)}
                hint={`${formatNumber(counts.draft)} draft / in-review`}
              />
              <HeroStat
                icon={<HardDrive className="h-3.5 w-3.5" />}
                label="Storage used"
                value={formatBytes(totalSize)}
                hint="across all datasets"
              />
              <HeroStat
                icon={<Quote className="h-3.5 w-3.5" />}
                label="Organizations"
                value={formatNumber(orgCount)}
                hint={orgCount === 1 ? 'one workspace' : 'total'}
              />
            </div>
          )}
        </div>
      </section>

      {/* ── Body ─────────────────────────────────────────────────────── */}
      <section className="mx-auto max-w-[1200px] px-7 py-7">
        {/* Filter chip row + view toggle */}
        <div className="flex flex-wrap items-center gap-2 mb-5">
          <FilterChip
            active={statusFilter === 'all'}
            onClick={() => setStatusFilter('all')}
            count={counts.all}
          >
            All
          </FilterChip>
          <FilterChip
            active={statusFilter === 'published'}
            onClick={() => setStatusFilter('published')}
            count={counts.published}
          >
            Published
          </FilterChip>
          <FilterChip
            active={statusFilter === 'draft'}
            onClick={() => setStatusFilter('draft')}
            count={counts.draft}
          >
            Draft / in-review
          </FilterChip>
          <div className="ml-auto">
            <ViewToggle value={viewMode} onChange={setViewMode} />
          </div>
        </div>

        {/* Loading */}
        {(me.isLoading || q.isLoading) && (
          <div className="grid gap-3 sm:grid-cols-2 xl:grid-cols-3">
            {Array.from({ length: 4 }).map((_, i) => (
              <CardSkeleton key={i} />
            ))}
          </div>
        )}

        {/* Error */}
        {q.isError && <ErrorState error={q.error} onRetry={() => q.refetch()} />}

        {/* Empty */}
        {!q.isLoading && !q.isError && q.data && q.data.datasets.length === 0 && (
          <div className="rounded-lg border border-dashed border-border-subtle bg-white p-12 text-center">
            <h3 className="text-[16px] font-bold text-brand-navy mb-1">
              {isAllScope
                ? 'No in-review datasets cloud-wide'
                : 'No datasets yet in your workspace'}
            </h3>
            <p className="text-[13.5px] text-fg-secondary max-w-md mx-auto">
              {isAllScope
                ? 'Switch back to “My org only” for your scoped view.'
                : 'Datasets uploaded via NDI Cloud (ndi-matlab, ndi-python, or the Data Browser) will appear here — published work, in-review submissions, and drafts.'}
            </p>
          </div>
        )}

        {/* Non-empty but filter returns nothing */}
        {!q.isLoading &&
          q.data &&
          q.data.datasets.length > 0 &&
          visible.length === 0 && (
            <div className="rounded-lg border border-dashed border-border-subtle bg-white p-10 text-center">
              <p className="text-[13.5px] text-fg-secondary">
                No datasets match the&nbsp;
                <strong className="text-brand-navy font-semibold">
                  {statusFilter}
                </strong>
                &nbsp;filter.
              </p>
              <button
                type="button"
                onClick={() => setStatusFilter('all')}
                className="mt-2 text-[12.5px] text-fg-link hover:underline underline-offset-2"
              >
                Show all
              </button>
            </div>
          )}

        {/* Cards or table */}
        {!q.isLoading && visible.length > 0 && (
          viewMode === 'grid' ? (
            <div className="grid gap-3 sm:grid-cols-2 xl:grid-cols-3">
              {visible.map((d) => (
                <DatasetCard key={d.id} dataset={d} />
              ))}
            </div>
          ) : (
            <DatasetTable datasets={visible} />
          )
        )}
      </section>
    </>
  );
}

/* ─── View mode toggle (list / grid) ─────────────────────────────── */

function ViewToggle({
  value,
  onChange,
}: {
  value: ViewMode;
  onChange: (next: ViewMode) => void;
}) {
  return (
    <div
      role="group"
      aria-label="View mode"
      className="inline-flex items-center gap-0 rounded-md border border-border-subtle overflow-hidden bg-white"
      data-testid="view-toggle"
    >
      <ViewToggleButton
        active={value === 'grid'}
        onClick={() => onChange('grid')}
        label="Grid view"
      >
        <LayoutGrid className="h-3.5 w-3.5" aria-hidden />
      </ViewToggleButton>
      <ViewToggleButton
        active={value === 'table'}
        onClick={() => onChange('table')}
        label="Table view"
      >
        <List className="h-3.5 w-3.5" aria-hidden />
      </ViewToggleButton>
    </div>
  );
}

function ViewToggleButton({
  active,
  onClick,
  label,
  children,
}: {
  active: boolean;
  onClick: () => void;
  label: string;
  children: React.ReactNode;
}) {
  return (
    <button
      type="button"
      onClick={onClick}
      aria-pressed={active}
      aria-label={label}
      className={cn(
        'inline-flex items-center justify-center px-2.5 py-1.5 transition-colors',
        active
          ? 'bg-bg-muted text-brand-navy'
          : 'text-fg-muted hover:text-brand-navy hover:bg-bg-muted/60',
      )}
    >
      {children}
    </button>
  );
}

/* ─── Dense table view ──────────────────────────────────────────── */

/**
 * Dense workspace table — mirrors the data-browser.html mockup's row
 * layout. One row per dataset with a colored status squircle, title +
 * slug subline, status badge, species, document count, DOI, updated
 * timestamp. Clicking a row navigates to the dataset detail page.
 *
 * No horizontal scrolling by design — we collapse the less-critical
 * columns (DOI, species) on narrow widths rather than scroll.
 */
function DatasetTable({ datasets }: { datasets: DatasetRecord[] }) {
  return (
    <div
      className="bg-white border border-border-subtle rounded-xl overflow-hidden"
      style={{ boxShadow: 'var(--shadow-xs)' }}
    >
      <table className="w-full border-collapse text-[13.5px]">
        <thead>
          <tr>
            <th className="text-left font-bold tracking-[0.1em] uppercase text-[10px] text-fg-muted px-4 py-3 border-b border-border-subtle bg-bg-muted" style={{ width: '40%' }}>
              Dataset
            </th>
            <th className="text-left font-bold tracking-[0.1em] uppercase text-[10px] text-fg-muted px-4 py-3 border-b border-border-subtle bg-bg-muted">
              Status
            </th>
            <th className="text-left font-bold tracking-[0.1em] uppercase text-[10px] text-fg-muted px-4 py-3 border-b border-border-subtle bg-bg-muted hidden md:table-cell">
              Species
            </th>
            <th className="text-left font-bold tracking-[0.1em] uppercase text-[10px] text-fg-muted px-4 py-3 border-b border-border-subtle bg-bg-muted">
              Docs
            </th>
            <th className="text-left font-bold tracking-[0.1em] uppercase text-[10px] text-fg-muted px-4 py-3 border-b border-border-subtle bg-bg-muted hidden lg:table-cell">
              DOI
            </th>
            <th className="text-left font-bold tracking-[0.1em] uppercase text-[10px] text-fg-muted px-4 py-3 border-b border-border-subtle bg-bg-muted hidden sm:table-cell">
              Updated
            </th>
          </tr>
        </thead>
        <tbody>
          {datasets.map((d, i) => (
            <DatasetRow key={d.id} dataset={d} isLast={i === datasets.length - 1} />
          ))}
        </tbody>
      </table>
    </div>
  );
}

function DatasetRow({ dataset, isLast }: { dataset: DatasetRecord; isLast: boolean }) {
  const published = dataset.publishStatus === 'published' || dataset.isPublished;
  const statusKey = published ? 'pub' : 'draft';
  const updatedAt = dataset.updatedAt || dataset.uploadedAt || dataset.createdAt;
  const docCount = dataset.summary?.counts?.totalDocuments ?? dataset.documentCount;

  return (
    <tr
      className={cn(
        'hover:bg-bg-muted transition-colors cursor-pointer',
        isLast ? '' : 'border-b border-border-subtle/60',
      )}
    >
      <td className="px-4 py-3 align-middle">
        <Link
          to={`/datasets/${dataset.id}`}
          className="flex items-center gap-3 min-w-0"
        >
          <span
            className={cn(
              'shrink-0 flex items-center justify-center h-8 w-8 rounded-md',
              statusKey === 'pub'
                ? 'bg-[rgba(23,167,255,0.12)] text-brand-blue'
                : 'bg-bg-muted text-fg-muted',
            )}
            aria-hidden
          >
            <FileText className="h-3.5 w-3.5" />
          </span>
          <div className="min-w-0">
            <div className="font-semibold text-fg-primary truncate group-hover:text-ndi-teal">
              {dataset.name}
            </div>
            {dataset.id && (
              <div className="font-mono text-[10.5px] text-fg-muted truncate">
                {dataset.id}
              </div>
            )}
          </div>
        </Link>
      </td>
      <td className="px-4 py-3 align-middle">
        <Badge variant={published ? 'pub' : 'secondary'}>
          {published ? '● Published' : 'Draft'}
        </Badge>
      </td>
      <td className="px-4 py-3 align-middle hidden md:table-cell text-fg-secondary">
        {dataset.species ? <span className="truncate">{shortenSpecies(dataset.species)}</span> : <span className="text-fg-muted">&mdash;</span>}
      </td>
      <td className="px-4 py-3 align-middle font-mono text-[12px] text-fg-secondary">
        {docCount != null ? formatNumber(docCount) : <span className="text-fg-muted">&mdash;</span>}
      </td>
      <td className="px-4 py-3 align-middle hidden lg:table-cell">
        {dataset.doi ? (
          <span className="font-mono text-[11px] text-fg-secondary truncate max-w-[200px] inline-block" title={dataset.doi}>
            {dataset.doi.replace(/^https?:\/\//, '')}
          </span>
        ) : (
          <span className="text-fg-muted">&mdash;</span>
        )}
      </td>
      <td className="px-4 py-3 align-middle hidden sm:table-cell text-[12px] text-fg-muted">
        {updatedAt ? formatDate(updatedAt) : <span>&mdash;</span>}
      </td>
    </tr>
  );
}

/** NDI datasets typically list species as a comma-separated string
 *  with parenthesized scientific names (e.g., "Ferrets (Mustelo putorius
 *  furo), Rats (Rattus norvegicus)"). For a table row we just want the
 *  first primary species name; preserve the full string in title. */
function shortenSpecies(species: string): string {
  const first = species.split(',')[0]?.trim() ?? species;
  return first.length > 32 ? `${first.slice(0, 32)}…` : first;
}

/* ─── Hero stat card ─────────────────────────────────────────────── */

function HeroStat({
  icon,
  label,
  value,
  hint,
}: {
  icon: React.ReactNode;
  label: string;
  value: string | number;
  hint?: string;
}) {
  return (
    <div
      className="rounded-lg border border-white/10 p-4"
      style={{ background: 'rgba(255,255,255,0.05)' }}
    >
      <div className="flex items-center gap-1.5 text-[10.5px] font-bold tracking-[0.1em] uppercase text-white/55 mb-2">
        {icon}
        <span>{label}</span>
      </div>
      <div className="font-display font-bold text-[24px] tracking-tight leading-none text-white mb-1">
        {value}
      </div>
      {hint && (
        <div className="text-[11.5px] text-white/45 font-mono">{hint}</div>
      )}
    </div>
  );
}

/* ─── Filter chips ───────────────────────────────────────────────── */

function FilterChip({
  active,
  onClick,
  count,
  children,
}: {
  active: boolean;
  onClick: () => void;
  count?: number;
  children: React.ReactNode;
}) {
  return (
    <button
      type="button"
      onClick={onClick}
      aria-pressed={active}
      className={cn(
        'inline-flex items-center gap-1.5 rounded-md border px-3 py-1.5 text-[12.5px] font-medium transition-all',
        active
          ? 'bg-ndi-teal-light border-ndi-teal-border text-ndi-teal font-semibold'
          : 'bg-white border-border-subtle text-fg-secondary hover:border-border-strong',
      )}
    >
      <span>{children}</span>
      {count != null && (
        <span
          className={cn(
            'font-mono text-[11px] px-1.5 py-0 rounded-full',
            active ? 'bg-white/70' : 'bg-gray-100',
          )}
        >
          {count}
        </span>
      )}
    </button>
  );
}

/* ─── Admin scope toggle ─────────────────────────────────────────── */

function ScopeToggle({
  value,
  onChange,
}: {
  value: MyScope;
  onChange: (next: MyScope) => void;
}) {
  return (
    <div
      role="group"
      aria-label="Dataset scope"
      className="inline-flex items-center rounded-full border border-white/15 overflow-hidden text-[12.5px] shrink-0"
      data-testid="my-scope-toggle"
      style={{ background: 'rgba(255,255,255,0.06)' }}
    >
      <ScopeToggleButton
        active={value === 'mine'}
        onClick={() => onChange('mine')}
      >
        My org only
      </ScopeToggleButton>
      <ScopeToggleButton
        active={value === 'all'}
        onClick={() => onChange('all')}
      >
        All orgs
      </ScopeToggleButton>
    </div>
  );
}

function ScopeToggleButton({
  active,
  onClick,
  children,
}: {
  active: boolean;
  onClick: () => void;
  children: React.ReactNode;
}) {
  return (
    <button
      type="button"
      onClick={onClick}
      aria-pressed={active}
      className={cn(
        'px-3.5 py-1.5 font-medium transition-colors',
        active ? 'bg-white text-brand-navy' : 'text-white/75 hover:text-white',
      )}
    >
      {children}
    </button>
  );
}
