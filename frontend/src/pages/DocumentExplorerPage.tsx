import { useMemo } from 'react';
import { Link, useParams, useSearchParams } from 'react-router-dom';
import { LayoutGrid, List } from 'lucide-react';

import { useClassCounts, useDataset } from '@/api/datasets';
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
 * Layout:
 *   1. Depth-gradient hero band with eyebrow "DOCUMENT EXPLORER",
 *      dataset-name-aware H1, a back-to-dataset link, and subtitle.
 *   2. Body: mode toggle (Summary Tables / Raw Documents), then either
 *      embedded TableTab or the 2-col raw documents pane (220px sidebar
 *      + paginated table).
 *
 * URL state: `?mode=raw&class=<cls>&page=<n>` for deep-linkability.
 */
export function DocumentExplorerPage() {
  const { id } = useParams();
  const [searchParams, setSearchParams] = useSearchParams();
  const mode: Mode = searchParams.get('mode') === 'raw' ? 'raw' : 'summary';
  const ds = useDataset(id);

  const setMode = (next: Mode) => {
    const params = new URLSearchParams(searchParams);
    if (next === 'summary') params.delete('mode');
    else params.set('mode', 'raw');
    setSearchParams(params, { replace: true });
  };

  if (!id) {
    return <p className="text-sm text-fg-muted">Missing dataset id.</p>;
  }

  const datasetName = ds.data?.name;

  return (
    <>
      {/* ── Hero band ─────────────────────────────────────────────── */}
      <section
        className="relative overflow-hidden text-white"
        style={{ background: 'var(--grad-depth)' }}
        aria-labelledby="doc-explorer-hero"
      >
        <div
          aria-hidden
          className="absolute inset-0 pointer-events-none"
          style={{
            backgroundImage: "url('/brand/ndicloud-emblem.svg')",
            backgroundSize: '120px',
            backgroundRepeat: 'repeat',
            opacity: 0.05,
          }}
        />
        <div className="relative mx-auto max-w-[1200px] px-7 py-10 md:py-12">
          {/* Back link */}
          <div className="mb-3">
            <Link
              to={`/datasets/${id}`}
              className="inline-flex items-center gap-1.5 text-[12px] text-white/60 hover:text-white/90 transition-colors"
            >
              <span aria-hidden>&larr;</span> Back to dataset
            </Link>
          </div>

          <div className="eyebrow mb-4">
            <span className="eyebrow-dot" aria-hidden />
            DOCUMENT EXPLORER
          </div>

          <h1
            id="doc-explorer-hero"
            className="text-white font-display font-extrabold tracking-tight leading-tight text-[2rem] md:text-[2.25rem] mb-2 max-w-4xl"
          >
            {datasetName ? `${datasetName} documents` : 'Explore raw documents.'}
          </h1>

          <p className="text-white/70 text-[14.5px] leading-relaxed max-w-[620px]">
            Pivot between curated summary tables and the underlying raw
            document list, scoped by NDI class.
          </p>
        </div>
      </section>

      {/* ── Body ──────────────────────────────────────────────────── */}
      <section className="mx-auto max-w-[1200px] px-7 py-7">
        <div className="space-y-3">
          <div className="flex items-center justify-end">
            <ModeToggle value={mode} onChange={setMode} />
          </div>
          {mode === 'summary' ? (
            <TableTab />
          ) : (
            <RawDocumentsPane
              datasetId={id}
              searchParams={searchParams}
              setSearchParams={setSearchParams}
            />
          )}
        </div>
      </section>
    </>
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
      className="flex items-center rounded-md border border-border-subtle overflow-hidden"
    >
      <button
        type="button"
        onClick={() => onChange('summary')}
        aria-pressed={value === 'summary'}
        className={cn(
          'flex items-center gap-1.5 px-3 py-1.5 text-xs font-medium transition-colors',
          value === 'summary'
            ? 'bg-brand-navy text-white'
            : 'text-fg-secondary hover:text-brand-navy',
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
            ? 'bg-brand-navy text-white'
            : 'text-fg-secondary hover:text-brand-navy',
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

  // `min-w-0` on the grid children so the 1fr track can shrink below its
  // min-content and the inner table's `overflow-x-auto` wrapper actually
  // scrolls horizontally instead of forcing the whole page wider. Matches
  // the fix in DatasetDetailPage.
  return (
    <div className="grid gap-4 lg:grid-cols-[220px_1fr]">
      <aside className="min-w-0">
        <Card>
          <CardHeader className="py-3">
            <CardTitle className="text-xs">Document classes</CardTitle>
          </CardHeader>
          <CardBody className="pt-0">
            {counts.isLoading ? (
              <p className="text-xs text-fg-muted">Loading…</p>
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
                <Button size="sm" variant="ghost" onClick={() => setClass(null)}>
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
