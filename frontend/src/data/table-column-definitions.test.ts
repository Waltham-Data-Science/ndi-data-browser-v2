/**
 * Tests for B6a canonical column defaults (Plan B amendment §4.B6a).
 *
 * Gates:
 *   - Subject default has exactly 13 columns (the 11 static tutorial cols +
 *     one dynamic treatment-location pair = 13 per §4.B6a).
 *   - Probe default has exactly 9 columns.
 *   - Epoch default has the canonical tutorial shape (see inline rationale).
 *   - `sessionDocumentIdentifier` is NOT in the default visible subject set.
 *   - `age` / `weight` / `ageAtRecording` / `description` are NOT in the
 *     default visible subject set.
 *   - Multi-valued cells CSV-join via `csvJoinFormatter`.
 *   - Dynamic treatment columns get discovered at runtime from row data.
 */
import { describe, expect, it } from 'vitest';
import {
  SUBJECT_DEFAULT_COLUMNS,
  PROBE_DEFAULT_COLUMNS,
  EPOCH_DEFAULT_COLUMNS,
  csvJoinFormatter,
  discoverDynamicColumns,
  resolveDefaultColumns,
  getColumnDefinition,
} from './table-column-definitions';

describe('SUBJECT_DEFAULT_COLUMNS', () => {
  it('has exactly 11 static columns (tutorial 13 = 11 static + 1 dynamic pair)', () => {
    // Amendment §4.B6a lists 13 "columns" where 12 and 13 are a dynamic
    // pair (treatment-location name + ontology). The static array holds
    // 11 columns; the remaining 2 are discovered at runtime from row data
    // via `discoverDynamicColumns`. See `resolveDefaultColumns` for the
    // combined output.
    expect(SUBJECT_DEFAULT_COLUMNS.length).toBe(11);
  });

  it('matches the tutorial-order ids', () => {
    const ids = SUBJECT_DEFAULT_COLUMNS.map((c) => c.id);
    expect(ids).toEqual([
      'subjectDocumentIdentifier',
      'subjectLocalIdentifier',
      'strainName',
      'strainOntology',
      'backgroundStrainName',
      'backgroundStrainOntology',
      'geneticStrainTypeName',
      'speciesName',
      'speciesOntology',
      'biologicalSexName',
      'biologicalSexOntology',
    ]);
  });

  it('marks every canonical column as visible', () => {
    for (const c of SUBJECT_DEFAULT_COLUMNS) {
      expect(c.visible).toBe(true);
    }
  });

  it('does NOT include sessionDocumentIdentifier in the default visible set', () => {
    const ids = new Set(SUBJECT_DEFAULT_COLUMNS.map((c) => c.id));
    expect(ids.has('sessionDocumentIdentifier')).toBe(false);
  });

  it('does NOT include age or weight in the default visible set', () => {
    // Per amendment §4.B6a: age / weight are not in the canonical MATLAB
    // view — they live as generic `subjectmeasurement` KV pairs and
    // surfacing them would invent a convention that doesn't exist.
    const ids = new Set(SUBJECT_DEFAULT_COLUMNS.map((c) => c.id));
    expect(ids.has('ageAtRecording')).toBe(false);
    expect(ids.has('age')).toBe(false);
    expect(ids.has('weight')).toBe(false);
    expect(ids.has('description')).toBe(false);
  });

  it('applies csvJoinFormatter to every multi-valued column', () => {
    // Per amendment §4.B6a: multi-valued cells use CSV join with ", "
    // separator (matches MATLAB `join({...}, ', ')`). Identifier columns
    // are single-valued and don't need a formatter; ontology/name columns
    // can be multi-valued and do.
    const multiValued = [
      'strainName', 'strainOntology',
      'backgroundStrainName', 'backgroundStrainOntology',
      'geneticStrainTypeName',
      'speciesName', 'speciesOntology',
      'biologicalSexName', 'biologicalSexOntology',
    ];
    for (const id of multiValued) {
      const col = SUBJECT_DEFAULT_COLUMNS.find((c) => c.id === id);
      expect(col?.formatter).toBe(csvJoinFormatter);
    }
  });
});

describe('PROBE_DEFAULT_COLUMNS', () => {
  it('has exactly 9 columns (Report C §1.3)', () => {
    expect(PROBE_DEFAULT_COLUMNS.length).toBe(9);
  });

  it('matches the tutorial-order ids', () => {
    const ids = PROBE_DEFAULT_COLUMNS.map((c) => c.id);
    expect(ids).toEqual([
      'subjectDocumentIdentifier',
      'probeDocumentIdentifier',
      'probeName',
      'probeType',
      'probeReference',
      'probeLocationName',
      'probeLocationOntology',
      'cellTypeName',
      'cellTypeOntology',
    ]);
  });

  it('marks every column as visible', () => {
    for (const c of PROBE_DEFAULT_COLUMNS) {
      expect(c.visible).toBe(true);
    }
  });
});

