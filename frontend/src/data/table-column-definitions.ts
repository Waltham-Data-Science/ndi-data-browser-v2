/**
 * Human-readable descriptions + ontology hints for summary-table columns.
 * Powers the info-icon tooltip on column headers and drives auto-hide for
 * ontology-typed columns that get no value.
 *
 * Ported from v1's data/table-column-definitions.ts with keys rewritten to
 * match v2's camelCase row shape (see
 * `backend/services/summary_table_service.py`). The (table_type, column)
 * key is derived with getColumnDefinition().
 */

export interface ColumnDefinition {
  label: string;
  description: string;
  /** Values in this column are ontology term IDs from this provider prefix
   * (e.g. `NCBITaxon`, `UBERON`, `WBStrain`, `PATO`, `CL`, `CHEBI`, `EMPTY`). */
  ontologyPrefix?: string;
}

const definitions: Record<string, ColumnDefinition> = {
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
 * Look up the column definition for a (tableType, columnName) pair.
 *
 * tableType uses the backend's class-name vocabulary: `subject`, `element`,
 * `element_epoch`, `treatment`, `combined`, `ontology`. Aliases flow into
 * the canonical per-class key so tooltips render across the combined view.
 */
export function getColumnDefinition(
  tableType: string,
  columnName: string,
): ColumnDefinition | undefined {
  const rawKey = `${tableType}_${columnName}`;
  const resolved = combinedAliases[rawKey] ?? rawKey;
  return definitions[resolved];
}
