import { ChevronLeft } from 'lucide-react';
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
 * Layout mirrors v1's DocumentDetailPage:
 *   - Breadcrumb "← Back to dataset"
 *   - DocumentDetailView: class badge + JSON tree + deps list + files
 *   - DataPanel: binary visualization (dispatches on detect_kind)
 *   - DependencyGraphView: visual + text dep tree
 *   - AppearsElsewhere: cross-dataset references (M6-era but already wired)
 */
export function DocumentDetailPage() {
  const { id, docId } = useParams();
  const doc = useDocument(id, docId);

  if (!id || !docId) {
    return <p className="text-sm text-gray-500">Missing dataset or document id.</p>;
  }

  return (
    <div className="space-y-4 max-w-4xl">
      <p className="text-xs">
        <Link
          to={`/datasets/${id}/tables/subject`}
          className="inline-flex items-center gap-1 text-brand-600 dark:text-brand-400 hover:underline"
        >
          <ChevronLeft className="h-3 w-3" />
          Back to dataset
        </Link>
      </p>

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
  );
}
