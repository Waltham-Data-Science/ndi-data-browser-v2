import { useQuery } from '@tanstack/react-query';
import { apiFetch } from './client';

export interface TableResponse {
  columns: Array<{ key: string; label: string }>;
  rows: Array<Record<string, unknown>>;
}

export function useSummaryTable(
  datasetId: string | undefined,
  className: string,
) {
  return useQuery({
    queryKey: ['table', datasetId, className],
    queryFn: () => apiFetch<TableResponse>(`/api/datasets/${datasetId}/tables/${className}`),
    enabled: !!datasetId && !!className,
    staleTime: 60_000,
  });
}
