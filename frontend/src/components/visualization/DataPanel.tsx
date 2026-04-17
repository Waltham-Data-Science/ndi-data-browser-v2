import { useState } from 'react';
import { Activity, ImageIcon, LineChart, Video } from 'lucide-react';

import {
  useBinaryKind,
  useFitcurve,
  useImageData,
  useTimeseries,
  useVideoUrl,
} from '@/api/binary';
import { Card, CardBody, CardHeader, CardTitle } from '@/components/ui/Card';
import { Skeleton } from '@/components/ui/Skeleton';

import { FitcurveChart } from './FitcurveChart';
import { ImageViewer } from './ImageViewer';
import { TimeseriesChart } from './TimeseriesChart';
import { VideoPlayer } from './VideoPlayer';

interface DataPanelProps {
  datasetId: string;
  documentId: string;
}

/**
 * Unified binary-data viewer. Dispatches on the backend's `detect_kind()`
 * result:
 *
 * - `timeseries` → TimeseriesChart (uPlot)
 * - `image` → ImageViewer (raster + frame stepper + zoom)
 * - `video` → VideoPlayer (HTML5 native controls)
 * - `fitcurve` → FitcurveChart (uPlot of evaluated parametric curve)
 * - `unknown` → renders nothing (caller's Files section shows the raw links)
 *
 * All child components handle their own error shape; this wrapper only
 * shows the type-detection skeleton.
 */
export function DataPanel({ datasetId, documentId }: DataPanelProps) {
  const [_imageFrame, setImageFrame] = useState<number | undefined>(undefined);
  const { data: kindInfo, isLoading: kindLoading } = useBinaryKind(datasetId, documentId);
  const kind = kindInfo?.kind ?? 'unknown';

  const isTimeseries = kind === 'timeseries';
  const isImage = kind === 'image';
  const isVideo = kind === 'video';
  const isFitcurve = kind === 'fitcurve';

  const { data: tsData, isLoading: tsLoading } = useTimeseries(datasetId, documentId, isTimeseries);
  const { data: imgData, isLoading: imgLoading } = useImageData(datasetId, documentId, isImage);
  const { data: vidData, isLoading: vidLoading } = useVideoUrl(datasetId, documentId, isVideo);
  const { data: fitData, isLoading: fitLoading } = useFitcurve(datasetId, documentId, isFitcurve);

  if (kindLoading) {
    return <Skeleton className="h-40 w-full" />;
  }
  if (kind === 'unknown') {
    return null;
  }

  const Icon = isTimeseries ? Activity : isImage ? ImageIcon : isVideo ? Video : LineChart;
  const label = isTimeseries
    ? `Timeseries${tsData?.format ? ` (${tsData.format.toUpperCase()})` : ''}`
    : isImage
      ? 'Image'
      : isVideo
        ? 'Video'
        : 'Fit curve';

  return (
    <Card>
      <CardHeader className="py-3">
        <CardTitle className="text-xs font-medium flex items-center gap-1.5">
          <Icon className="h-3.5 w-3.5" />
          {label}
        </CardTitle>
      </CardHeader>
      <CardBody className="pt-0">
        {isTimeseries &&
          (tsLoading ? (
            <div className="space-y-2">
              <Skeleton className="h-4 w-48" />
              <Skeleton className="h-64 w-full" />
            </div>
          ) : tsData ? (
            <TimeseriesChart data={tsData} />
          ) : null)}
        {isImage &&
          (imgLoading ? (
            <Skeleton className="h-64 w-full" />
          ) : imgData ? (
            <ImageViewer data={imgData} onFrameChange={setImageFrame} />
          ) : null)}
        {isVideo &&
          (vidLoading ? (
            <Skeleton className="h-64 w-full" />
          ) : vidData ? (
            <VideoPlayer data={vidData} />
          ) : null)}
        {isFitcurve &&
          (fitLoading ? (
            <Skeleton className="h-48 w-full" />
          ) : fitData ? (
            <FitcurveChart data={fitData} />
          ) : null)}
      </CardBody>
    </Card>
  );
}
