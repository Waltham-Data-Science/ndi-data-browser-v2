import { useMutation, useQuery } from '@tanstack/react-query';
import { apiFetch } from './client';

export interface QueryNode {
  operation: string;
  field?: string;
  param1?: unknown;
  param2?: unknown;
}

export interface QueryCondition extends QueryNode {
  /** Same shape as QueryNode — alias kept for readability in the UI. */
  operation: string;
}

export interface QueryResponse {
  documents?: Array<Record<string, unknown>>;
  ids?: string[];
  total?: number;
  totalItems?: number;
  number_matches?: number;
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

export function useAppearsElsewhere(
  documentId: string | undefined,
  excludeDatasetId: string | undefined,
) {
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

export interface QueryOperation {
  name: string;
  label: string;
  description?: string;
  paramSchema?: {
    field?: string;
    param1?: string;
    param2?: string;
  };
  negatable?: boolean;
}

export interface QueryOperationsResponse {
  operations: QueryOperation[];
}

export function useQueryOperations() {
  return useQuery({
    queryKey: ['query-operations'],
    queryFn: () => apiFetch<QueryOperationsResponse>('/api/query/operations'),
    staleTime: Infinity,
  });
}
