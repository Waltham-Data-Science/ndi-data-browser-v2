import { Link } from 'react-router-dom';
import { useMyDatasets } from '@/api/datasets';
import { useMe } from '@/api/auth';
import { CardSkeleton } from '@/components/ui/Skeleton';
import { ErrorState } from '@/components/errors/ErrorState';
import { Card, CardBody } from '@/components/ui/Card';

export function MyDatasetsPage() {
  const me = useMe();
  const q = useMyDatasets(me.isSuccess);

  if (me.isError) {
    return <ErrorState error={me.error} />;
  }
  if (me.isLoading) return <CardSkeleton />;

  return (
    <div className="space-y-3">
      <h1 className="text-2xl font-bold">My organization&apos;s datasets</h1>
      {q.isLoading && <CardSkeleton />}
      {q.isError && <ErrorState error={q.error} onRetry={() => q.refetch()} />}
      {q.data && q.data.datasets.length === 0 && (
        <p className="text-slate-600 dark:text-slate-400">No private datasets in your organization yet.</p>
      )}
      <div className="grid gap-3 sm:grid-cols-2">
        {q.data?.datasets.map((d) => (
          <Link key={d.id} to={`/datasets/${d.id}`}>
            <Card className="h-full hover:ring-brand-500">
              <CardBody>
                <h3 className="font-semibold">{d.name}</h3>
                {(d.description ?? d.abstract) && (
                  <p className="text-sm text-slate-600 dark:text-slate-300 line-clamp-3">{d.description ?? d.abstract}</p>
                )}
              </CardBody>
            </Card>
          </Link>
        ))}
      </div>
    </div>
  );
}
