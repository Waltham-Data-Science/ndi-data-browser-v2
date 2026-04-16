import { useMutation, useQuery } from '@tanstack/react-query';
import { apiFetch } from './client';

export interface QueryNode {
  operation: string;
  field?: string;
  param1?: unknown;
  param2?: unknown;
}

export interface QueryResponse {
  documents?: Array<Record<string, unknown>>;
  ids?: string[];
  total?: number;
}

export function useRunQuery() {
  return useMutation({
    mutationFn: (body: { searchstructure: QueryNode[]; scope: string }) =>
      apiFetch<QueryResponse>('/api/query', { method: 'POST', body }),
  });
}

export interface AppearsElsewhereResponse {
  datasets: Array<{ datasetId: string; count: number; sampleDocIds: string[] }>;
  totalReferences: number;
}

export function useAppearsElsewhere(documentId: string | undefined, excludeDatasetId: string | undefined) {
  return useQuery({
    queryKey: ['appears-elsewhere', documentId, excludeDatasetId],
    queryFn: () =>
      apiFetch<AppearsElsewhereResponse>('/api/query/appears-elsewhere', {
        method: 'POST',
        body: { documentId, excludeDatasetId },
      }),
    enabled: !!documentId,
  });
}
