import { useQuery } from '@tanstack/react-query';
import { apiFetch } from './client';

export interface TableColumn {
  key: string;
  label: string;
  /** For ontology-table columns — the ontology term ID describing the
   * column itself (e.g. `"EMPTY:0000153"`). Present only on the
   * `/api/datasets/:id/tables/ontology` response. */
  ontologyTerm?: string | null;
}

export interface TableResponse {
  columns: TableColumn[];
  rows: Array<Record<string, unknown>>;
}

/** One ontology-table group — all `ontologyTableRow` docs that share a
 * `variableNames` CSV roll up into a single `OntologyTableGroup`. */
export interface OntologyTableGroup {
  variableNames: string[];
  names: string[];
  ontologyNodes: string[];
  table: TableResponse;
  docIds: string[];
  rowCount: number;
}

export interface OntologyTablesResponse {
  groups: OntologyTableGroup[];
}

/** Table of a single NDI class: subject, element (probe), element_epoch,
 * treatment, probe_location, openminds_subject. */
export function useSummaryTable(
  datasetId: string | undefined,
  className: string | undefined,
) {
  return useQuery({
    queryKey: ['table', datasetId, className],
    queryFn: () =>
      apiFetch<TableResponse>(`/api/datasets/${datasetId}/tables/${className}`),
    enabled: !!datasetId && !!className,
    staleTime: 60_000,
  });
}

/** Cross-class joined view — subject ⋈ element ⋈ element_epoch. */
export function useCombinedTable(datasetId: string | undefined) {
  return useQuery({
    queryKey: ['table', datasetId, 'combined'],
    queryFn: () =>
      apiFetch<TableResponse>(`/api/datasets/${datasetId}/tables/combined`),
    enabled: !!datasetId,
    staleTime: 60_000,
  });
}

/** Ontology tables — groups of `ontologyTableRow` docs that share a schema. */
export function useOntologyTables(datasetId: string | undefined) {
  return useQuery({
    queryKey: ['table', datasetId, 'ontology'],
    queryFn: () =>
      apiFetch<OntologyTablesResponse>(`/api/datasets/${datasetId}/tables/ontology`),
    enabled: !!datasetId,
    staleTime: 60_000,
  });
}

/**
 * Canonical table types the UI knows about. Matches the backend's
 * `SUPPORTED_CLASSES` plus the dedicated `combined` + `ontology` routes.
 */
export type TableType =
  | 'combined'
  | 'subject'
  | 'element'
  | 'element_epoch'
  | 'treatment'
  | 'probe_location'
  | 'openminds_subject'
  | 'ontology';
