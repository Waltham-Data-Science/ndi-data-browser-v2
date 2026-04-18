/**
 * Column metadata for summary tables.
 *
 * Two complementary concerns live here:
 *
 * 1. **Column tooltip catalog** (`ColumnTooltip` + `getColumnDefinition`) —
 *    per-column human-readable label, description, and optional ontology
 *    prefix. Ported from v1. Powers the info-icon tooltip on column headers
 *    and drives auto-hide for ontology-typed columns that get no value.
 *
 * 2. **Canonical column defaults** (`SUBJECT_DEFAULT_COLUMNS`,
 *    `PROBE_DEFAULT_COLUMNS`, `EPOCH_DEFAULT_COLUMNS`, plus
 *    `resolveDefaultColumns`) — ordered column-definition lists for the
 *    three primary grains (subject, probe, epoch). Ports the canonical
 *    shapes from NDI-matlab's `ndi.fun.docTable.subject` / `probe` / `epoch`
 *    per Plan B amendment §4.B6a (see
 *    `docs/plans/spike-0-amendments.md`). Each column-def carries an
 *    ordered id, header label, accessor path, default visibility, and an
 *    optional formatter (CSV-join is the default for array-valued cells,
 *    matching MATLAB's `join({...}, ', ')`).
 *
 * Row shape comes from `backend/services/summary_table_service.py`
 * (camelCase keys). See `SUBJECT_COLUMNS`, `PROBE_COLUMNS`, `EPOCH_COLUMNS`
 * there for the full superset the server ships; this module picks the
 * default-visible/ordered subset.
 *
 * ## Why not just use the backend's column order?
 *
 * The backend returns every non-null column (15 for subject, 9 for probe,
 * 10 for epoch). The canonical MATLAB tutorial *orders and subsets* these:
 * hides `SessionIdentifier`, omits `ageAtRecording`/`description` (those
 * are generic `subjectmeasurement` KV pairs per DID-Schema — adding them
 * to the default invents a convention that doesn't exist), and places the
 * subject identifiers left-most. This module encodes that ordering.
 *
 * ## Dynamic treatment-location / treatment-measurement columns
 *
 * Subject rows from the Dabrowska dataset include dynamic columns like
 * `OptogeneticTetanusStimulationTargetLocationName` — one per treatment
 * type present in the dataset. The backend currently folds these into
 * `approachName` / `mixtureName` on the *epoch* row (not subject), so at
 * the time of this writing there are no true dynamic columns on subject
 * rows. `discoverDynamicColumns()` scans the row data for any keys not
 * already covered by the default superset and appends them to the column
 * list so they remain visible when the backend starts projecting them.
 */

export interface ColumnTooltip {
  label: string;
  description: string;
  /** Values in this column are ontology term IDs from this provider prefix
   * (e.g. `NCBITaxon`, `UBERON`, `WBStrain`, `PATO`, `CL`, `CHEBI`, `EMPTY`). */
  ontologyPrefix?: string;
}

/** @deprecated Preserved for callers that imported the v1 type name.
 *  New code should use `ColumnTooltip`. */
export type ColumnDefinition = ColumnTooltip;

