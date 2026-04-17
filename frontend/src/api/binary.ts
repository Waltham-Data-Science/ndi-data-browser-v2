import { useQuery } from '@tanstack/react-query';
import { apiFetch } from './client';

export type BinaryKind = 'timeseries' | 'image' | 'video' | 'fitcurve' | 'unknown';

export function useBinaryKind(
  datasetId: string | undefined,
  documentId: string | undefined,
) {
  return useQuery({
    queryKey: ['binary-kind', datasetId, documentId],
    queryFn: () =>
      apiFetch<{ kind: BinaryKind }>(
        `/api/datasets/${datasetId}/documents/${documentId}/data/type`,
      ),
    enabled: !!datasetId && !!documentId,
  });
}

/** v1-compatible TimeseriesData shape. Locked by
 * `backend/services/binary_service.py` + `test_binary_shape.py`. */
export interface TimeseriesData {
  channels: Record<string, Array<number | null>>;
  timestamps?: number[] | null;
  sample_count: number;
  format: string;
  error?: string | null;
  /** Machine-readable hint the frontend maps to a friendly message. */
  errorKind?: string | null;
}

export function useTimeseries(
  datasetId: string,
  documentId: string,
  enabled: boolean,
) {
  return useQuery({
    queryKey: ['binary', 'timeseries', datasetId, documentId],
    queryFn: () =>
      apiFetch<TimeseriesData>(
        `/api/datasets/${datasetId}/documents/${documentId}/data/timeseries`,
      ),
    enabled,
  });
}

export interface ImageData {
  dataUri: string;
  width: number;
  height: number;
  mode?: string;
  nFrames?: number;
  format?: string;
  error?: string | null;
}

export function useImageData(
  datasetId: string,
  documentId: string,
  enabled: boolean,
) {
  return useQuery({
    queryKey: ['binary', 'image', datasetId, documentId],
    queryFn: () =>
      apiFetch<ImageData>(
        `/api/datasets/${datasetId}/documents/${documentId}/data/image`,
      ),
    enabled,
  });
}

export interface VideoData {
  url: string;
  contentType: string;
  error?: string | null;
}

export function useVideoUrl(
  datasetId: string,
  documentId: string,
  enabled: boolean,
) {
  return useQuery({
    queryKey: ['binary', 'video', datasetId, documentId],
    queryFn: () =>
      apiFetch<VideoData>(
        `/api/datasets/${datasetId}/documents/${documentId}/data/video`,
      ),
    enabled,
  });
}

export interface FitcurveData {
  form: string;
  parameters: number[];
  x: number[];
  y: number[];
  error?: string | null;
}

export function useFitcurve(
  datasetId: string,
  documentId: string,
  enabled: boolean,
) {
  return useQuery({
    queryKey: ['binary', 'fitcurve', datasetId, documentId],
    queryFn: () =>
      apiFetch<FitcurveData>(
        `/api/datasets/${datasetId}/documents/${documentId}/data/fitcurve`,
      ),
    enabled,
  });
}
