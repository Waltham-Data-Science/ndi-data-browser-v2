import { useTimeseries, useImageData, useVideoUrl, useFitcurve, type BinaryKind } from '@/api/binary';
import { ErrorState } from '@/components/errors/ErrorState';
import { Skeleton } from '@/components/ui/Skeleton';
import { TimeseriesChart } from './TimeseriesChart';
import { FitcurveChart } from './FitcurveChart';

export function BinaryViewer({
  datasetId,
  documentId,
  kind,
}: {
  datasetId: string;
  documentId: string;
  kind: BinaryKind;
}) {
  if (kind === 'timeseries') {
    return <TimeseriesBlock datasetId={datasetId} documentId={documentId} />;
  }
  if (kind === 'image') {
    return <ImageBlock datasetId={datasetId} documentId={documentId} />;
  }
  if (kind === 'video') {
    return <VideoBlock datasetId={datasetId} documentId={documentId} />;
  }
  if (kind === 'fitcurve') {
    return <FitcurveBlock datasetId={datasetId} documentId={documentId} />;
  }
  return null;
}

function TimeseriesBlock({ datasetId, documentId }: { datasetId: string; documentId: string }) {
  const q = useTimeseries(datasetId, documentId, true);
  if (q.isLoading) return <Skeleton className="h-64 w-full" />;
  if (q.isError) return <ErrorState error={q.error} onRetry={() => q.refetch()} />;
  if (!q.data) return null;
  return <TimeseriesChart data={q.data} />;
}

function ImageBlock({ datasetId, documentId }: { datasetId: string; documentId: string }) {
  const q = useImageData(datasetId, documentId, true);
  if (q.isLoading) return <Skeleton className="h-64 w-full" />;
  if (q.isError) return <ErrorState error={q.error} onRetry={() => q.refetch()} />;
  if (!q.data) return null;
  return (
    <figure>
      <img src={q.data.dataUri} alt="Document binary" className="max-h-[60vh] rounded border border-slate-200" />
      <figcaption className="text-xs text-slate-500 mt-1">
        {q.data.width} × {q.data.height}
      </figcaption>
    </figure>
  );
}

function VideoBlock({ datasetId, documentId }: { datasetId: string; documentId: string }) {
  const q = useVideoUrl(datasetId, documentId, true);
  if (q.isLoading) return <Skeleton className="h-64 w-full" />;
  if (q.isError) return <ErrorState error={q.error} onRetry={() => q.refetch()} />;
  if (!q.data) return null;
  return <video controls src={q.data.url} className="max-h-[60vh] rounded border border-slate-200" />;
}

function FitcurveBlock({ datasetId, documentId }: { datasetId: string; documentId: string }) {
  const q = useFitcurve(datasetId, documentId, true);
  if (q.isLoading) return <Skeleton className="h-64 w-full" />;
  if (q.isError) return <ErrorState error={q.error} onRetry={() => q.refetch()} />;
  if (!q.data) return null;
  return <FitcurveChart data={q.data} />;
}
