import { useMemo } from 'react';
import { Link, useParams, useSearchParams } from 'react-router-dom';
import { LayoutGrid, List } from 'lucide-react';

import { useClassCounts } from '@/api/datasets';
import { useDocuments } from '@/api/documents';
import { Button } from '@/components/ui/Button';
import { Card, CardBody, CardHeader, CardTitle } from '@/components/ui/Card';
import {
  DocumentTypeSelector,
  type DocTypeCount,
} from '@/components/documents/DocumentTypeSelector';
import { ErrorState } from '@/components/errors/ErrorState';
import { TableSkeleton } from '@/components/ui/Skeleton';
import { TableTab } from './TableTab';
import { cn } from '@/lib/cn';
import { formatNumber } from '@/lib/format';

type Mode = 'summary' | 'raw';

const PAGE_SIZE = 50;

/**
 * Top-level toggle between Summary Tables (rich projection) and Raw
 * Documents (class-filtered paginated list). Plan §M4c.
 *
 * Right-pane:
 * - Summary Tables → embedded TableTab (already owns the TableSelector).
 * - Raw Documents  → DocumentTypeSelector on the left + paginated list
 *   on the right. Row-click navigates to the M5 document detail page.
 *
 * URL state: `?mode=raw&class=<cls>&page=<n>` for deep-linkability.
 */
export function DocumentExplorerPage() {
  const { id } = useParams();
  const [searchParams, setSearchParams] = useSearchParams();
  const mode: Mode = searchParams.get('mode') === 'raw' ? 'raw' : 'summary';

  const setMode = (next: Mode) => {
    const params = new URLSearchParams(searchParams);
    if (next === 'summary') params.delete('mode');
    else params.set('mode', 'raw');
    setSearchParams(params, { replace: true });
  };

  if (!id) {
    return <p className="text-sm text-slate-500">Missing dataset id.</p>;
  }

  return (
    <div className="space-y-3">
      <div className="flex items-center justify-end">
        <ModeToggle value={mode} onChange={setMode} />
      </div>
      {mode === 'summary' ? (
        <TableTab />
      ) : (
        <RawDocumentsPane datasetId={id} searchParams={searchParams} setSearchParams={setSearchParams} />
      )}
    </div>
  );
}

function ModeToggle({
  value,
  onChange,
}: {
  value: Mode;
  onChange: (next: Mode) => void;
}) {
  return (
    <div
      role="group"
      aria-label="View mode"
      className="flex items-center rounded-md border border-slate-200 dark:border-slate-700 overflow-hidden"
    >
      <button
        type="button"
        onClick={() => onChange('summary')}
        aria-pressed={value === 'summary'}
        className={cn(
          'flex items-center gap-1.5 px-3 py-1.5 text-xs font-medium transition-colors',
          value === 'summary'
            ? 'bg-slate-900 text-white dark:bg-white dark:text-slate-900'
            : 'text-slate-600 dark:text-slate-400 hover:text-slate-900 dark:hover:text-slate-100',
        )}
      >
        <LayoutGrid className="h-3.5 w-3.5" />
        Summary Tables
      </button>
      <button
        type="button"
        onClick={() => onChange('raw')}
        aria-pressed={value === 'raw'}
        className={cn(
          'flex items-center gap-1.5 px-3 py-1.5 text-xs font-medium transition-colors',
          value === 'raw'
            ? 'bg-slate-900 text-white dark:bg-white dark:text-slate-900'
            : 'text-slate-600 dark:text-slate-400 hover:text-slate-900 dark:hover:text-slate-100',
        )}
      >
        <List className="h-3.5 w-3.5" />
        Raw Documents
      </button>
    </div>
  );
}

