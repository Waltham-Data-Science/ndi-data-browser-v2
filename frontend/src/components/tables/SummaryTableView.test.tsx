/** Smoke test for SummaryTableView rendering logic — the cell renderer,
 * auto-hide-empty-columns, and global filter are the high-risk pieces.
 *
 * NOTE: @tanstack/react-virtual returns zero items under jsdom because
 * scroll containers have zero height. We stub getBoundingClientRect so
 * the virtualizer materializes rows during tests.
 */
import { beforeEach, describe, expect, it, vi } from 'vitest';
import { fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { BrowserRouter } from 'react-router-dom';
import { SummaryTableView } from './SummaryTableView';
import type { TableResponse } from '@/api/tables';

// Mock the dynamic `import('xlsx')` call in exportXlsx. We expose a
// single `writeFile` spy plus minimal `utils` stubs so the code under
// test executes without a real xlsx dependency.
const writeFileMock = vi.fn();
const aoaToSheetMock = vi.fn((aoa: unknown[][]) => ({ aoa }));
const bookNewMock = vi.fn(() => ({ SheetNames: [], Sheets: {} }));
const bookAppendSheetMock = vi.fn();
vi.mock('xlsx', () => ({
  writeFile: (...args: unknown[]) => writeFileMock(...args),
  utils: {
    aoa_to_sheet: (...args: unknown[]) => aoaToSheetMock(...(args as [unknown[][]])),
    book_new: () => bookNewMock(),
    book_append_sheet: (...args: unknown[]) => bookAppendSheetMock(...args),
  },
}));

// @tanstack/react-virtual returns zero items under jsdom because scroll
// container dimensions are 0. Stub it to expose every row so the component
// cell renderers run. Real virtualization is exercised by Playwright E2E.
vi.mock('@tanstack/react-virtual', () => {
  return {
    useVirtualizer: ({ count, estimateSize }: { count: number; estimateSize: () => number }) => {
      const size = estimateSize();
      const items = Array.from({ length: count }, (_, i) => ({
        index: i,
        key: i,
        start: i * size,
        end: (i + 1) * size,
        size,
        lane: 0,
      }));
      return {
        getVirtualItems: () => items,
        getTotalSize: () => count * size,
      };
    },
  };
});

function withProviders(ui: React.ReactNode) {
  const qc = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
  return (
    <QueryClientProvider client={qc}>
      <BrowserRouter>{ui}</BrowserRouter>
    </QueryClientProvider>
  );
}

const tutorialHaleyTable: TableResponse = {
  columns: [
    { key: 'subjectIdentifier', label: 'Subject Identifier' },
    { key: 'speciesName', label: 'Species' },
    { key: 'speciesOntology', label: 'Species Ontology' },
    { key: 'strainName', label: 'Strain' },
    { key: 'strainOntology', label: 'Strain Ontology' },
    { key: 'biologicalSexName', label: 'Sex' },
    { key: 'biologicalSexOntology', label: 'Sex Ontology' },
    { key: 'ageAtRecording', label: 'Age at Recording' },
    { key: 'description', label: 'Description' },
  ],
  rows: [
    {
      subjectIdentifier: 'PR811_4144@chalasani-lab.salk.edu',
      speciesName: 'Caenorhabditis elegans',
      speciesOntology: 'NCBITaxon:6239',
      strainName: 'N2',
      strainOntology: 'WBStrain:00000001',
      biologicalSexName: 'hermaphrodite',
      biologicalSexOntology: 'PATO:0001340',
      ageAtRecording: null, // empty across all rows — should auto-hide
      description: null,
    },
  ],
};

describe('SummaryTableView', () => {
  it('renders ontology cells as interactive popover buttons', () => {
    render(withProviders(<SummaryTableView data={tutorialHaleyTable} tableType="subject" />));
    // Ontology values render as popover buttons.
    expect(
      screen.getByRole('button', { name: /NCBITaxon:6239/ }),
    ).toBeInTheDocument();
    expect(
      screen.getByRole('button', { name: /WBStrain:00000001/ }),
    ).toBeInTheDocument();
    expect(
      screen.getByRole('button', { name: /PATO:0001340/ }),
    ).toBeInTheDocument();
  });

  it('renders the row count in the toolbar', () => {
    render(withProviders(<SummaryTableView data={tutorialHaleyTable} tableType="subject" />));
    // Row count appears once in the toolbar area (not inside column picker).
    expect(screen.getAllByText('1 / 1 rows').length).toBeGreaterThanOrEqual(1);
  });

  it('offers an auto-hide toggle for empty columns', () => {
    render(withProviders(<SummaryTableView data={tutorialHaleyTable} tableType="subject" />));
    // ageAtRecording + description are both all-null → 2 empty cols hidden.
    // The toggle text includes the count.
    expect(screen.getAllByText(/2 empty cols hidden/).length).toBeGreaterThanOrEqual(1);
  });

  it('tags ontology chips with data-ontology-term for e2e hooks', () => {
    const { container } = render(
      withProviders(<SummaryTableView data={tutorialHaleyTable} tableType="subject" />),
    );
    const tagged = container.querySelectorAll('[data-ontology-term]');
    expect(tagged.length).toBeGreaterThanOrEqual(3); // species + strain + sex
  });
});

describe('SummaryTableView XLS export', () => {
  beforeEach(() => {
    writeFileMock.mockClear();
    aoaToSheetMock.mockClear();
    bookNewMock.mockClear();
    bookAppendSheetMock.mockClear();
  });

  it('renders a dedicated XLS export button alongside CSV and JSON', () => {
    render(
      withProviders(
        <SummaryTableView
          data={tutorialHaleyTable}
          tableType="subject"
          title="haley-subjects"
        />,
      ),
    );
    expect(screen.getByTestId('export-csv')).toBeInTheDocument();
    expect(screen.getByTestId('export-xlsx')).toBeInTheDocument();
    expect(screen.getByTestId('export-json')).toBeInTheDocument();
  });

  it('invokes xlsx.writeFile with an .xlsx filename when XLS is clicked', async () => {
    render(
      withProviders(
        <SummaryTableView
          data={tutorialHaleyTable}
          tableType="subject"
          title="haley-subjects"
        />,
      ),
    );
    fireEvent.click(screen.getByTestId('export-xlsx'));
    // exportXlsx `await`s the dynamic import; waitFor polls until the
    // async work has landed.
    await waitFor(() => {
      expect(writeFileMock).toHaveBeenCalledTimes(1);
    });
    const [, filename] = writeFileMock.mock.calls[0] as [unknown, string];
    expect(filename).toBe('haley-subjects.xlsx');
    expect(aoaToSheetMock).toHaveBeenCalledTimes(1);
    expect(bookNewMock).toHaveBeenCalledTimes(1);
    expect(bookAppendSheetMock).toHaveBeenCalledTimes(1);
  });

  it('falls back to "table.xlsx" when no title is provided', async () => {
    render(
      withProviders(<SummaryTableView data={tutorialHaleyTable} tableType="subject" />),
    );
    fireEvent.click(screen.getByTestId('export-xlsx'));
    await waitFor(() => {
      expect(writeFileMock).toHaveBeenCalledTimes(1);
    });
    const [, filename] = writeFileMock.mock.calls[0] as [unknown, string];
    expect(filename).toBe('table.xlsx');
  });
});

// ─── B6a canonical column defaults ──────────────────────────────────────
// Fixture: Francesconi-tutorial-shaped subject row (Dabrowska lab). Exercises
// ordering + hidden-by-default + CSV-join on array cells + dynamic treatment-
// location discovery all in one shot.
const francesconiSubjectTable: TableResponse = {
  columns: [
    { key: 'subjectIdentifier', label: 'Subject Identifier' },
    { key: 'subjectLocalIdentifier', label: 'Local Identifier' },
    { key: 'subjectDocumentIdentifier', label: 'Subject Doc ID' },
    { key: 'sessionDocumentIdentifier', label: 'Session Doc ID' },
    { key: 'strainName', label: 'Strain' },
    { key: 'strainOntology', label: 'Strain Ontology' },
    { key: 'backgroundStrainName', label: 'Background Strain' },
    { key: 'backgroundStrainOntology', label: 'Background Strain Ontology' },
    { key: 'geneticStrainTypeName', label: 'Genetic Strain Type' },
    { key: 'speciesName', label: 'Species' },
    { key: 'speciesOntology', label: 'Species Ontology' },
    { key: 'biologicalSexName', label: 'Sex' },
    { key: 'biologicalSexOntology', label: 'Sex Ontology' },
    { key: 'ageAtRecording', label: 'Age at Recording' },
    { key: 'description', label: 'Description' },
    // Dynamic treatment column from the Dabrowska optogenetic-tetanus dataset
    { key: 'OptogeneticTetanusStimulationTargetLocationName', label: 'Optogenetic Tetanus Stimulation Target Location Name' },
  ],
  rows: [
    {
      subjectIdentifier: 'wi_rat_CRFCre_210818_BNST@dabrowska-lab.rosalindfranklin.edu',
      subjectLocalIdentifier: 'wi_rat_CRFCre_210818_BNST@dabrowska-lab.rosalindfranklin.edu',
      subjectDocumentIdentifier: '412693bb0b2a75c8_c0dc4139300a673e',
      sessionDocumentIdentifier: 'sess_abc123',
      // Multi-valued strain — expect CSV-join rendering
      strainName: ['CRF-Cre', 'OTR-IRES-Cre'],
      strainOntology: [],
      backgroundStrainName: 'WI',
      backgroundStrainOntology: 'RRID:RGD_13508588',
      geneticStrainTypeName: 'knockin',
      speciesName: 'Rattus norvegicus',
      speciesOntology: 'NCBITaxon:10116',
      biologicalSexName: 'male',
      biologicalSexOntology: 'PATO:0000384',
      ageAtRecording: null,
      description: null,
      OptogeneticTetanusStimulationTargetLocationName: 'BNST',
    },
  ],
};

/** Extract the visible label from each `<th>` — ignoring the tooltip text
 * that lives in a hidden sibling span. The label is the first `<span>`
 * inside the sort button; this shields us from the tooltip-description
 * string bleeding into `th.textContent`. */
function visibleHeaders(tableEl: HTMLElement): string[] {
  return Array.from(tableEl.querySelectorAll('thead th')).map((th) => {
    const labelSpan = th.querySelector('button span');
    return labelSpan?.textContent?.trim() ?? '';
  });
}

describe('SummaryTableView — B6a canonical column defaults (subject grain)', () => {
  it('hides sessionDocumentIdentifier by default', () => {
    render(withProviders(<SummaryTableView data={francesconiSubjectTable} tableType="subject" />));
    const tableEl = document.querySelector('table');
    if (!tableEl) throw new Error('no table rendered');
    const headers = visibleHeaders(tableEl as HTMLElement);
    expect(headers).not.toContain('Session Doc ID');
  });

  it('keeps sessionDocumentIdentifier available via the column picker', () => {
    const { container } = render(
      withProviders(<SummaryTableView data={francesconiSubjectTable} tableType="subject" />),
    );
    // Click the "Columns" toggle to reveal the picker (fireEvent goes
    // through React's synthetic-event path so the toggle state updates).
    const columnsBtn = screen.getByRole('button', { name: /Columns/i });
    fireEvent.click(columnsBtn);
    // The picker panel lives in a div with the column checkboxes. Look for
    // Session Doc ID as a checkbox label text (not a table header).
    const pickerLabels = Array.from(container.querySelectorAll('label'))
      .map((l) => l.textContent?.trim() ?? '');
    expect(pickerLabels.some((l) => l === 'Session Doc ID')).toBe(true);
  });

  it('renders the canonical headers in canonical order', () => {
    render(withProviders(<SummaryTableView data={francesconiSubjectTable} tableType="subject" />));
    const tableEl = document.querySelector('table');
    if (!tableEl) throw new Error('no table rendered');
    const headers = visibleHeaders(tableEl as HTMLElement);
    // Visible headers in order should start with the canonical 11 + the
    // dynamic treatment column. `sessionDocumentIdentifier` absent (hidden),
    // `ageAtRecording`/`description` absent (also hidden),
    // `subjectIdentifier` absent (hidden-by-default per canonical).
    expect(headers.slice(0, 3)).toEqual([
      'Subject Doc ID',
      'Local Identifier',
      'Strain',
    ]);
    expect(headers).not.toContain('Session Doc ID');
    expect(headers).not.toContain('Age at Recording');
    expect(headers).not.toContain('Description');
  });

  it('CSV-joins array cells in multi-valued columns', () => {
    render(withProviders(<SummaryTableView data={francesconiSubjectTable} tableType="subject" />));
    const tableEl = document.querySelector('table');
    if (!tableEl) throw new Error('no table rendered');
    // `strainName` was set to ['CRF-Cre', 'OTR-IRES-Cre'] — expect CSV-join.
    expect(within(tableEl).getByText('CRF-Cre, OTR-IRES-Cre')).toBeInTheDocument();
  });

  it('surfaces the discovered dynamic treatment column with a generated header', () => {
    render(withProviders(<SummaryTableView data={francesconiSubjectTable} tableType="subject" />));
    const tableEl = document.querySelector('table');
    if (!tableEl) throw new Error('no table rendered');
    const headers = visibleHeaders(tableEl as HTMLElement);
    // Header text comes from `prettyHeaderFromCamelCase` — space-separated words.
    expect(headers.some((h) => h.includes('Optogenetic Tetanus Stimulation Target Location'))).toBe(true);
  });
});

describe('SummaryTableView — B6a canonical column defaults (probe grain)', () => {
  const probeTable: TableResponse = {
    columns: [
      { key: 'probeDocumentIdentifier', label: 'Probe Doc ID' },
      { key: 'probeName', label: 'Name' },
      { key: 'probeType', label: 'Type' },
      { key: 'probeReference', label: 'Reference' },
      { key: 'probeLocationName', label: 'Probe Location' },
      { key: 'probeLocationOntology', label: 'Probe Location Ontology' },
      { key: 'cellTypeName', label: 'Cell Type' },
      { key: 'cellTypeOntology', label: 'Cell Type Ontology' },
      { key: 'subjectDocumentIdentifier', label: 'Subject Doc ID' },
    ],
    rows: [
      {
        probeDocumentIdentifier: '412693bb0bf99bbe_c0cb88b37570afba',
        probeName: 'Vm_210401_BNSTIII_a',
        probeType: 'patch-Vm',
        probeReference: '[1]',
        // Multi-valued location list demonstrates CSV-join
        probeLocationName: ['bed nucleus of stria terminalis', 'BNST'],
        probeLocationOntology: ['UBERON:0001880'],
        cellTypeName: 'Type III BNST neuron',
        cellTypeOntology: 'EMPTY:0000073',
        subjectDocumentIdentifier: '412693bb0b2cf772_c0d06cadbb168eb5',
      },
    ],
  };

  it('renders the 9 probe columns in canonical order', () => {
    render(withProviders(<SummaryTableView data={probeTable} tableType="element" />));
    const tableEl = document.querySelector('table');
    if (!tableEl) throw new Error('no table rendered');
    const headers = visibleHeaders(tableEl as HTMLElement);
    // Subject Doc ID first, then Probe Doc ID, then descriptors.
    expect(headers[0]).toBe('Subject Doc ID');
    expect(headers[1]).toBe('Probe Doc ID');
    expect(headers[2]).toBe('Name');
  });

  it('CSV-joins probeLocationName when multi-valued', () => {
    render(withProviders(<SummaryTableView data={probeTable} tableType="element" />));
    const tableEl = document.querySelector('table');
    if (!tableEl) throw new Error('no table rendered');
    expect(within(tableEl).getByText('bed nucleus of stria terminalis, BNST')).toBeInTheDocument();
  });
});

describe('SummaryTableView cell rendering', () => {
  const dualClockTable: TableResponse = {
    columns: [
      { key: 'epochNumber', label: 'Epoch' },
      { key: 'epochStart', label: 'Start' },
      { key: 'epochStop', label: 'Stop' },
    ],
    rows: [
      {
        epochNumber: 't00001',
        epochStart: { devTime: 0, globalTime: 739256.7 },
        epochStop: { devTime: 3600, globalTime: 739256.75 },
      },
    ],
  };

  it('renders {devTime, globalTime} structured epoch values', () => {
    const { container } = render(
      withProviders(<SummaryTableView data={dualClockTable} tableType="element_epoch" />),
    );
    const tableEl = container.querySelector('table');
    if (!tableEl) throw new Error('no table rendered');
    const tableWithin = within(tableEl);
    // Dev times (0 and 3600) render on the first line per cell.
    expect(tableWithin.getByText('0')).toBeInTheDocument();
    expect(tableWithin.getByText('3600')).toBeInTheDocument();
    // Global times render on the second line.
    expect(tableWithin.getByText('739256.7')).toBeInTheDocument();
    expect(tableWithin.getByText('739256.75')).toBeInTheDocument();
  });
});
