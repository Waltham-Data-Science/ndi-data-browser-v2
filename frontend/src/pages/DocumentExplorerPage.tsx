import { Link, useParams, useSearchParams } from 'react-router-dom';

import { useClassCounts } from '@/api/datasets';
import { useDocuments } from '@/api/documents';
import { Button } from '@/components/ui/Button';
import {
  Card,
  CardBody,
  CardDescription,
  CardHeader,
  CardTitle,
} from '@/components/ui/Card';
import { ErrorState } from '@/components/errors/ErrorState';
import { Skeleton, TableSkeleton } from '@/components/ui/Skeleton';
import { formatNumber } from '@/lib/format';

import { ClassCountsList } from './DatasetDetailPage';

const PAGE_SIZE = 50;

/**
 * Document Explorer tab content. Renders inside DatasetDetailPage's
 * outlet, so the hero band + tab bar come from the parent. Shows a
 * class filter sidebar + paginated raw-document list at full page
 * width. URL state: `?class=<cls>&page=<n>` for deep-linkability.
 *
 * The sidebar reuses the Overview tab's `ClassCountsList` (progress-
 * bar-style) so the two surfaces stay visually consistent — click a
 * summary class (subject/element/epoch) jumps to the Tables tab; click
 * any other class updates the `?class=` query on this tab and filters
 * the list below.
 */
export function DocumentExplorerPage() {
  const { id } = useParams();
  const [searchParams, setSearchParams] = useSearchParams();

  if (!id) {
    return <p className="text-sm text-fg-muted">Missing dataset id.</p>;
  }

  return (
    <RawDocumentsPane
      datasetId={id}
      searchParams={searchParams}
      setSearchParams={setSearchParams}
    />
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

  const clearClass = () => {
    const params = new URLSearchParams(searchParams);
    params.delete('class');
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

  // `min-w-0` on the grid children so the 1fr track can shrink below its
  // min-content and the inner table's `overflow-x-auto` wrapper actually
  // scrolls horizontally instead of forcing the whole page wider. Matches
  // the fix in DatasetDetailPage.
  return (
    <div className="grid gap-4 lg:grid-cols-[260px_1fr]">
      <aside className="min-w-0">
        <Card>
          <CardHeader>
            <CardTitle as="h3" className="text-sm">
              Document classes
            </CardTitle>
            <CardDescription>
              Click any class to filter below or jump to the summary tables.
            </CardDescription>
          </CardHeader>
          <CardBody>
            {counts.isLoading && <Skeleton className="h-32 w-full" />}
            {counts.isError && (
              <ErrorState error={counts.error} onRetry={() => counts.refetch()} />
            )}
            {counts.data && (
              <ClassCountsList datasetId={datasetId} data={counts.data} />
            )}
          </CardBody>
        </Card>
      </aside>

      <section className="min-w-0">
        <Card>
          <CardBody>
            <div className="flex flex-wrap items-center justify-between gap-2 mb-3">
              <h2 className="text-sm font-semibold text-brand-navy">
                {cls ? (
                  <span>
                    Documents ·{' '}
                    <span className="font-mono text-ndi-teal">{cls}</span>
                  </span>
                ) : (
                  'All documents'
                )}
              </h2>
              {cls && (
                <Button size="sm" variant="ghost" onClick={clearClass}>
                  Clear class filter
                </Button>
              )}
            </div>

            {docs.isLoading && <TableSkeleton rows={10} />}
            {docs.isError && <ErrorState error={docs.error} onRetry={() => docs.refetch()} />}
            {docs.data && (
              <>
                <p className="mb-2 text-xs text-fg-muted font-mono">
                  {formatNumber(docs.data.total)} total · page {page}
                </p>
                <div className="overflow-x-auto rounded border border-border-subtle">
                  <table className="w-full text-sm">
                    <thead className="bg-gray-50 sticky top-0">
                      <tr>
                        <th className="px-3 py-2 text-left font-medium text-fg-secondary">
                          Name
                        </th>
                        <th className="px-3 py-2 text-left font-medium text-fg-secondary">
                          Class
                        </th>
                        <th className="px-3 py-2 text-left font-medium text-fg-secondary">
                          Mongo ID
                        </th>
                        <th className="px-3 py-2 text-left font-medium text-fg-secondary">
                          ndiId
                        </th>
                      </tr>
                    </thead>
                    <tbody>
                      {docs.data.documents.length === 0 ? (
                        <tr>
                          <td
                            colSpan={4}
                            className="px-3 py-8 text-center text-fg-muted"
                          >
                            No documents for this class.
                          </td>
                        </tr>
                      ) : (
                        docs.data.documents.map((d) => {
                          const did = d.id ?? d.ndiId ?? '';
                          return (
                            <tr
                              key={did}
                              className="border-t border-border-subtle hover:bg-gray-50"
                            >
                              <td className="px-3 py-1.5">
                                <Link
                                  to={`/datasets/${datasetId}/documents/${did}`}
                                  className="text-brand-navy hover:text-ndi-teal hover:underline transition-colors"
                                >
                                  {d.name || (
                                    <span className="text-fg-muted" aria-hidden>
                                      —
                                    </span>
                                  )}
                                </Link>
                              </td>
                              <td className="px-3 py-1.5 font-mono text-xs">
                                {d.className || '—'}
                              </td>
                              <td className="px-3 py-1.5 font-mono text-xs text-fg-muted">
                                {d.id || ''}
                              </td>
                              <td className="px-3 py-1.5 font-mono text-xs text-fg-muted truncate max-w-[220px] md:max-w-[340px] lg:max-w-[480px]">
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
                  <span className="text-sm text-fg-muted font-mono">
                    Page {page}
                  </span>
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