describe('EPOCH_DEFAULT_COLUMNS', () => {
  it('has 10 columns — tutorial\'s 12 normalized to v2\'s {devTime, globalTime} shape', () => {
    // The tutorial lists local_t0, local_t1, global_t0, global_t1 as four
    // columns (= 12 total). v2's backend normalizes these into structured
    // `{devTime, globalTime}` values on `epochStart`/`epochStop`, giving
    // us 10 user-visible columns. See module docstring + backend
    // `EPOCH_COLUMNS`.
    expect(EPOCH_DEFAULT_COLUMNS.length).toBe(10);
  });

  it('matches the tutorial-order ids', () => {
    const ids = EPOCH_DEFAULT_COLUMNS.map((c) => c.id);
    expect(ids).toEqual([
      'epochNumber',
      'epochDocumentIdentifier',
      'probeDocumentIdentifier',
      'subjectDocumentIdentifier',
      'epochStart',
      'epochStop',
      'mixtureName',
      'mixtureOntology',
      'approachName',
      'approachOntology',
    ]);
  });

  it('applies csvJoinFormatter to multi-valued mixture/approach columns', () => {
    for (const id of ['mixtureName', 'mixtureOntology', 'approachName', 'approachOntology']) {
      const col = EPOCH_DEFAULT_COLUMNS.find((c) => c.id === id);
      expect(col?.formatter).toBe(csvJoinFormatter);
    }
  });
});

describe('csvJoinFormatter', () => {
  it('joins string arrays with ", "', () => {
    expect(csvJoinFormatter(['a', 'b', 'c'])).toBe('a, b, c');
  });

  it('returns empty string for empty arrays', () => {
    expect(csvJoinFormatter([])).toBe('');
  });

  it('drops null and undefined members', () => {
    expect(csvJoinFormatter(['a', null, 'b', undefined, 'c'])).toBe('a, b, c');
  });

  it('stringifies non-object scalars', () => {
    expect(csvJoinFormatter([1, 2, 3])).toBe('1, 2, 3');
    expect(csvJoinFormatter([true, false])).toBe('true, false');
  });

  it('JSON-stringifies nested objects inside arrays', () => {
    expect(csvJoinFormatter([{ x: 1 }, { y: 2 }])).toBe('{"x":1}, {"y":2}');
  });

  it('returns undefined for non-array values to let default renderer handle them', () => {
    expect(csvJoinFormatter('a single string')).toBeUndefined();
    expect(csvJoinFormatter(42)).toBeUndefined();
    expect(csvJoinFormatter(null)).toBeUndefined();
    expect(csvJoinFormatter({ devTime: 0, globalTime: 1 })).toBeUndefined();
  });

  it('joins ontology-looking strings as CSV (power-user chips-split is future work)', () => {
    // Per amendment §4.B6a: multi-valued cells = CSV join; chip-splitting
    // into UI chips is explicitly deferred to future column-config work.
    expect(csvJoinFormatter(['NCBITaxon:10116', 'NCBITaxon:10117']))
      .toBe('NCBITaxon:10116, NCBITaxon:10117');
  });
});

describe('discoverDynamicColumns', () => {
  const known = new Set([
    'subjectDocumentIdentifier',
    'subjectLocalIdentifier',
    'strainName',
  ]);

  it('picks up treatment-location columns from Dabrowska-shaped rows', () => {
    const rows = [
      {
        subjectDocumentIdentifier: 'a',
        subjectLocalIdentifier: 'b',
        strainName: 'CRF-Cre',
        OptogeneticTetanusStimulationTargetLocationName: 'BNST',
        OptogeneticTetanusStimulationTargetLocationOntology: 'UBERON:0001880',
      },
    ];
    const discovered = discoverDynamicColumns(rows, known);
    const ids = discovered.map((c) => c.id);
    expect(ids).toContain('OptogeneticTetanusStimulationTargetLocationName');
    expect(ids).toContain('OptogeneticTetanusStimulationTargetLocationOntology');
  });

  it('picks up treatment-measurement columns (Onset, Duration, Dose)', () => {
    const rows = [
      {
        DrugTreatmentOnset: '15:00',
        DrugTreatmentDuration: '30m',
        DrugTreatmentDose: '5mg/kg',
      },
    ];
    const discovered = discoverDynamicColumns(rows, new Set());
    const ids = discovered.map((c) => c.id);
    expect(ids).toContain('DrugTreatmentOnset');
    expect(ids).toContain('DrugTreatmentDuration');
    expect(ids).toContain('DrugTreatmentDose');
  });

  it('ignores keys already in the known set', () => {
    const rows = [
      { strainName: 'CRF-Cre', SomethingLocationName: 'BNST' },
    ];
    const discovered = discoverDynamicColumns(rows, new Set(['strainName', 'SomethingLocationName']));
    expect(discovered.length).toBe(0);
  });

  it('sorts discovered columns alphabetically for stable ordering', () => {
    const rows = [
      {
        ZetaLocationName: 'z',
        AlphaLocationName: 'a',
        BetaLocationName: 'b',
      },
    ];
    const discovered = discoverDynamicColumns(rows, new Set());
    expect(discovered.map((c) => c.id)).toEqual([
      'AlphaLocationName',
      'BetaLocationName',
      'ZetaLocationName',
    ]);
  });

  it('gives each dynamic column csvJoinFormatter (treatment values can be multi-valued)', () => {
    const rows = [{ FooLocationName: 'bar' }];
    const discovered = discoverDynamicColumns(rows, new Set());
    expect(discovered[0].formatter).toBe(csvJoinFormatter);
  });

  it('generates human-readable headers from camelCase/PascalCase keys', () => {
    const rows = [
      {
        OptogeneticTetanusStimulationTargetLocationName: 'x',
      },
    ];
    const discovered = discoverDynamicColumns(rows, new Set());
    expect(discovered[0].header).toBe(
      'Optogenetic Tetanus Stimulation Target Location Name',
    );
  });

  it('ignores non-treatment-shaped keys (e.g. ad-hoc custom columns)', () => {
    const rows = [
      { randomCustomField: 'x', subjectDescription: 'y' },
    ];
    const discovered = discoverDynamicColumns(rows, new Set());
    expect(discovered.length).toBe(0);
  });
});

