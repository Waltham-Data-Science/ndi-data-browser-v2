import { useParams, Link, Outlet, useLocation, Navigate } from 'react-router-dom';
import { useDataset, useClassCounts } from '@/api/datasets';
import { CardSkeleton, Skeleton } from '@/components/ui/Skeleton';
import { ErrorState } from '@/components/errors/ErrorState';
import { Card, CardBody, CardHeader } from '@/components/ui/Card';
import { formatNumber, formatDate } from '@/lib/format';
import { cn } from '@/lib/cn';

const TABLES = [
  { slug: 'subject', label: 'Subjects' },
  { slug: 'element', label: 'Elements' },
  { slug: 'element_epoch', label: 'Epochs' },
  { slug: 'combined', label: 'Combined' },
  { slug: 'treatment', label: 'Treatments' },
];

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
        <TableTabs datasetId={id} />
        <Outlet />
      </section>
    </div>
  );
}

function TableTabs({ datasetId }: { datasetId: string }) {
  const loc = useLocation();
  const activeSlug = loc.pathname.split('/').pop();
  return (
    <nav className="flex gap-1 border-b border-slate-200 dark:border-slate-800" aria-label="Tables">
      {TABLES.map((t) => (
        <Link
          key={t.slug}
          to={`/datasets/${datasetId}/tables/${t.slug}`}
          className={cn(
            'px-3 py-2 text-sm font-medium border-b-2 transition-colors',
            activeSlug === t.slug
              ? 'border-brand-500 text-brand-600'
              : 'border-transparent text-slate-600 hover:text-slate-900 dark:text-slate-400 dark:hover:text-slate-100',
          )}
        >
          {t.label}
        </Link>
      ))}
      <Link
        to={`/datasets/${datasetId}/documents`}
        className={cn(
          'px-3 py-2 text-sm font-medium border-b-2 transition-colors ml-auto',
          activeSlug === 'documents' || loc.pathname.includes('/documents')
            ? 'border-brand-500 text-brand-600'
            : 'border-transparent text-slate-600 hover:text-slate-900 dark:text-slate-400 dark:hover:text-slate-100',
        )}
      >
        All documents
      </Link>
    </nav>
  );
}