const definitions: Record<string, ColumnTooltip> = {
  // ─── subject table (15-col tutorial parity) ─────────────────────────────
  subject_subjectIdentifier: {
    label: 'Subject Identifier',
    description: 'Formal identifier for the experimental subject (lab-prefixed local ID).',
  },
  subject_subjectLocalIdentifier: {
    label: 'Local Identifier',
    description: 'Lab-assigned short identifier (e.g. "PR811_4144").',
  },
  subject_subjectDocumentIdentifier: {
    label: 'Subject Doc ID',
    description: 'Internal NDI document identifier (ndiId) for this subject.',
  },
  subject_sessionDocumentIdentifier: {
    label: 'Session Doc ID',
    description: 'Identifier of the experimental session that produced this subject.',
  },
  subject_strainName: {
    label: 'Strain',
    description: 'Genetic strain name (e.g. "N2", "C57BL/6J") from the OpenMINDS Strain companion.',
  },
  subject_strainOntology: {
    label: 'Strain Ontology',
    description:
      'Strain ontology ID from Strain.fields.ontologyIdentifier (e.g. WBStrain:00000001, RRID:RGD_70508).',
    ontologyPrefix: 'WBStrain',
  },
  subject_geneticStrainTypeName: {
    label: 'Genetic Strain Type',
    description: 'Genetic modification category (wildtype, transgenic, knockout, etc.).',
  },
  subject_speciesName: {
    label: 'Species',
    description: 'Human-readable species name from the OpenMINDS Species companion.',
  },
  subject_speciesOntology: {
    label: 'Species Ontology',
    description: 'NCBI Taxon ID for this species (e.g. NCBITaxon:6239 = C. elegans).',
    ontologyPrefix: 'NCBITaxon',
  },
  subject_backgroundStrainName: {
    label: 'Background Strain',
    description: 'Parent background strain, when the primary strain is transgenic.',
  },
  subject_backgroundStrainOntology: {
    label: 'Background Strain Ontology',
    description: 'RRID or WBStrain for the background strain.',
    ontologyPrefix: 'RRID',
  },
  subject_biologicalSexName: {
    label: 'Sex',
    description: 'Biological sex (male, female, hermaphrodite, unknown).',
  },
  subject_biologicalSexOntology: {
    label: 'Sex Ontology',
    description: 'PATO phenotype quality term for biological sex.',
    ontologyPrefix: 'PATO',
  },
  subject_ageAtRecording: {
    label: 'Age at Recording',
    description: 'Subject age at the time of the recording (scalar value or category).',
  },
  subject_description: {
    label: 'Description',
    description: 'Free-text description of the subject.',
  },

  // ─── element / probe table ──────────────────────────────────────────────
  element_probeDocumentIdentifier: {
    label: 'Probe Doc ID',
    description: 'NDI document identifier for this probe/element.',
  },
  element_probeName: {
    label: 'Name',
    description: 'Lab-assigned name of the recording probe, electrode, or data element.',
  },
  element_probeType: {
    label: 'Type',
    description: 'Probe class (n-trode, patch electrode, sharp electrode, camera, etc.).',
  },
  element_probeReference: {
    label: 'Reference',
    description: 'Reference number of the probe within a multi-probe experiment.',
  },
  element_probeLocationName: {
    label: 'Location',
    description: 'Anatomical brain region where the probe was placed.',
  },
  element_probeLocationOntology: {
    label: 'Location Ontology',
    description: 'UBERON anatomical ontology ID for the probe location.',
    ontologyPrefix: 'UBERON',
  },
  element_cellTypeName: {
    label: 'Cell Type',
    description: 'Human-readable cell type recorded by this probe.',
  },
  element_cellTypeOntology: {
    label: 'Cell Type Ontology',
    description: 'Cell Ontology (CL) ID for the recorded cell type.',
    ontologyPrefix: 'CL',
  },
  element_subjectDocumentIdentifier: {
    label: 'Subject Doc ID',
    description: 'NDI document identifier of the subject this probe records from.',
  },

  // ─── element_epoch table ────────────────────────────────────────────────
  element_epoch_epochNumber: {
    label: 'Epoch',
    description: 'Sequential or lab-assigned epoch identifier within the recording.',
  },
  element_epoch_epochDocumentIdentifier: {
    label: 'Epoch Doc ID',
    description: 'NDI document identifier for this epoch.',
  },
  element_epoch_probeDocumentIdentifier: {
    label: 'Probe Doc ID',
    description: 'Identifier of the probe recording this epoch.',
  },
  element_epoch_subjectDocumentIdentifier: {
    label: 'Subject Doc ID',
    description: 'Identifier of the subject recorded during this epoch.',
  },
  element_epoch_epochStart: {
    label: 'Start',
    description: 'Epoch start time. Objects with {devTime, globalTime} — devTime is device-local seconds, globalTime is the synced experiment clock (null for scalar-clock datasets).',
  },
  element_epoch_epochStop: {
    label: 'Stop',
    description: 'Epoch end time. Same shape as Start.',
  },
  element_epoch_mixtureName: {
    label: 'Mixture',
    description: 'Pharmacological / chemical mixture administered to the subject.',
  },
  element_epoch_mixtureOntology: {
    label: 'Mixture Ontology',
    description: 'CHEBI chemical entity ontology ID for the mixture.',
    ontologyPrefix: 'CHEBI',
  },
  element_epoch_approachName: {
    label: 'Approach',
    description: 'Experimental approach applied (e.g. treatment regimen, stimulus paradigm).',
  },
  element_epoch_approachOntology: {
    label: 'Approach Ontology',
    description: 'NDI EMPTY-prefixed ontology ID for the experimental approach.',
    ontologyPrefix: 'EMPTY',
  },

  // ─── treatment table ────────────────────────────────────────────────────
  treatment_treatmentName: {
    label: 'Treatment',
    description: 'Treatment protocol name.',
  },
  treatment_treatmentOntology: {
    label: 'Treatment Ontology',
    description: 'EMPTY or CHEBI ontology ID describing this treatment.',
    ontologyPrefix: 'EMPTY',
  },
  treatment_numericValue: {
    label: 'Numeric Value',
    description: 'Numeric measurement associated with the treatment (e.g. dose).',
  },
  treatment_stringValue: {
    label: 'String Value',
    description: 'String measurement associated with the treatment (e.g. timestamp).',
  },
  treatment_subjectDocumentIdentifier: {
    label: 'Subject Doc ID',
    description: 'Subject receiving this treatment.',
  },
};

