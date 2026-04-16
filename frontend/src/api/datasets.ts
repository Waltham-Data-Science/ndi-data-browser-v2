import { useQuery } from '@tanstack/react-query';
import { apiFetch } from './client';

export interface DatasetSummary {
  id: string;
  name: string;
  description?: string;
  abstract?: string;
  className?: string;
  contributors?: Array<{ firstName?: string; lastName?: string; contact?: string }>;
  correspondingAuthors?: Array<{ firstName?: string; lastName?: string; contact?: string }>;
  funding?: Array<{ source?: string }>;
  associatedPublications?: string[];
  pubMedId?: string;
  doi?: string;
  license?: string;
  branchName?: string;
  isSubscribed?: boolean;
  organizationId?: string;
  isPublished?: boolean;
  isDeleted?: boolean;
  publishStatus?: string;
  createdAt?: string;
  updatedAt?: string;
  uploadedAt?: string;
  totalSize?: number;
  documentCount?: number;
}

export interface DatasetListResponse {
  totalNumber: number;
  datasets: DatasetSummary[];
}

export interface ClassCountsResponse {
  datasetId: string;
  totalDocuments: number;
  classCounts: Record<string, number>;
}

export function usePublishedDatasets(page: number, pageSize: number) {
  return useQuery({
    queryKey: ['datasets', 'published', page, pageSize],
    queryFn: () =>
      apiFetch<DatasetListResponse>(`/api/datasets/published?page=${page}&pageSize=${pageSize}`),
  });
}

export function useMyDatasets(enabled: boolean) {
  return useQuery({
    queryKey: ['datasets', 'my'],
    queryFn: () => apiFetch<DatasetListResponse>('/api/datasets/my'),
    enabled,
  });
}

export function useDataset(datasetId: string | undefined) {
  return useQuery({
    queryKey: ['dataset', datasetId],
    queryFn: () => apiFetch<DatasetSummary>(`/api/datasets/${datasetId}`),
    enabled: !!datasetId,
  });
}

export function useClassCounts(datasetId: string | undefined) {
  return useQuery({
    queryKey: ['dataset', datasetId, 'class-counts'],
    queryFn: () => apiFetch<ClassCountsResponse>(`/api/datasets/${datasetId}/class-counts`),
    enabled: !!datasetId,
  });
}
