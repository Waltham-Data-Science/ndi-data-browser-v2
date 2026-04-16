import { useState } from 'react';
import { useAppearsElsewhere } from '@/api/query';
import { Button } from '@/components/ui/Button';
import { Card, CardBody } from '@/components/ui/Card';

export function AppearsElsewhere({
  datasetId,
  documentId,
}: {
  datasetId: string;
  documentId: string;
}) {
  const [enabled, setEnabled] = useState(false);
  const q = useAppearsElsewhere(enabled ? documentId : undefined, datasetId);

  if (!enabled) {
    return (
      <div className="rounded border border-dashed border-slate-300 p-3">
        <p className="text-sm text-slate-600 dark:text-slate-300">
          Find where this document is referenced across all other datasets.
        </p>
        <Button size="sm" variant="secondary" className="mt-2" onClick={() => setEnabled(true)}>
          Search cross-cloud
        </Button>
      </div>
    );
  }

  if (q.isLoading) return <p className="text-sm text-slate-500">Searching cross-cloud…</p>;
  if (q.isError) {
    return (
      <div className="rounded border border-amber-300 bg-amber-50 p-3 text-sm dark:border-amber-700 dark:bg-amber-900/30">
        Could not complete cross-cloud search.{' '}
        <button className="underline" onClick={() => q.refetch()}>Retry</button>
      </div>
    );
  }
  if (!q.data) return null;

  if (q.data.datasets.length === 0) {
    return <p className="text-sm text-slate-600">Not referenced anywhere else.</p>;
  }

  return (
    <Card>
      <CardBody>
        <h3 className="text-sm font-semibold mb-2">
          Referenced by {q.data.totalReferences} documents across {q.data.datasets.length} other datasets
        </h3>
        <ul className="space-y-1">
          {q.data.datasets.map((d) => (
            <li key={d.datasetId} className="text-sm">
              <a
                href={`/datasets/${d.datasetId}`}
                className="text-brand-600 hover:underline"
              >
                {d.datasetId}
              </a>{' '}
              <span className="text-slate-500">— {d.count} reference{d.count === 1 ? '' : 's'}</span>
            </li>
          ))}
        </ul>
      </CardBody>
    </Card>
  );
}
