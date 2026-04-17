import { Link, Navigate, Outlet, useParams } from 'react-router-dom';
import { useClassCounts, useDataset } from '@/api/datasets';
import { CardSkeleton, Skeleton } from '@/components/ui/Skeleton';
import { ErrorState } from '@/components/errors/ErrorState';
import { Card, CardBody, CardHeader } from '@/components/ui/Card';
import { formatDate, formatNumber } from '@/lib/format';

export function DatasetDetailPage() {
  const { id } = useParams();
  const ds = useDataset(id ?? '');
  const cc = useClassCounts(id ?? '');

  if (!id) return <Navigate to="/datasets" replace />;

  return (
    <div className="grid gap-4 lg:grid-cols-[320px_1fr]">
      <aside className="space-y-3">
        {ds.isLoading && <CardSkeleton />}
        {ds.isError && <ErrorState error={ds.error} onRetry={() => ds.refetch()} />}
        {ds.data && (
          <Card>
            <CardHeader>
              <h1 className="text-lg font-bold text-slate-900 dark:text-slate-100">{ds.data.name}</h1>
              {ds.data.organizationId && <p className="text-xs text-slate-500">{ds.data.organizationId}</p>}
            </CardHeader>
            <CardBody className="space-y-3 text-sm">
              {(ds.data.description ?? ds.data.abstract) && (
                <p className="text-slate-700 dark:text-slate-300">{ds.data.description ?? ds.data.abstract}</p>
              )}
              <dl className="grid grid-cols-[auto_1fr] gap-x-3 gap-y-1 text-xs">
                {ds.data.contributors && ds.data.contributors.length > 0 && (
                  <>
                    <dt className="font-medium text-slate-500">Contributors</dt>
                    <dd className="text-slate-700 dark:text-slate-300">
                      {ds.data.contributors
                        .map((c) => [c.firstName, c.lastName].filter(Boolean).join(' '))
                        .filter(Boolean)
                        .join(', ')}
                    </dd>
                  </>
                )}
                {ds.data.funding && ds.data.funding.length > 0 && (
                  <>
                    <dt className="font-medium text-slate-500">Funding</dt>
                    <dd className="text-slate-700 dark:text-slate-300">
                      {ds.data.funding.map((f) => f.source).filter(Boolean).join('; ')}
                    </dd>
                  </>
                )}
                {ds.data.pubMedId && (
                  <>
                    <dt className="font-medium text-slate-500">PubMed</dt>
                    <dd className="text-slate-700 dark:text-slate-300">{ds.data.pubMedId}</dd>
                  </>
                )}
                <dt className="font-medium text-slate-500">Created</dt>
                <dd className="text-slate-700 dark:text-slate-300">{formatDate(ds.data.createdAt)}</dd>
                <dt className="font-medium text-slate-500">Updated</dt>
                <dd className="text-slate-700 dark:text-slate-300">{formatDate(ds.data.updatedAt)}</dd>
              </dl>
            </CardBody>
          </Card>
        )}

        <Card>
          <CardHeader>
            <h2 className="text-sm font-semibold">Document classes</h2>
          </CardHeader>
          <CardBody>
            {cc.isLoading && <Skeleton className="h-32 w-full" />}
            {cc.isError && <ErrorState error={cc.error} onRetry={() => cc.refetch()} />}
            {cc.data && (
              <>
                <p className="mb-2 text-xs text-slate-500">
                  {formatNumber(cc.data.totalDocuments)} documents total
                </p>
                <ul className="space-y-1">
                  {Object.entries(cc.data.classCounts)
                    .sort((a, b) => b[1] - a[1])
                    .slice(0, 25)
                    .map(([cls, n]) => {
                      const pct = (n / Math.max(1, cc.data!.totalDocuments)) * 100;
                      return (
                        <li key={cls} className="text-xs">
                          <Link
                            to={`documents?class=${encodeURIComponent(cls)}`}
                            className="flex items-center gap-2 hover:underline"
                          >
                            <div className="flex-1 flex items-center gap-2">
                              <span className="font-mono truncate">{cls}</span>
                              <span className="ml-auto text-slate-500">{formatNumber(n)}</span>
                            </div>
                          </Link>
                          <div className="h-1 rounded bg-slate-200 dark:bg-slate-800">
                            <div className="h-1 rounded bg-brand-500" style={{ width: `${Math.max(2, pct)}%` }} />
                          </div>
                        </li>
                      );
                    })}
                </ul>
              </>
            )}
          </CardBody>
        </Card>
      </aside>

      <section className="space-y-3">
        <Outlet />
      </section>
    </div>
  );
}