function RawDocumentsPane({
  datasetId,
  searchParams,
  setSearchParams,
}: {
  datasetId: string;
  searchParams: URLSearchParams;
  setSearchParams: (next: URLSearchParams) => void;
}) {
  const cls = searchParams.get('class');
  const page = Math.max(1, parseInt(searchParams.get('page') ?? '1', 10) || 1);

  const setClass = (next: string | null) => {
    const params = new URLSearchParams(searchParams);
    if (next) params.set('class', next);
    else params.delete('class');
    params.delete('page');
    setSearchParams(params);
  };

  const setPage = (next: number) => {
    const params = new URLSearchParams(searchParams);
    if (next <= 1) params.delete('page');
    else params.set('page', String(next));
    setSearchParams(params);
  };

  const counts = useClassCounts(datasetId);
  const docs = useDocuments(datasetId, cls, page, PAGE_SIZE);

  const classList: DocTypeCount[] = useMemo(() => {
    const raw = counts.data?.classCounts ?? {};
    return Object.entries(raw)
      .map(([className, count]) => ({ className, count }))
      .sort((a, b) => b.count - a.count);
  }, [counts.data]);

  return (
    <div className="grid gap-4 lg:grid-cols-[220px_1fr]">
      <aside>
        <Card>
          <CardHeader className="py-3">
            <CardTitle className="text-xs">Document classes</CardTitle>
          </CardHeader>
          <CardBody className="pt-0">
            {counts.isLoading ? (
              <p className="text-xs text-slate-500">Loading…</p>
            ) : counts.isError ? (
              <ErrorState error={counts.error} onRetry={() => counts.refetch()} />
            ) : (
              <DocumentTypeSelector
                types={classList}
                selected={cls}
                onSelect={setClass}
                total={counts.data?.totalDocuments ?? 0}
              />
            )}
          </CardBody>
        </Card>
      </aside>

      <section>
        <Card>
          <CardBody>
            <div className="flex flex-wrap items-center justify-between gap-2 mb-3">
              <h2 className="text-sm font-semibold">
                {cls ? (
                  <span>
                    Documents · <span className="font-mono text-brand-600 dark:text-brand-400">{cls}</span>
                  </span>
                ) : (
                  'All documents'
                )}
              </h2>
              {cls && (
                <Button size="sm" variant="ghost" onClick={() => setClass(null)}>
                  Clear class filter
                </Button>
              )}
            </div>

            {docs.isLoading && <TableSkeleton rows={10} />}
            {docs.isError && <ErrorState error={docs.error} onRetry={() => docs.refetch()} />}
            {docs.data && (
              <>
                <p className="mb-2 text-xs text-slate-500 dark:text-slate-400 font-mono">
                  {formatNumber(docs.data.total)} total · page {page}
                </p>
                <div className="overflow-x-auto rounded border border-slate-200 dark:border-slate-700">
                  <table className="w-full text-sm">
                    <thead className="bg-slate-50 dark:bg-slate-900 sticky top-0">
                      <tr>
                        <th className="px-3 py-2 text-left font-medium text-slate-500 dark:text-slate-300">
                          Name
                        </th>
                        <th className="px-3 py-2 text-left font-medium text-slate-500 dark:text-slate-300">
                          Class
                        </th>
                        <th className="px-3 py-2 text-left font-medium text-slate-500 dark:text-slate-300">
                          Mongo ID
                        </th>
                        <th className="px-3 py-2 text-left font-medium text-slate-500 dark:text-slate-300">
                          ndiId
                        </th>
                      </tr>
                    </thead>
                    <tbody>
                      {docs.data.documents.length === 0 ? (
                        <tr>
                          <td colSpan={4} className="px-3 py-8 text-center text-slate-500 dark:text-slate-400">
                            No documents for this class.
                          </td>
                        </tr>
                      ) : (
                        docs.data.documents.map((d) => {
                          const did = d.id ?? d.ndiId ?? '';
                          return (
                            <tr
                              key={did}
                              className="border-t border-slate-100 dark:border-slate-800 hover:bg-slate-50 dark:hover:bg-slate-800/40"
                            >
                              <td className="px-3 py-1.5">
                                <Link
                                  to={`/datasets/${datasetId}/documents/${did}`}
                                  className="text-brand-600 dark:text-brand-400 hover:underline"
                                >
                                  {d.name || <span className="text-slate-400">—</span>}
                                </Link>
                              </td>
                              <td className="px-3 py-1.5 font-mono text-xs">
                                {d.className || '—'}
                              </td>
                              <td className="px-3 py-1.5 font-mono text-xs text-slate-500 dark:text-slate-400">
                                {d.id || ''}
                              </td>
                              <td className="px-3 py-1.5 font-mono text-xs text-slate-500 dark:text-slate-400 truncate max-w-[220px]">
                                {d.ndiId || ''}
                              </td>
                            </tr>
                          );
                        })
                      )}
                    </tbody>
                  </table>
                </div>

                <nav
                  className="mt-3 flex items-center justify-center gap-3"
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
                  <span className="text-sm text-slate-500 font-mono">Page {page}</span>
                  <Button
                    variant="secondary"
                    size="sm"
                    disabled={page * PAGE_SIZE >= docs.data.total}
                    onClick={() => setPage(page + 1)}
                  >
                    Next
                  </Button>
                </nav>
              </>
            )}
          </CardBody>
        </Card>
      </section>
    </div>
  );
}
