import { useQuery } from '@tanstack/react-query';
import { apiFetch } from './client';

export interface DocumentSummary {
  id?: string;
  ndiId?: string;
  name?: string;
  className?: string;
  datasetId?: string;
  data?: Record<string, unknown>;
}

export interface DocumentListResponse {
  total: number;
  page: number;
  pageSize: number;
  documents: DocumentSummary[];
}

export function useDocuments(
  datasetId: string | undefined,
  className: string | null,
  page: number,
  pageSize: number,
) {
  const qs = new URLSearchParams({ page: String(page), pageSize: String(pageSize) });
  if (className) qs.set('class', className);
  return useQuery({
    queryKey: ['documents', datasetId, className, page, pageSize],
    queryFn: () =>
      apiFetch<DocumentListResponse>(`/api/datasets/${datasetId}/documents?${qs.toString()}`),
    enabled: !!datasetId,
  });
}

export function useDocument(datasetId: string | undefined, documentId: string | undefined) {
  return useQuery({
    queryKey: ['document', datasetId, documentId],
    queryFn: () =>
      apiFetch<DocumentSummary>(`/api/datasets/${datasetId}/documents/${documentId}`),
    enabled: !!datasetId && !!documentId,
  });
}
