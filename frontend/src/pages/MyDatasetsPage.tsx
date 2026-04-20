import { useMyDatasets } from '@/api/datasets';
import { useMe } from '@/api/auth';
import { DatasetCard } from '@/components/datasets/DatasetCard';
import { CardSkeleton } from '@/components/ui/Skeleton';
import { ErrorState } from '@/components/errors/ErrorState';
import { formatNumber } from '@/lib/format';

/**
 * Logged-in-user view of the caller's organization's unpublished datasets.
 * Backend route: ``GET /api/datasets/my`` (requires session; returns the
 * same shape as ``/api/datasets/published`` including embedded compact
 * summaries — so we can reuse `DatasetCard` end-to-end).
 *
 * From here a user can drill into any unpublished dataset's detail page
 * (``/datasets/:id``) just like a published one; the backend forwards
 * the Cognito access token from the session so the cloud allows access
 * to datasets the user's organization owns but has not yet published.
 */
export function MyDatasetsPage() {
  const me = useMe();
  const q = useMyDatasets(me.isSuccess);

  if (me.isError) return <ErrorState error={me.error} />;
  if (me.isLoading) return <CardSkeleton />;

  const total = q.data?.totalNumber ?? q.data?.datasets?.length ?? 0;

  return (
    <div className="space-y-5">
      <header className="space-y-2">
        <h1 className="text-2xl font-bold text-gray-900 dark:text-gray-100">
          My organization&apos;s datasets
        </h1>
        <p className="text-sm text-gray-600 dark:text-gray-300">
          Unpublished datasets owned by your organization. Click any card for
          full detail — subjects, probes, epochs, and raw documents — just
          like the public catalog.
        </p>
        {q.data && (
          <p className="text-xs text-gray-500 dark:text-gray-400 font-mono">
            {formatNumber(total)} {total === 1 ? 'dataset' : 'datasets'}
          </p>
        )}
      </header>

      {q.isLoading && (
        <div className="grid gap-3 sm:grid-cols-2 xl:grid-cols-3">
          {Array.from({ length: 4 }).map((_, i) => (
            <CardSkeleton key={i} />
          ))}
        </div>
      )}

      {q.isError && <ErrorState error={q.error} onRetry={() => q.refetch()} />}

      {!q.isLoading && !q.isError && q.data && q.data.datasets.length === 0 && (
        <div className="rounded-lg border border-dashed border-gray-200 dark:border-gray-700 p-10 text-center">
          <p className="text-sm text-gray-600 dark:text-gray-400">
            No unpublished datasets in your organization yet.
          </p>
          <p className="mt-2 text-xs text-gray-500 dark:text-gray-500">
            Datasets you upload via NDI Cloud will appear here before they are
            published.
          </p>
        </div>
      )}

      {!q.isLoading && q.data && q.data.datasets.length > 0 && (
        <div className="grid gap-3 sm:grid-cols-2 xl:grid-cols-3">
          {q.data.datasets.map((d) => (
            <DatasetCard key={d.id} dataset={d} />
          ))}
        </div>
      )}
    </div>
  );
}
