import { Search } from 'lucide-react';
import { useMemo, useState, type FormEvent } from 'react';
import { useSearchParams } from 'react-router-dom';

import { usePublishedDatasets } from '@/api/datasets';
import { DatasetCard } from '@/components/datasets/DatasetCard';
import { ErrorState } from '@/components/errors/ErrorState';
import { Button } from '@/components/ui/Button';
import { CardSkeleton } from '@/components/ui/Skeleton';
import { formatNumber } from '@/lib/format';

const PAGE_SIZE = 20;

/** Popular search seeds shown below the hero search. Click = run search. */
const POPULAR_SEARCHES = [
  'Mus musculus',
  'V1 recordings',
  'Orientation tuning',
  'Chronic probe',
  'Van Hooser Lab',
] as const;

/**
 * Public Data Commons — catalog grid and landing page for app.ndi-cloud.com.
 *
 * Layout:
 *   1. Depth-gradient hero band with eyebrow, h1, prominent search, popular
 *      seed chips, and an at-a-glance stat strip. Full-bleed; pattern
 *      overlay from the shared NDI brandmark.
 *   2. Body section (max-w 1200px) with a results-info bar (count + filter
 *      hint) above a responsive card grid.
 *   3. Pagination nav.
 *
 * Preserves everything the v2 version did:
 *   - `usePublishedDatasets(page, PAGE_SIZE)` React Query fetch
 *   - URL state for `?q=` and `?page=`
 *   - Client-side text filter (`visible` memo) — deep search is M6/M7
 *   - Accessible loading, empty, and error states
 *
 * Design bar mirrored from `search.html` (depth gradient + eyebrow + stats
 * strip + large centered search) and the marketing homepage.
 */
