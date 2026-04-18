import { useQuery } from '@tanstack/react-query';

import type { DatasetSummary } from '@/types/dataset-summary';

import { apiFetch } from './client';

export interface Contributor {
  firstName?: string;
  lastName?: string;
  contact?: string;
  /** ORCID URL, e.g. `https://orcid.org/0000-0001-6282-7124`. */
  orcid?: string;
}

export interface AssociatedPublication {
  title?: string;
  /** DOI URL, e.g. `https://doi.org/10.7554/eLife.103191.4`. */
  DOI?: string;
  PMID?: string;
  PMCID?: string;
}

/** Raw cloud-record shape returned verbatim from `/api/datasets/...` endpoints.
 *
 * Renamed from `DatasetSummary` (Plan B B1) to make room for the new
 * `DatasetSummary` type — see `@/types/dataset-summary` — which holds the
 * synthesized per-dataset fact sheet produced by
 * `GET /api/datasets/:id/summary`. This interface continues to model the
 * cloud's `IDataset` payload.
 */
export interface DatasetRecord {
  id: string;
  /** Mongo _id returned as `_id` on detail; v2 exposes it as `id` across hooks. */
  _id?: string;
  name: string;
  description?: string;
  abstract?: string;
  className?: string;
  affiliation?: string;
  /** Comma-separated species list — e.g. "Caenorhabditis elegans, Escherichia coli". */
  species?: string;
  brainRegions?: string;
  numberOfSubjects?: number;
  neurons?: number;
  contributors?: Contributor[];
  correspondingAuthors?: Contributor[];
  funding?: Array<{ source?: string }>;
  associatedPublications?: AssociatedPublication[];
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
  datasets: DatasetRecord[];
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
    queryFn: () => apiFetch<DatasetRecord>(`/api/datasets/${datasetId}`),
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

/**
 * Synthesized dataset summary — the Plan B B1 `DatasetSummary`. Backed by
 * `GET /api/datasets/:id/summary`, produced by the backend
 * :class:`DatasetSummaryService` from cloud-indexed class counts +
 * ndiquery-based fact extraction.
 *
 * Not to be confused with :interface:`DatasetRecord` above (the raw
 * `IDataset` shape returned by the catalog endpoints).
 */
export function useDatasetSummary(datasetId: string | undefined) {
  return useQuery({
    queryKey: ['dataset', datasetId, 'summary'],
    queryFn: () =>
      apiFetch<DatasetSummary>(`/api/datasets/${datasetId}/summary`),
    enabled: !!datasetId,
  });
}

/**
 * Grain-selectable pivot response envelope (Plan B B6e). Mirrors
 * :class:`backend.services.pivot_service.PivotResponse`.
 *
 * ``grain`` is one of ``subject`` / ``session`` / ``element`` in v1. Row
 * shape is grain-specific; see the backend projection helpers for the
 * contract per grain.
 */
export interface PivotColumn {
  key: string;
  label: string;
}

export interface PivotResponse {
  datasetId: string;
  grain: string;
  columns: PivotColumn[];
  rows: Array<Record<string, unknown>>;
  computedAt: string;
  schemaVersion: 'pivot:v1';
  totalRows: number;
}

export type PivotGrain = 'subject' | 'session' | 'element';

/**
 * Fetches the pivot table for a given dataset + grain. Gated by
 * ``FEATURE_PIVOT_V1`` on the backend — a 503 indicates the feature is
 * disabled and the pivot nav should hide itself. The TanStack Query
 * retry gate already treats 5xx as non-retryable when it arrives as an
 * :class:`ApiError`.
 */
export function useDatasetPivot(
  datasetId: string | undefined,
  grain: PivotGrain | undefined,
) {
  return useQuery({
    queryKey: ['dataset', datasetId, 'pivot', grain],
    queryFn: () =>
      apiFetch<PivotResponse>(
        `/api/datasets/${datasetId}/pivot/${grain}`,
      ),
    enabled: !!datasetId && !!grain,
    staleTime: 60_000,
  });
}
