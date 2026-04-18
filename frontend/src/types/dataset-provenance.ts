/**
 * DatasetProvenance — aggregated dataset-level derivation facts.
 *
 * One-to-one mirror of
 * ``backend/services/dataset_provenance_service.py::DatasetProvenance``.
 * Produced by the B5 aggregator and returned verbatim from
 * ``GET /api/datasets/:id/provenance``.
 *
 * Vocabulary lock (amendment §4.B5): we call this "dataset provenance" /
 * "derivation graph" — NEVER "lineage". The cloud's ``classLineage`` is
 * *class-ISA* lineage (a ``spikesorting`` doc's superclass chain), which
 * is a completely different concept. Using "lineage" unqualified in v2
 * would be a naming clash.
 */

export interface DatasetDependencyEdge {
  /** The dataset being described (always this dataset). */
  sourceDatasetId: string;
  /** The other dataset some of this dataset's documents depend on. */
  targetDatasetId: string;
  /** The document class of the source docs carrying the ``depends_on``
   *  refs — e.g. ``"element"``, ``"element_epoch"``, ``"spikesorting"``.
   *  Useful for grouping "X element docs point at Y" vs "X epochs point
   *  at Y" in the UI. */
  viaDocumentClass: string;
  /** Count of DISTINCT target ndiIds in ``targetDatasetId`` referenced
   *  by ``depends_on`` fields on documents of class ``viaDocumentClass``
   *  in ``sourceDatasetId``. NOT a per-source-document count — two source
   *  docs pointing at the same target ndiId contribute 1 to this count,
   *  not 2. Dedup is intentional: shared probe / subject refs are common
   *  in NDI and document-level counting would inflate whenever callers
   *  rely on a shared upstream entity. */
  edgeCount: number;
}

export interface DatasetProvenance {
  datasetId: string;
  /** Parent dataset this one was branched from, or ``null`` if not a
   *  branch. Sourced from ``IDataset.branchOf`` on the cloud. */
  branchOf: string | null;
  /** Child datasets forked off this one. ``[]`` means this dataset has
   *  not been branched into any downstream forks. */
  branches: string[];
  /** Cross-dataset ``depends_on`` edges, one per
   *  ``(targetDatasetId, viaDocumentClass)`` tuple. Same-dataset refs
   *  (dataset depends on itself) are filtered out; they're the
   *  per-document dependency-graph's concern (M5). */
  documentDependencies: DatasetDependencyEdge[];
  /** ISO-8601 build timestamp. Cached blobs stay fresh for 5 minutes
   *  (amendment §4.B3). */
  computedAt: string;
  schemaVersion: 'provenance:v1';
}

/** Runtime marker exported alongside the type so that ``import type`` cannot
 *  accidentally erase the schema version at build time. Mirrors the
 *  ``DatasetSummaryContract`` pattern in ``dataset-summary.ts``. */
export const DatasetProvenanceContract = {
  schemaVersion: 'provenance:v1',
} as const;
