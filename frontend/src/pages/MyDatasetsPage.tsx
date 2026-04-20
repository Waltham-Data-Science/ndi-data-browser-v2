import { useMyDatasets } from '@/api/datasets';
import { useMe } from '@/api/auth';
import { DatasetCard } from '@/components/datasets/DatasetCard';
import { Badge } from '@/components/ui/Badge';
import { CardSkeleton } from '@/components/ui/Skeleton';
import { ErrorState } from '@/components/errors/ErrorState';
import { formatNumber } from '@/lib/format';

/**
 * Logged-in-user view of every dataset owned by the caller's org(s).
 *
 * Backend route: ``GET /api/datasets/my`` aggregates cloud's
 * ``/organizations/:orgId/datasets`` across every org on
 * ``session.organization_ids``. Returns the same shape as
 * ``/api/datasets/published`` (including embedded compact summaries),
 * so `DatasetCard` renders published / in-review / draft states
 * uniformly — the per-row ``publishStatus`` badge distinguishes them.
 *
 * From here the user can drill into any row's detail page
 * (``/datasets/:id``); the backend forwards the Cognito access token
 * from the session so the cloud's permission filter allows access to
 * unpublished / draft datasets the caller's org owns. Pre-2026-04-20
 * this surface only showed the narrow ``isPublished=false AND
 * isSubmitted=true`` slice — see CLAUDE.md "My Org datasets".
 */
export function MyDatasetsPage() {
  const me = useMe();
  const q = useMyDatasets(me.isSuccess);

  if (me.isError) return <ErrorState error={me.error} />;
  if (me.isLoading) return <CardSkeleton />;

  const total = q.data?.totalNumber ?? q.data?.datasets?.length ?? 0;
  const orgCount = me.data?.organizationIds?.length ?? 0;
  const isAdmin = me.data?.isAdmin ?? false;

  return (
    <div className="space-y-5">
      <header className="space-y-2">
        <div className="flex flex-wrap items-center gap-2">
          <h1 className="text-2xl font-bold text-gray-900 dark:text-gray-100">
            My organization&apos;s datasets
          </h1>
          {isAdmin && <Badge variant="secondary">admin</Badge>}
        </div>
        <p className="text-sm text-gray-600 dark:text-gray-300">
          Every dataset owned by your organization — published, in-review,
          and drafts. Click any card for full detail (subjects, probes,
          epochs, raw documents) just like the public catalog.
        </p>
        {q.data && (
          <p className="text-xs text-gray-500 dark:text-gray-400 font-mono">
            {formatNumber(total)} {total === 1 ? 'dataset' : 'datasets'}
            {orgCount > 1 ? ` · ${orgCount} organizations` : ''}
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
