import { useQuery } from '@tanstack/react-query';
import { apiFetch } from './client';

export type BinaryKind = 'timeseries' | 'image' | 'video' | 'fitcurve' | 'unknown';

export function useBinaryKind(datasetId: string | undefined, documentId: string | undefined) {
  return useQuery({
    queryKey: ['binary-kind', datasetId, documentId],
    queryFn: () =>
      apiFetch<{ kind: BinaryKind }>(
        `/api/datasets/${datasetId}/documents/${documentId}/data/type`,
      ),
    enabled: !!datasetId && !!documentId,
  });
}

export interface TimeseriesData {
  y: number[] | number[][];
  sampleRate: number;
  nSamples?: number;
  channels?: number;
}

export function useTimeseries(datasetId: string, documentId: string, enabled: boolean) {
  return useQuery({
    queryKey: ['binary', 'timeseries', datasetId, documentId],
    queryFn: () =>
      apiFetch<TimeseriesData>(
        `/api/datasets/${datasetId}/documents/${documentId}/data/timeseries`,
      ),
    enabled,
  });
}

export function useImageData(datasetId: string, documentId: string, enabled: boolean) {
  return useQuery({
    queryKey: ['binary', 'image', datasetId, documentId],
    queryFn: () =>
      apiFetch<{ dataUri: string; width: number; height: number }>(
        `/api/datasets/${datasetId}/documents/${documentId}/data/image`,
      ),
    enabled,
  });
}

export function useVideoUrl(datasetId: string, documentId: string, enabled: boolean) {
  return useQuery({
    queryKey: ['binary', 'video', datasetId, documentId],
    queryFn: () =>
      apiFetch<{ url: string; contentType: string }>(
        `/api/datasets/${datasetId}/documents/${documentId}/data/video`,
      ),
    enabled,
  });
}

export function useFitcurve(datasetId: string, documentId: string, enabled: boolean) {
  return useQuery({
    queryKey: ['binary', 'fitcurve', datasetId, documentId],
    queryFn: () =>
      apiFetch<{ form: string; parameters: number[]; x: number[]; y: number[] }>(
        `/api/datasets/${datasetId}/documents/${documentId}/data/fitcurve`,
      ),
    enabled,
  });
}
