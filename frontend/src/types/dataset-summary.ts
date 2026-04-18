/**
 * DatasetSummary — synthesized per-dataset fact sheet.
 *
 * One-to-one mirror of
 * ``backend/services/dataset_summary_service.py::DatasetSummary``.
 * Produced by the B1 synthesizer and returned verbatim from
 * ``GET /api/datasets/:id/summary``.
 *
 * Name intentionally uses the NDI-vocabulary "dataset" rather than the
 * invented term "experiment" (amendment doc §3). Not to be confused with
 * the raw cloud-record shape at ``@/api/datasets.DatasetRecord``; that's
 * the ``IDataset`` payload the catalog endpoints return verbatim.
 */

export interface OntologyTerm {
  label: string;
  /** e.g. `"NCBITaxon:10116"`. ``null`` when the doc recorded a name but no
   *  ontology reference (e.g. Haley's GeneticStrainType with empty
   *  ``preferredOntologyIdentifier``). */
  ontologyId: string | null;
}

export interface DatasetSummaryCounts {
  sessions: number;
  subjects: number;
  probes: number;
  /** Supertype of probes + inferred elements. */
  elements: number;
  epochs: number;
  totalDocuments: number;
}

export interface DatasetSummaryDateRange {
  /** ISO-8601. */
  earliest: string | null;
  /** ISO-8601. */
  latest: string | null;
}

export interface DatasetSummaryContributor {
  firstName: string;
  lastName: string;
  orcid: string | null;
}

export interface DatasetSummaryCitation {
  title: string;
  /** e.g. ``"CC-BY-4.0"``. */
  license: string | null;
  /** Prefix ``10.63884/`` — the canonical dataset DOI. */
  datasetDoi: string | null;
  paperDois: string[];
  contributors: DatasetSummaryContributor[];
  /** Record-creation year from ``createdAt`` in NDI Cloud. **NOT** the
   *  paper publication year — a dataset uploaded in 2026 for a 2019 paper
   *  will report ``year: 2026``. Resolve ``paperDois`` externally for the
   *  true publication year. B4's cite modal is expected to label this
   *  explicitly as "upload year". */
  year: number | null;
}

export interface DatasetSummary {
  datasetId: string;

  /** Counts — sourced from `GET /document-class-counts`. No client-side tally. */
  counts: DatasetSummaryCounts;

  /** Multi-valued facts. ``[]`` = fact genuinely absent; ``null`` = extraction
   *  did not run (e.g. zero subjects). Never truncate. */
  species: OntologyTerm[] | null;
  strains: OntologyTerm[] | null;
  sexes: OntologyTerm[] | null;
  brainRegions: OntologyTerm[] | null;
  /** Free-text bucket — no canonical ontology (amendment doc §3). */
  probeTypes: string[] | null;

  /** Scale signals for catalog cards and detail views. */
  dateRange: DatasetSummaryDateRange;
  totalSizeBytes: number | null;

  /** Citation surface — available verbatim from ``GET /datasets/:id``. */
  citation: DatasetSummaryCitation;

  /** Extraction provenance. Rendered as "Last computed X ago" + debug tooltip. */
  computedAt: string;
  schemaVersion: 'summary:v1';
  extractionWarnings: string[];
}

/** Runtime marker exported alongside the type so that `import type` cannot
 * accidentally erase the schema version string at build time. Useful when
 * a future consumer needs to branch on the shape at runtime. */
export const DatasetSummaryContract = { schemaVersion: 'summary:v1' } as const;

/**
 * Compact catalog-card projection of :interface:`DatasetSummary` (Plan B B2).
 *
 * A strict subset chosen for the visual footprint of a single catalog card
 * (species/region chips, subject + doc counts, citation header). Embedded in
 * each row of the `GET /api/datasets/published` and `GET /api/datasets/my`
 * responses as `DatasetRecord.summary`.
 *
 * Wire-size tradeoff (amendment §4.B2): for a 20-row catalog page the full
 * :interface:`DatasetSummary` adds ~100KB per page (citation.contributors,
 * extractionWarnings, computedAt, probeTypes, strains, sexes). This compact
 * projection is ~400-600 bytes per row. Clients that need the full shape
 * still hit `GET /api/datasets/:id/summary`.
 *
 * NOT a mutation of :interface:`DatasetSummary` — it's an additive new type
 * that refers to the same `OntologyTerm` sub-shape.
 */
export interface CompactDatasetSummaryCounts {
  subjects: number;
  totalDocuments: number;
}

export interface CompactDatasetSummaryCitation {
  title: string;
  license: string | null;
  datasetDoi: string | null;
  year: number | null;
}

export interface CompactDatasetSummary {
  datasetId: string;
  counts: CompactDatasetSummaryCounts;
  /** ``null`` when extraction did not run (e.g. zero subjects). ``[]`` when
   *  it did run but found nothing. Catalog card treats both the same
   *  rendering-wise (hide the row). */
  species: OntologyTerm[] | null;
  brainRegions: OntologyTerm[] | null;
  citation: CompactDatasetSummaryCitation;
  schemaVersion: 'summary:v1';
}