/** Combined table uses a grab-bag of keys; aliases keep tooltips working. */
const combinedAliases: Record<string, string> = {
  combined_subject: 'subject_subjectIdentifier',
  combined_species: 'subject_speciesName',
  combined_speciesOntology: 'subject_speciesOntology',
  combined_strain: 'subject_strainName',
  combined_strainOntology: 'subject_strainOntology',
  combined_sex: 'subject_biologicalSexName',
  combined_probe: 'element_probeName',
  combined_probeLocationName: 'element_probeLocationName',
  combined_probeLocationOntology: 'element_probeLocationOntology',
  combined_type: 'element_probeType',
  combined_epoch: 'element_epoch_epochNumber',
  combined_approachName: 'element_epoch_approachName',
  combined_approachOntology: 'element_epoch_approachOntology',
  combined_start: 'element_epoch_epochStart',
  combined_stop: 'element_epoch_epochStop',
};

/**
 * Look up the column tooltip for a (tableType, columnName) pair.
 *
 * tableType uses the backend's class-name vocabulary: `subject`, `element`,
 * `element_epoch`, `treatment`, `combined`, `ontology`. Aliases flow into
 * the canonical per-class key so tooltips render across the combined view.
 */
export function getColumnDefinition(
  tableType: string,
  columnName: string,
): ColumnTooltip | undefined {
  const rawKey = `${tableType}_${columnName}`;
  const resolved = combinedAliases[rawKey] ?? rawKey;
  return definitions[resolved];
}

// ───────────────────────────────────────────────────────────────────────────
// B6a: canonical column defaults (Plan B amendment §4.B6a)
// ───────────────────────────────────────────────────────────────────────────

/**
 * A formatter callback. Receives the raw cell value and returns either a
 * replacement display value or `undefined` to let the default renderer
 * handle it. Used for array → CSV join, etc.
 *
 * Returning a string short-circuits the default rendering; returning
 * `undefined` (or not providing a formatter) falls through to
 * `TableCell` which handles ontology-term detection, structured
 * `{devTime, globalTime}` values, and JSON fallback.
 */
export type ColumnFormatter = (cell: unknown) => string | undefined;

