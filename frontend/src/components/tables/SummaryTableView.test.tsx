/** Smoke test for SummaryTableView rendering logic — the cell renderer,
 * auto-hide-empty-columns, and global filter are the high-risk pieces.
 *
 * NOTE: @tanstack/react-virtual returns zero items under jsdom because
 * scroll containers have zero height. We stub getBoundingClientRect so
 * the virtualizer materializes rows during tests.
 */
import { describe, expect, it, vi } from 'vitest';
import { render, screen, within } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { BrowserRouter } from 'react-router-dom';
import { SummaryTableView } from './SummaryTableView';
import type { TableResponse } from '@/api/tables';

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
