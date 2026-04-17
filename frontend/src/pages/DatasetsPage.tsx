import { useMemo } from 'react';
import { useSearchParams } from 'react-router-dom';

import { usePublishedDatasets } from '@/api/datasets';
import { Button } from '@/components/ui/Button';
import { CardSkeleton } from '@/components/ui/Skeleton';
import { DatasetCard } from '@/components/datasets/DatasetCard';
import { DatasetSearch } from '@/components/datasets/DatasetSearch';
import { ErrorState } from '@/components/errors/ErrorState';
import { formatNumber } from '@/lib/format';

const PAGE_SIZE = 20;

/**
 * Catalog grid — home page for v2. Hero text on top, search input beside
 * the count, responsive grid of DatasetCard below, manual pagination at
 * the bottom. Client-side filter narrows results within the currently
 * loaded page; deep search spans pages via the backend later (M6/M7).
 *
 * URL state: ?q=… for filter, ?page=N for pagination. Makes card
 * positions deep-linkable.
 */
export function DatasetsPage() {
  const [searchParams, setSearchParams] = useSearchParams();
  const page = Math.max(1, parseInt(searchParams.get('page') ?? '1', 10) || 1);
  const q = searchParams.get('q') ?? '';

  const setPage = (n: number) => {
    const next = new URLSearchParams(searchParams);
    if (n <= 1) next.delete('page');
    else next.set('page', String(n));
    setSearchParams(next, { replace: false });
  };
  const setQ = (v: string) => {
    const next = new URLSearchParams(searchParams);
    if (!v) next.delete('q');
    else next.set('q', v);
    // Reset pagination on new search.
    next.delete('page');
    setSearchParams(next, { replace: true });
  };

  const { data, isLoading, isError, error, refetch } = usePublishedDatasets(page, PAGE_SIZE);

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
        ...(d.contributors?.map((c) => `${c.firstName ?? ''} ${c.lastName ?? ''}`) ?? []),
      ]
        .filter(Boolean)
        .some((x) => String(x).toLowerCase().includes(needle)),
    );
  }, [data, q]);

  const total = data?.totalNumber ?? 0;
  const pageCount = total > 0 ? Math.ceil(total / PAGE_SIZE) : 1;

  return (
    <div className="space-y-5">
      <header className="space-y-3">
        <div className="flex flex-wrap items-start justify-between gap-3">
          <div>
            <h1 className="text-2xl font-bold text-slate-900 dark:text-slate-100">
              Published datasets
            </h1>
            <p className="text-sm text-slate-600 dark:text-slate-300">
              Browse the NDI Cloud catalog. Click any card for full detail — subjects, probes,
              epochs, and raw documents.
            </p>
          </div>
          <div className="w-full max-w-sm">
            <DatasetSearch
              value={q}
              onChange={setQ}
              placeholder="Search name, abstract, contributor…"
            />
          </div>
        </div>
        <p className="text-xs text-slate-500 dark:text-slate-400 font-mono">
          {q ? `${visible.length} of ${formatNumber(total)}` : `${formatNumber(total)} total`}
        </p>
      </header>

      {isLoading && (
        <div className="grid gap-3 sm:grid-cols-2 xl:grid-cols-3">
          {Array.from({ length: 6 }).map((_, i) => (
            <CardSkeleton key={i} />
          ))}
        </div>
      )}

      {isError && <ErrorState error={error} onRetry={() => refetch()} />}

      {!isLoading && !isError && visible.length === 0 && (
        <div className="rounded-lg border border-dashed border-slate-200 dark:border-slate-700 p-10 text-center">
          <p className="text-sm text-slate-600 dark:text-slate-400">
            No datasets match your search.
          </p>
          {q && (
            <Button
              variant="ghost"
              size="sm"
              className="mt-2"
              onClick={() => setQ('')}
            >
              Clear filter
            </Button>
          )}
        </div>
      )}

      {!isLoading && visible.length > 0 && (
        <div className="grid gap-3 sm:grid-cols-2 xl:grid-cols-3">
          {visible.map((d) => (
            <DatasetCard key={d.id} dataset={d} />
          ))}
        </div>
      )}

      <nav
        className="flex items-center justify-center gap-3 pt-2"
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
        <span className="text-sm text-slate-600 dark:text-slate-300 font-mono">
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
    </div>
  );
}