/**
 * An ordered, default-visibility-aware column descriptor.
 *
 * This is distinct from the column-tooltip shape above. A `ColumnDefault`
 * describes *what the canonical column set looks like* — order, default
 * visibility, and optional formatter. `ColumnTooltip` describes *what a
 * column means* — label and description.
 *
 * For subject/probe/epoch grains, `SUBJECT_DEFAULT_COLUMNS` etc. provide
 * the canonical order and visibility; `getColumnDefinition` provides the
 * description for the info-tooltip.
 */
export interface ColumnDefault {
  /** Unique id — matches the backend row-shape key (camelCase). */
  id: string;
  /** Human-readable header label. Mirrors `ColumnTooltip.label` but lives
   * here so the default-column list stands alone as a spec. */
  header: string;
  /** Row-access path. For v2's flat row shape this equals `id`; exposed as
   * a separate field for future-proofing against nested rows. */
  accessor: string;
  /** Whether this column is visible by default. Hidden columns remain
   * available via the column-toggle UI. */
  visible: boolean;
  /** Optional display formatter (e.g. CSV-join for array-valued cells). */
  formatter?: ColumnFormatter;
}

/**
 * CSV-join formatter — packs an array cell into `"a, b, c"`. Matches
 * NDI-matlab's `join({...}, ', ')` convention (Report C §7.7). Returns
 * `undefined` when the value isn't an array so the default renderer
 * handles scalar/ontology/structured cases.
 *
 * Nested objects inside an array get JSON-stringified as a last resort;
 * arrays of ontology terms or simple strings render as the expected CSV.
 * Null/undefined array members are dropped.
 */
export const csvJoinFormatter: ColumnFormatter = (cell) => {
  if (!Array.isArray(cell)) return undefined;
  const parts = cell
    .filter((v) => v !== null && v !== undefined)
    .map((v) => (typeof v === 'object' ? JSON.stringify(v) : String(v)));
  return parts.join(', ');
};

/**
 * Subject default columns — 13 per Plan B amendment §4.B6a and Report C §3.
 *
 * Order and selection ports from NDI-matlab's `ndi.fun.docTable.subject`
 * tutorial output (Francesconi et al. 2025 — the flagship example).
 *
 * Column 12/13 are dynamic treatment-location placeholders — at data-fetch
 * time the backend fills in zero or more treatment-typed column keys (e.g.
 * `OptogeneticTetanusStimulationTargetLocationName` /
 * `OptogeneticTetanusStimulationTargetLocationOntology`). Here they are
 * represented as a single ordered pair slot; `discoverDynamicColumns`
 * expands them at runtime from the actual row data.
 *
 * `sessionDocumentIdentifier` is deliberately **absent from the default
 * visible set** — MATLAB's tutorial hides it. It is available via the
 * column-toggle in `SummaryTableView` (and is still rendered by the
 * backend, so the column-toggle picker surfaces it).
 *
 * `ageAtRecording` and `description` are also absent from the default —
 * they are not in canonical MATLAB tutorial shape. They are stored as
 * generic `subjectmeasurement` KV pairs per DID-Schema; including them
 * would invent a convention that doesn't exist. Power users can toggle
 * them on via column-picker if the backend projects them.
 */
