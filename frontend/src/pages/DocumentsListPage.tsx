import { useState } from 'react';
import { useParams, useSearchParams, Link } from 'react-router-dom';
import { useDocuments } from '@/api/documents';
import { TableSkeleton } from '@/components/ui/Skeleton';
import { ErrorState } from '@/components/errors/ErrorState';
import { Card, CardBody } from '@/components/ui/Card';
import { Button } from '@/components/ui/Button';
import { formatNumber } from '@/lib/format';

export function DocumentsListPage() {
  const { id } = useParams();
  const [search, setSearch] = useSearchParams();
  const cls = search.get('class');
  const [page, setPage] = useState(1);
  const pageSize = 50;
  const { data, isLoading, isError, error, refetch } = useDocuments(id, cls, page, pageSize);

  return (
    <Card>
      <CardBody>
        <div className="flex flex-wrap items-center justify-between gap-2 mb-3">
          <h2 className="text-lg font-semibold">
            Documents {cls && <span className="text-sm text-slate-500">({cls})</span>}
          </h2>
          {cls && (
            <Button size="sm" variant="ghost" onClick={() => setSearch({})}>
              Clear class filter
            </Button>
          )}
        </div>
        {isLoading && <TableSkeleton rows={10} />}
        {isError && <ErrorState error={error} onRetry={() => refetch()} />}
        {data && (
          <>
            <p className="mb-2 text-xs text-slate-500">
              {formatNumber(data.total)} total
            </p>
            <div className="overflow-x-auto rounded border border-slate-200 dark:border-slate-800">
              <table className="w-full text-sm">
                <thead className="bg-slate-50 dark:bg-slate-800">
                  <tr>
                    <th className="px-3 py-2 text-left">Name</th>
                    <th className="px-3 py-2 text-left">Class</th>
                    <th className="px-3 py-2 text-left">ID</th>
                  </tr>
                </thead>
                <tbody>
                  {data.documents.map((d) => {
                    const did = d.id ?? d.ndiId ?? '';
                    return (
                      <tr key={did} className="border-t border-slate-100 dark:border-slate-800">
                        <td className="px-3 py-1.5">
                          <Link
                            to={`/datasets/${id}/documents/${did}`}
                            className="text-brand-600 hover:underline"
                          >
                            {d.name ?? '—'}
                          </Link>
                        </td>
                        <td className="px-3 py-1.5 font-mono text-xs">{d.className ?? '—'}</td>
                        <td className="px-3 py-1.5 font-mono text-xs text-slate-500">{did}</td>
                      </tr>
                    );
                  })}
                </tbody>
              </table>
            </div>
            <div className="mt-3 flex items-center justify-center gap-3">
              <Button
                variant="secondary"
                size="sm"
                disabled={page === 1}
                onClick={() => setPage((p) => Math.max(1, p - 1))}
              >
                Previous
              </Button>
              <span className="text-sm text-slate-600">Page {page}</span>
              <Button
                variant="secondary"
                size="sm"
                disabled={page * pageSize >= data.total}
                onClick={() => setPage((p) => p + 1)}
              >
                Next
              </Button>
            </div>
          </>
        )}
      </CardBody>
    </Card>
  );
}