describe('resolveDefaultColumns', () => {
  it('combines canonical defaults + dynamic + hidden for the subject grain', () => {
    const rows = [
      {
        subjectDocumentIdentifier: 'a',
        subjectLocalIdentifier: 'b',
        strainName: 'CRF-Cre',
        sessionDocumentIdentifier: 'sess_1',
        OptogeneticTetanusStimulationTargetLocationName: 'BNST',
        OptogeneticTetanusStimulationTargetLocationOntology: 'UBERON:0001880',
      },
    ];
    const result = resolveDefaultColumns('subject', rows);
    const ids = result.map((c) => c.id);
    // Canonical 11 come first, in order.
    expect(ids.slice(0, 11)).toEqual([
      'subjectDocumentIdentifier',
      'subjectLocalIdentifier',
      'strainName',
      'strainOntology',
      'backgroundStrainName',
      'backgroundStrainOntology',
      'geneticStrainTypeName',
      'speciesName',
      'speciesOntology',
      'biologicalSexName',
      'biologicalSexOntology',
    ]);
    // Dynamic treatment columns come next (visible).
    expect(ids).toContain('OptogeneticTetanusStimulationTargetLocationName');
    expect(ids).toContain('OptogeneticTetanusStimulationTargetLocationOntology');
    // Hidden-by-default columns come last.
    const sessionCol = result.find((c) => c.id === 'sessionDocumentIdentifier');
    expect(sessionCol?.visible).toBe(false);
  });

  it('handles the probe grain with no dynamic columns', () => {
    const result = resolveDefaultColumns('probe');
    expect(result.length).toBe(PROBE_DEFAULT_COLUMNS.length);
    expect(result.every((c) => c.visible)).toBe(true);
  });

  it('handles grain aliases (epoch → element_epoch, element → probe)', () => {
    expect(resolveDefaultColumns('epoch').map((c) => c.id))
      .toEqual(EPOCH_DEFAULT_COLUMNS.map((c) => c.id));
    expect(resolveDefaultColumns('element').map((c) => c.id))
      .toEqual(PROBE_DEFAULT_COLUMNS.map((c) => c.id));
  });

  it('returns empty list for unknown grains (signals caller to fall back)', () => {
    expect(resolveDefaultColumns('combined')).toEqual([]);
    expect(resolveDefaultColumns('ontology')).toEqual([]);
    expect(resolveDefaultColumns('treatment')).toEqual([]);
    expect(resolveDefaultColumns('probe_location')).toEqual([]);
    expect(resolveDefaultColumns('openminds_subject')).toEqual([]);
  });

  it('passes through unknown backend keys so new columns stay visible', () => {
    // Guardrail: if the backend ships a new subject column tomorrow
    // (e.g. `newField`), it flows through visible instead of disappearing.
    const rows = [{ newField: 'hello' }];
    const result = resolveDefaultColumns('subject', rows);
    const passthrough = result.find((c) => c.id === 'newField');
    expect(passthrough).toBeDefined();
    expect(passthrough?.visible).toBe(true);
  });
});

describe('getColumnDefinition (tooltip lookup) — unchanged by B6a', () => {
  it('still resolves subject tooltips', () => {
    expect(getColumnDefinition('subject', 'strainName')?.label).toBe('Strain');
  });

  it('still resolves combined aliases', () => {
    expect(getColumnDefinition('combined', 'strain')?.label).toBe('Strain');
  });
});