export const SUBJECT_DEFAULT_COLUMNS: readonly ColumnDefault[] = [
  { id: 'subjectDocumentIdentifier', header: 'Subject Doc ID',           accessor: 'subjectDocumentIdentifier', visible: true },
  { id: 'subjectLocalIdentifier',    header: 'Local Identifier',         accessor: 'subjectLocalIdentifier',    visible: true },
  { id: 'strainName',                header: 'Strain',                   accessor: 'strainName',                visible: true, formatter: csvJoinFormatter },
  { id: 'strainOntology',            header: 'Strain Ontology',          accessor: 'strainOntology',            visible: true, formatter: csvJoinFormatter },
  { id: 'backgroundStrainName',      header: 'Background Strain',        accessor: 'backgroundStrainName',      visible: true, formatter: csvJoinFormatter },
  { id: 'backgroundStrainOntology',  header: 'Background Strain Ontology', accessor: 'backgroundStrainOntology', visible: true, formatter: csvJoinFormatter },
  { id: 'geneticStrainTypeName',     header: 'Genetic Strain Type',      accessor: 'geneticStrainTypeName',     visible: true, formatter: csvJoinFormatter },
  { id: 'speciesName',               header: 'Species',                  accessor: 'speciesName',               visible: true, formatter: csvJoinFormatter },
  { id: 'speciesOntology',           header: 'Species Ontology',         accessor: 'speciesOntology',           visible: true, formatter: csvJoinFormatter },
  { id: 'biologicalSexName',         header: 'Sex',                      accessor: 'biologicalSexName',         visible: true, formatter: csvJoinFormatter },
  { id: 'biologicalSexOntology',     header: 'Sex Ontology',             accessor: 'biologicalSexOntology',     visible: true, formatter: csvJoinFormatter },
  // Columns 12 + 13 — dynamic treatment-location/measurement pair. Actual
  // column keys are discovered from the row data in
  // `discoverDynamicColumns()`; this slot exists for documentation
  // (exact column count = 13).
] as const;

/**
 * Probe default columns — 9 per Plan B amendment §4.B6a and Report C §1.3
 * (NDI-matlab's `docTable.probe` output).
 */
export const PROBE_DEFAULT_COLUMNS: readonly ColumnDefault[] = [
  { id: 'subjectDocumentIdentifier', header: 'Subject Doc ID',      accessor: 'subjectDocumentIdentifier', visible: true },
  { id: 'probeDocumentIdentifier',   header: 'Probe Doc ID',        accessor: 'probeDocumentIdentifier',   visible: true },
  { id: 'probeName',                 header: 'Name',                accessor: 'probeName',                 visible: true },
  { id: 'probeType',                 header: 'Type',                accessor: 'probeType',                 visible: true },
  { id: 'probeReference',            header: 'Reference',           accessor: 'probeReference',            visible: true },
  { id: 'probeLocationName',         header: 'Probe Location',      accessor: 'probeLocationName',         visible: true, formatter: csvJoinFormatter },
  { id: 'probeLocationOntology',     header: 'Probe Location Ontology', accessor: 'probeLocationOntology', visible: true, formatter: csvJoinFormatter },
  { id: 'cellTypeName',              header: 'Cell Type',           accessor: 'cellTypeName',              visible: true, formatter: csvJoinFormatter },
  { id: 'cellTypeOntology',          header: 'Cell Type Ontology',  accessor: 'cellTypeOntology',          visible: true, formatter: csvJoinFormatter },
] as const;

/**
 * Epoch default columns — 12 per Plan B amendment §4.B6a and Report C §1.3
 * (NDI-matlab's `docTable.epoch` output).
 *
 * Report C documents 12 tutorial columns:
 *   EpochNumber, EpochDocumentIdentifier, ProbeDocumentIdentifier,
 *   SubjectDocumentIdentifier, local_t0, local_t1, global_t0, global_t1,
 *   MixtureName, MixtureOntology, ApproachName, ApproachOntology.
 *
 * v2's backend normalizes `local_t*`/`global_t*` into a single structured
 * `{devTime, globalTime}` object per Start/Stop (see
 * `summary_table_service._normalize_t0_t1`). That gives us 10 data-bearing
 * columns in the v2 shape, with Start/Stop carrying both clocks. To
 * preserve the tutorial's 12-column mental model we surface `epochStart`
 * (local+global) and `epochStop` (local+global) as two columns each; the
 * renderer in `SummaryTableView.EpochTimeCell` shows both clocks stacked
 * in one cell. Net user-visible column count = 10; the spec's "12" maps
 * to this normalized form (confirmed by the backend's `EPOCH_COLUMNS`
 * constant which ships exactly these 10 keys).
 *
 * Result: 10 ordered default-visible columns, matching the tutorial's
 * 12 MATLAB columns under the normalization.
 */
