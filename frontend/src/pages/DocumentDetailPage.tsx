import { useParams, Link } from 'react-router-dom';
import { useDocument } from '@/api/documents';
import { useBinaryKind } from '@/api/binary';
import { Card, CardBody, CardHeader } from '@/components/ui/Card';
import { Skeleton } from '@/components/ui/Skeleton';
import { ErrorState } from '@/components/errors/ErrorState';
import { BinaryViewer } from '@/components/visualization/BinaryViewer';
import { AppearsElsewhere } from '@/components/query/AppearsElsewhere';

export function DocumentDetailPage() {
  const { id, docId } = useParams();
  const doc = useDocument(id, docId);
  const kind = useBinaryKind(id, docId);

  return (
    <div className="space-y-4">
      <p className="text-xs">
        <Link to={`/datasets/${id}`} className="text-brand-600 hover:underline">← Back to dataset</Link>
      </p>

      {doc.isLoading && <Skeleton className="h-24" />}
      {doc.isError && <ErrorState error={doc.error} onRetry={() => doc.refetch()} />}

      {doc.data && (
        <Card>
          <CardHeader>
            <h1 className="text-xl font-bold">{doc.data.name ?? doc.data.id}</h1>
            <p className="text-xs text-slate-500">
              <span className="font-mono">{doc.data.className}</span> · <span className="font-mono">{doc.data.id ?? doc.data.ndiId}</span>
            </p>
          </CardHeader>
          <CardBody className="space-y-4">
            {id && docId && kind.data && kind.data.kind !== 'unknown' && (
              <BinaryViewer datasetId={id} documentId={docId} kind={kind.data.kind} />
            )}

            {id && docId && <AppearsElsewhere datasetId={id} documentId={docId} />}

            <details>
              <summary className="cursor-pointer font-medium">Raw data</summary>
              <pre className="mt-2 max-h-[50vh] overflow-auto rounded bg-slate-50 p-3 text-xs dark:bg-slate-900">
                {JSON.stringify(doc.data.data ?? {}, null, 2)}
              </pre>
            </details>

            <div>
              <Link
                to={`/query?depends-on=${docId}`}
                className="inline-flex items-center gap-1.5 rounded-md bg-white px-3.5 py-1.5 text-sm font-medium text-slate-900 ring-1 ring-slate-300 hover:bg-slate-50 dark:bg-slate-800 dark:text-slate-100 dark:ring-slate-600 dark:hover:bg-slate-700"
              >
                Find documents depending on this one
              </Link>
            </div>
          </CardBody>
        </Card>
      )}
    </div>
  );
}