export function DatasetsPage() {
  const [searchParams, setSearchParams] = useSearchParams();
  const page = Math.max(1, parseInt(searchParams.get('page') ?? '1', 10) || 1);
  const q = searchParams.get('q') ?? '';

  // Local input state so the hero form only commits on submit (matches the
  // mockup's "type then press Search" UX). DatasetSearch in older versions
  // committed on every keystroke; we keep that for the in-grid filter only.
  const [draftQ, setDraftQ] = useState(q);

  const setQ = (v: string) => {
    const next = new URLSearchParams(searchParams);
    if (!v) next.delete('q');
    else next.set('q', v);
    next.delete('page');
    setSearchParams(next, { replace: true });
  };

  const setPage = (n: number) => {
    const next = new URLSearchParams(searchParams);
    if (n <= 1) next.delete('page');
    else next.set('page', String(n));
    setSearchParams(next, { replace: false });
  };

  const handleSubmit = (e: FormEvent) => {
    e.preventDefault();
    setQ(draftQ.trim());
  };

  const handlePopular = (term: string) => {
    setDraftQ(term);
    setQ(term);
  };

  const { data, isLoading, isError, error, refetch } = usePublishedDatasets(
    page,
    PAGE_SIZE,
  );

  const visible = useMemo(() => {
    const all = data?.datasets ?? [];
    if (!q.trim()) return all;
    const needle = q.toLowerCase();
    return all.filter((d) =>
      [
        d.name,
        d.abstract,
        d.description,
        d.doi,
        d.pubMedId,
        ...(d.contributors?.map(
          (c) => `${c.firstName ?? ''} ${c.lastName ?? ''}`,
        ) ?? []),
      ]
        .filter(Boolean)
        .some((x) => String(x).toLowerCase().includes(needle)),
    );
  }, [data, q]);

  const total = data?.totalNumber ?? 0;
  const pageCount = total > 0 ? Math.ceil(total / PAGE_SIZE) : 1;

  return (
    <>
      {/* ── Hero band (full bleed) ──────────────────────────────────── */}
      <section
        className="relative overflow-hidden text-white"
        style={{ background: 'var(--grad-depth)' }}
        aria-labelledby="datasets-hero"
      >
        {/* Pattern overlay — NDI brandmark at 5% opacity */}
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

        <div className="relative mx-auto max-w-[1200px] px-7 py-14 md:py-16">
          {/* Eyebrow */}
          <div className="eyebrow mb-4">
            <span className="eyebrow-dot" aria-hidden />
            NDI Data Commons · Open access
          </div>

          {/* H1 */}
          <h1
            id="datasets-hero"
            className="text-white font-display font-extrabold tracking-tight leading-[1.1] text-[2.25rem] md:text-[2.75rem] mb-3 max-w-3xl"
          >
            Discover published neuroscience datasets.
          </h1>

          <p className="text-white/70 text-[15px] leading-relaxed max-w-[640px] mb-6">
            Faceted search across every dataset on NDI Cloud. Filter by species,
            region, probe, year &mdash; every entry carries a Crossref DOI.
          </p>

          {/* Glassmorphic search box */}
          <form
            onSubmit={handleSubmit}
            className="flex gap-1.5 p-1.5 rounded-xl border border-white/18 backdrop-blur-md max-w-[720px]"
            style={{ background: 'rgba(255,255,255,0.08)' }}
            role="search"
          >
            <label htmlFor="hero-search" className="sr-only">
              Search datasets
            </label>
            <div className="flex-1 flex items-center gap-2 px-3">
              <Search className="h-4 w-4 text-white/55" aria-hidden />
              <input
                id="hero-search"
                type="search"
                value={draftQ}
                onChange={(e) => setDraftQ(e.target.value)}
                placeholder="Search species, region, probe, contributor, DOI…"
                className="flex-1 bg-transparent border-none outline-none text-white text-[15px] placeholder:text-white/50 py-2.5"
                autoComplete="off"
              />
            </div>
            <button
              type="submit"
              className="rounded-lg bg-ndi-teal px-5 py-2.5 text-[14px] font-semibold text-white hover:brightness-110 focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-brand-blue-3 transition-all"
              style={{ boxShadow: 'var(--shadow-cta)' }}
            >
              Search
            </button>
          </form>

          {/* Popular tags */}
          <div className="mt-4 flex flex-wrap gap-2 items-center text-[12.5px]">
            <span className="text-white/55 font-semibold tracking-wide">
              Popular:
            </span>
            {POPULAR_SEARCHES.map((term) => (
              <button
                key={term}
                type="button"
                onClick={() => handlePopular(term)}
                className="px-3 py-1 rounded-full text-white/85 hover:text-white hover:bg-white/15 transition-colors"
                style={{ background: 'rgba(255,255,255,0.08)' }}
              >
                {term}
              </button>
            ))}
          </div>

          {/* Stat strip */}
          <div className="mt-6 pt-5 border-t border-white/10 flex flex-wrap gap-x-10 gap-y-3 text-[11.5px] text-white/55">
            <Stat label="Published datasets" value={formatNumber(total)} />
            <Stat label="DOI coverage" value="Crossref" />
            <Stat label="Metadata standard" value="OpenMINDS" />
            <Stat label="Access" value="No login required" />
          </div>
        </div>
      </section>

      {/* ── Body ─────────────────────────────────────────────────────── */}
      <section className="mx-auto max-w-[1200px] px-7 py-8">
        {/* Results info bar */}
        <div
          className="flex flex-wrap items-center justify-between gap-3 rounded-md bg-white border border-border-subtle px-4 py-2.5 mb-4"
          style={{ boxShadow: 'var(--shadow-xs)' }}
        >
          <span className="text-[13.5px] text-fg-secondary">
            {isLoading ? (
              'Loading…'
            ) : q ? (
              <>
                <strong className="text-brand-navy font-semibold">
                  {visible.length}
                </strong>{' '}
                of {formatNumber(total)} dataset{total === 1 ? '' : 's'} matching{' '}
                <em className="text-brand-navy not-italic font-medium">
                  &ldquo;{q}&rdquo;
                </em>
              </>
            ) : (
              <>
                <strong className="text-brand-navy font-semibold">
                  {formatNumber(total)}
                </strong>{' '}
                datasets &middot; page {page} of {pageCount}
              </>
            )}
          </span>
          {q && (
            <button
              type="button"
              onClick={() => {
                setDraftQ('');
                setQ('');
              }}
              className="text-[12.5px] text-fg-muted hover:text-fg-secondary underline underline-offset-2"
            >
              Clear search
            </button>
          )}
        </div>

        {/* Loading */}
        {isLoading && (
          <div className="grid gap-3 sm:grid-cols-2 xl:grid-cols-3">
            {Array.from({ length: 6 }).map((_, i) => (
              <CardSkeleton key={i} />
            ))}
          </div>
        )}

        {/* Error */}
        {isError && <ErrorState error={error} onRetry={() => refetch()} />}

        {/* Empty */}
        {!isLoading && !isError && visible.length === 0 && (
          <div className="rounded-lg border border-dashed border-border-subtle bg-white p-10 text-center">
            <p className="text-[14px] text-fg-secondary">
              {q
                ? `No datasets match “${q}” on page ${page}.`
                : 'No published datasets yet.'}
            </p>
            {q && (
              <Button
                variant="ghost"
                size="sm"
                className="mt-3"
                onClick={() => {
                  setDraftQ('');
                  setQ('');
                }}
              >
                Clear filter
              </Button>
            )}
          </div>
        )}

        {/* Cards */}
        {!isLoading && visible.length > 0 && (
          <div className="grid gap-3 sm:grid-cols-2 xl:grid-cols-3">
            {visible.map((d) => (
              <DatasetCard key={d.id} dataset={d} />
            ))}
          </div>
        )}

        {/* Pagination */}
        {!isLoading && pageCount > 1 && (
          <nav
            className="flex items-center justify-center gap-3 pt-8"
            aria-label="Pagination"
          >
            <Button
              variant="secondary"
              size="sm"
              disabled={page === 1}
              onClick={() => setPage(page - 1)}
            >
              Previous
            </Button>
            <span className="text-[13px] text-fg-muted font-mono">
              Page {page} of {pageCount}
            </span>
            <Button
              variant="secondary"
              size="sm"
              disabled={!data || page >= pageCount}
              onClick={() => setPage(page + 1)}
            >
              Next
            </Button>
          </nav>
        )}
      </section>
    </>
  );
}

function Stat({ label, value }: { label: string; value: string | number }) {
  return (
    <div className="flex flex-col">
      <strong className="text-white font-display font-bold text-[17px] tracking-tight leading-none mb-1">
        {value}
      </strong>
      <span className="uppercase tracking-wider">{label}</span>
    </div>
  );
}