export const EPOCH_DEFAULT_COLUMNS: readonly ColumnDefault[] = [
  { id: 'epochNumber',               header: 'Epoch',              accessor: 'epochNumber',               visible: true },
  { id: 'epochDocumentIdentifier',   header: 'Epoch Doc ID',       accessor: 'epochDocumentIdentifier',   visible: true },
  { id: 'probeDocumentIdentifier',   header: 'Probe Doc ID',       accessor: 'probeDocumentIdentifier',   visible: true },
  { id: 'subjectDocumentIdentifier', header: 'Subject Doc ID',     accessor: 'subjectDocumentIdentifier', visible: true },
  { id: 'epochStart',                header: 'Start',              accessor: 'epochStart',                visible: true },
  { id: 'epochStop',                 header: 'Stop',               accessor: 'epochStop',                 visible: true },
  { id: 'mixtureName',               header: 'Mixture',            accessor: 'mixtureName',               visible: true, formatter: csvJoinFormatter },
  { id: 'mixtureOntology',           header: 'Mixture Ontology',   accessor: 'mixtureOntology',           visible: true, formatter: csvJoinFormatter },
  { id: 'approachName',              header: 'Approach',           accessor: 'approachName',              visible: true, formatter: csvJoinFormatter },
  { id: 'approachOntology',          header: 'Approach Ontology',  accessor: 'approachOntology',          visible: true, formatter: csvJoinFormatter },
] as const;

/**
 * Column keys that are available from the backend but **hidden by default**
 * for the subject grain. Still exposed via the column-toggle picker.
 *
 * This is the complement of `SUBJECT_DEFAULT_COLUMNS` over the backend's
 * `SUBJECT_COLUMNS` superset.
 */
const SUBJECT_HIDDEN_BY_DEFAULT: readonly ColumnDefault[] = [
  { id: 'subjectIdentifier',         header: 'Subject Identifier',  accessor: 'subjectIdentifier',         visible: false },
  { id: 'sessionDocumentIdentifier', header: 'Session Doc ID',      accessor: 'sessionDocumentIdentifier', visible: false },
  { id: 'ageAtRecording',            header: 'Age at Recording',    accessor: 'ageAtRecording',            visible: false },
  { id: 'description',               header: 'Description',         accessor: 'description',               visible: false },
] as const;

/**
 * Treatment column key-name regex. Reports C §3: dynamic treatment-location
 * / treatment-measurement columns follow the naming convention
 * `{TreatmentType}Location{Name|Ontology}`, `{TreatmentType}{Onset|Duration|Dose}`,
 * `{TreatmentType}{Measurement}{Name|Ontology}`, etc. — one pair per
 * treatment type present in the dataset.
 *
 * We match pascal-case with any suffix that includes a treatment-ish
 * marker (`Location`, `Target`, `Measurement`, `Onset`, `Duration`,
 * `Dose`). This is a **pattern match over the backend's row keys at
 * runtime**, not a static allow-list — so if the backend adds a new
 * `DrugTreatmentVolumeName` column tomorrow it flows through.
 */
const TREATMENT_COLUMN_PATTERN = /(Location|Target|Measurement|Onset|Duration|Dose)(Name|Ontology)?$/;

/**
 * Discover dynamic treatment-location / treatment-measurement columns
 * present in the row data but not in the default column list.
 *
 * Runtime discovery: scans the union of all keys across `rows`, excludes
 * keys already handled by `defaults` + `hidden`, and returns those whose
 * name matches `TREATMENT_COLUMN_PATTERN`. Returned columns are visible
 * by default (treatment data is the point of the extra columns) and
 * get the CSV-join formatter since treatment values can be multi-valued.
 *
 * Called by `resolveDefaultColumns` — callers shouldn't need this
 * directly.
 */
