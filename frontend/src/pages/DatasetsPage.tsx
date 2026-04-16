import { useMemo, useState } from 'react';
import { Link } from 'react-router-dom';
import { usePublishedDatasets, type DatasetSummary } from '@/api/datasets';
import { Input } from '@/components/ui/Input';
import { Button } from '@/components/ui/Button';
import { CardSkeleton } from '@/components/ui/Skeleton';
import { ErrorState } from '@/components/errors/ErrorState';
import { Card, CardBody } from '@/components/ui/Card';
import { truncate } from '@/lib/format';

export function DatasetsPage() {
  const [page, setPage] = useState(1);
  const pageSize = 20;
  const { data, isLoading, isError, error, refetch } = usePublishedDatasets(page, pageSize);
  const [q, setQ] = useState('');

  const visible = useMemo(() => {
    const all = data?.datasets ?? [];
    if (!q.trim()) return all;
    const needle = q.toLowerCase();
    return all.filter((d) =>
      [d.name, d.description ?? d.abstract, ...(d.contributors?.map((c) => `${c.firstName ?? ''} ${c.lastName ?? ''}`) ?? [])]
        .filter(Boolean)
        .some((x) => String(x).toLowerCase().includes(needle)),
    );
  }, [data, q]);

  return (
    <div className="space-y-4">
      <header className="flex flex-wrap items-end justify-between gap-3">
        <div>
          <h1 className="text-2xl font-bold text-slate-900 dark:text-slate-100">Published datasets</h1>
          <p className="text-sm text-slate-600 dark:text-slate-300">
            {data?.totalNumber ?? '—'} total
          </p>
        </div>
        <div className="w-full max-w-sm">
          <label className="sr-only" htmlFor="search">Search datasets</label>
          <Input
            id="search"
            placeholder="Search by name, description, contributor…"
            value={q}
            onChange={(e) => setQ(e.target.value)}
          />
        </div>
      </header>

      {isLoading && (
        <div className="grid gap-3 sm:grid-cols-2">
          {Array.from({ length: 6 }).map((_, i) => <CardSkeleton key={i} />)}
        </div>
      )}
      {isError && <ErrorState error={error} onRetry={() => refetch()} />}
      {!isLoading && !isError && visible.length === 0 && (
        <p className="text-sm text-slate-600 dark:text-slate-400">No datasets match your search.</p>
      )}

      <div className="grid gap-3 sm:grid-cols-2">
        {visible.map((d) => <DatasetCard key={d.id} d={d} />)}
      </div>

      <div className="flex items-center justify-center gap-3">
        <Button variant="secondary" size="sm" disabled={page === 1} onClick={() => setPage((p) => Math.max(1, p - 1))}>
          Previous
        </Button>
        <span className="text-sm text-slate-600 dark:text-slate-300">Page {page}</span>
        <Button
          variant="secondary"
          size="sm"
          disabled={!data || page * pageSize >= data.totalNumber}
          onClick={() => setPage((p) => p + 1)}
        >
          Next
        </Button>
      </div>
    </div>
  );
}

function DatasetCard({ d }: { d: DatasetSummary }) {
  const authors =
    d.contributors
      ?.map((c) => [c.firstName, c.lastName].filter(Boolean).join(' '))
      .filter(Boolean)
      .slice(0, 3)
      .join(', ') ?? '';
  const desc = d.description ?? d.abstract;
  return (
    <Link to={`/datasets/${d.id}`}>
      <Card className="h-full hover:ring-brand-500 transition-shadow hover:shadow-md">
        <CardBody className="space-y-2">
          <h3 className="font-semibold text-slate-900 dark:text-slate-100">{d.name}</h3>
          {desc && (
            <p className="text-sm text-slate-600 dark:text-slate-300">{truncate(desc, 180)}</p>
          )}
          {authors && <p className="text-xs text-slate-500 dark:text-slate-400">{authors}</p>}
          {d.documentCount != null && (
            <p className="text-xs text-slate-500">{d.documentCount.toLocaleString()} documents</p>
          )}
        </CardBody>
      </Card>
    </Link>
  );
}
