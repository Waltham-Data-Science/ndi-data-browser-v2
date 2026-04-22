import { Link, useParams } from 'react-router-dom';

import { useDocument } from '@/api/documents';
import { AppearsElsewhere } from '@/components/query/AppearsElsewhere';
import { DependencyGraphView } from '@/components/documents/DependencyGraph';
import { DocumentDetailView } from '@/components/documents/DocumentDetail';
import { DataPanel } from '@/components/visualization/DataPanel';
import { ErrorState } from '@/components/errors/ErrorState';
import { Skeleton } from '@/components/ui/Skeleton';

/**
 * Document detail page — composes DocumentDetailView + DataPanel +
 * DependencyGraphView + AppearsElsewhere into a single scrollable view.
 *
 * Layout:
 *   1. Depth-gradient hero band with eyebrow ("DOCUMENT · <class>"),
 *      document name as H1, sub-line for class. Includes a
 *      "← Back to dataset" link.
 *   2. Body: 4 stacked sections — DocumentDetailView, DataPanel,
 *      DependencyGraphView, AppearsElsewhere.
 */
export function DocumentDetailPage() {
  const { id, docId } = useParams();
  const doc = useDocument(id, docId);

  if (!id || !docId) {
    return <p className="text-sm text-fg-muted">Missing dataset or document id.</p>;
  }

  const docName = doc.data?.name;
  const docClass = doc.data?.className;
  const eyebrowTail = docClass || (docId.length > 24 ? `${docId.slice(0, 24)}…` : docId);

  return (
    <>
      {/* ── Hero band ─────────────────────────────────────────────── */}
      <section
        className="relative overflow-hidden text-white"
        style={{ background: 'var(--grad-depth)' }}
        aria-labelledby="doc-detail-hero"
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
            DOCUMENT
            <span className="mx-2 opacity-30" aria-hidden>
              |
            </span>
            <span className="font-mono normal-case tracking-normal text-[10.5px] text-white/85">
              {eyebrowTail}
            </span>
          </div>

          {doc.isLoading ? (
            <Skeleton className="h-9 w-3/4 max-w-[720px] bg-white/15" />
          ) : (
            <h1
              id="doc-detail-hero"
              className="text-white font-display font-extrabold tracking-tight leading-tight text-[2rem] md:text-[2.25rem] mb-2 max-w-4xl"
            >
              {docName || 'Document'}
            </h1>
          )}

          {docClass && (
            <p className="text-white/70 text-[13.5px]">
              <span className="font-mono">{docClass}</span>
            </p>
          )}
        </div>
      </section>

      {/* ── Body ──────────────────────────────────────────────────── */}
      <section className="mx-auto max-w-[1200px] px-7 py-7">
        <div className="space-y-4 max-w-4xl">
          {doc.isLoading && (
            <div className="space-y-3">
              <Skeleton className="h-16 w-full" />
              <Skeleton className="h-32 w-full" />
              <Skeleton className="h-48 w-full" />
            </div>
          )}

          {doc.isError && <ErrorState error={doc.error} onRetry={() => doc.refetch()} />}

          {doc.data && (
            <>
              <DocumentDetailView document={doc.data} datasetId={id} />
              <DataPanel datasetId={id} documentId={docId} />
              <DependencyGraphView datasetId={id} documentId={docId} />
              <AppearsElsewhere datasetId={id} documentId={docId} />
            </>
          )}
        </div>
      </section>
    </>
  );
}