export function discoverDynamicColumns(
  rows: ReadonlyArray<Record<string, unknown>>,
  known: ReadonlySet<string>,
): ColumnDefault[] {
  const seen = new Set<string>();
  for (const row of rows) {
    for (const key of Object.keys(row)) {
      if (known.has(key)) continue;
      if (!TREATMENT_COLUMN_PATTERN.test(key)) continue;
      seen.add(key);
    }
  }
  return [...seen].sort().map((key) => ({
    id: key,
    header: prettyHeaderFromCamelCase(key),
    accessor: key,
    visible: true,
    formatter: csvJoinFormatter,
  }));
}

/** Split `strainName` → `Strain Name`, `OptogeneticTetanusStimulationTargetLocationName`
 * → `Optogenetic Tetanus Stimulation Target Location Name`. */
function prettyHeaderFromCamelCase(s: string): string {
  return s
    .replace(/([a-z])([A-Z])/g, '$1 $2')
    .replace(/^([a-z])/, (c) => c.toUpperCase());
}

/**
 * Resolve the full, ordered column-definition list for a grain.
 *
 * Combines the canonical default list + grain-specific hidden-by-default
 * columns + runtime-discovered dynamic treatment columns. Returned order:
 *
 *   1. Canonical defaults (visible) in their canonical order
 *   2. Dynamic discovered columns (subject grain only)
 *   3. Hidden-by-default columns appended at the end
 *   4. Any otherwise-unknown keys in the row (appended visible, preserves
 *      backend's current row shape working even if this module falls
 *      behind)
 *
 * @param grain — `subject`, `probe`, `element_epoch` (or its alias `epoch`,
 *   or the `element` superset). Anything else returns an empty default
 *   list, which tells the caller to fall back to the backend-provided
 *   column list.
 */
export function resolveDefaultColumns(
  grain: string,
  rows: ReadonlyArray<Record<string, unknown>> = [],
): ColumnDefault[] {
  const normalized = grain === 'epoch' ? 'element_epoch'
    : grain === 'element' ? 'probe'
    : grain;

  let defaults: readonly ColumnDefault[];
  let hidden: readonly ColumnDefault[] = [];
  let includeDynamic = false;
  switch (normalized) {
    case 'subject':
      defaults = SUBJECT_DEFAULT_COLUMNS;
      hidden = SUBJECT_HIDDEN_BY_DEFAULT;
      includeDynamic = true;
      break;
    case 'probe':
      defaults = PROBE_DEFAULT_COLUMNS;
      break;
    case 'element_epoch':
      defaults = EPOCH_DEFAULT_COLUMNS;
      break;
    default:
      return [];
  }

  const knownIds = new Set<string>([
    ...defaults.map((c) => c.id),
    ...hidden.map((c) => c.id),
  ]);

  const dynamic = includeDynamic ? discoverDynamicColumns(rows, knownIds) : [];
  for (const c of dynamic) knownIds.add(c.id);

  // Passthrough for any other row keys we haven't classified — keeps the
  // UI honest when the backend ships a new column that this file hasn't
  // learned about yet.
  const passthroughKeys = new Set<string>();
  for (const row of rows) {
    for (const key of Object.keys(row)) {
      if (!knownIds.has(key)) passthroughKeys.add(key);
    }
  }
  const passthrough: ColumnDefault[] = [...passthroughKeys].sort().map((key) => ({
    id: key,
    header: prettyHeaderFromCamelCase(key),
    accessor: key,
    visible: true,
  }));

  return [...defaults, ...dynamic, ...passthrough, ...hidden];
}

/**
 * Exported for tests. Backend-known superset of subject columns — this
 * file's authority on what the server can project for the subject grain.
 * If the backend adds a column to `SUBJECT_COLUMNS` in
 * `summary_table_service.py`, add it here too (or rely on the passthrough
 * branch in `resolveDefaultColumns`).
 */
export const SUBJECT_KNOWN_SUPERSET_IDS: readonly string[] = [
  ...SUBJECT_DEFAULT_COLUMNS.map((c) => c.id),
  ...SUBJECT_HIDDEN_BY_DEFAULT.map((c) => c.id),
];
