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

// ---------------------------------------------------------------------------
// Dependency graph
// ---------------------------------------------------------------------------

export interface DepGraphNode {
  /** Mongo _id — may be null when the ndiId couldn't be resolved. */
  id: string | null;
  ndiId: string;
  name: string;
  className: string;
  isTarget?: boolean;
}

export interface DepGraphEdge {
  source: string;  // ndiId of the source node
  target: string;  // ndiId of the target node
  label: string;
  direction: 'upstream' | 'downstream';
}

export interface DependencyGraph {
  target_id: string;
  target_ndi_id: string | null;
  nodes: DepGraphNode[];
  edges: DepGraphEdge[];
  node_count: number;
  edge_count: number;
  truncated: boolean;
  max_depth: number;
  error?: string | null;
}

export function useDependencyGraph(
  datasetId: string | undefined,
  documentId: string | undefined,
  maxDepth: number = 3,
) {
  return useQuery({
    queryKey: ['dep-graph', datasetId, documentId, maxDepth],
    queryFn: () =>
      apiFetch<DependencyGraph>(
        `/api/datasets/${datasetId}/documents/${documentId}/dependencies?max_depth=${maxDepth}`,
      ),
    enabled: !!datasetId && !!documentId,
    // 10-min TTL matches the backend Redis cache so revisits render instantly.
    staleTime: 10 * 60 * 1000,
  });
}
