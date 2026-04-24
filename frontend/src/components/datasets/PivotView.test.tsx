/**
 * PivotView — Plan B B6e.
 *
 * Covers:
 *   - Grain selector populated from DatasetSummary.counts (only grains with ≥1)
 *   - Table renders rows when pivot data arrives
 *   - Empty state when pivot returns zero rows
 *   - Feature-flag-off (503) hides via DatasetPivotNavGuard AND renders the
 *     disabled card when the main route is hit directly.
 */
import { describe, expect, it, vi } from 'vitest';
import { MemoryRouter, Route, Routes } from 'react-router-dom';
import { render, screen, within } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';

import { ApiError } from '@/api/errors';
import * as datasetsApi from '@/api/datasets';
import type { PivotResponse } from '@/api/datasets';
import type { DatasetSummary } from '@/types/dataset-summary';
import { DatasetPivotNavGuard, PivotView } from './PivotView';

// Audit 2026-04-23 (#63): PivotView now renders via VirtualizedTable.
// @tanstack/react-virtual returns zero items under jsdom because the
// scroll container has 0 height — same mock pattern as
// SummaryTableView.test.tsx so every row materializes and the cell
// expectations below keep working. Real virtualization is exercised
// by Playwright E2E.
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

function withProviders(
  ui: React.ReactNode,
  { path = '/datasets/DSX/pivot/subject' } = {},
) {
  const qc = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
  return (
    <QueryClientProvider client={qc}>
      <MemoryRouter initialEntries={[path]}>
        <Routes>
          <Route path="/datasets/:id/pivot/:grain" element={ui} />
        </Routes>
      </MemoryRouter>
    </QueryClientProvider>
  );
}

function baseSummary(
  overrides: Partial<DatasetSummary['counts']> = {},
): DatasetSummary {
  const counts = {
    sessions: 2,
    subjects: 3,
    probes: 1,
    elements: 1,
    epochs: 4,
    totalDocuments: 11,
    ...overrides,
  };
  return {
    datasetId: 'DSX',
    counts,
    species: null,
    strains: null,
    sexes: null,
    brainRegions: null,
    probeTypes: null,
    dateRange: { earliest: null, latest: null },
    totalSizeBytes: null,
    citation: {
      title: 'Test',
      license: null,
      datasetDoi: null,
      paperDois: [],
      contributors: [],
      year: null,
    },
    computedAt: new Date().toISOString(),
    schemaVersion: 'summary:v1',
    extractionWarnings: [],
  };
}

type PivotHookResult = ReturnType<typeof datasetsApi.useDatasetPivot>;
type SummaryHookResult = ReturnType<typeof datasetsApi.useDatasetSummary>;

function stubPivot(overrides: Partial<PivotHookResult> = {}): PivotHookResult {
  return {
    data: undefined,
    isLoading: false,
    isError: false,
    error: null,
    refetch: vi.fn(),
    ...overrides,
  } as PivotHookResult;
}

function stubSummary(
  overrides: Partial<SummaryHookResult> = {},
): SummaryHookResult {
  return {
    data: baseSummary(),
    isLoading: false,
    isError: false,
    error: null,
    refetch: vi.fn(),
    ...overrides,
  } as SummaryHookResult;
}

function pivotResponse(rows: Array<Record<string, unknown>>): PivotResponse {
  return {
    datasetId: 'DSX',
    grain: 'subject',
    columns: [
      { key: 'subjectDocumentIdentifier', label: 'Subject Doc ID' },
      { key: 'subjectLocalIdentifier', label: 'Local Identifier' },
      { key: 'speciesName', label: 'Species' },
      { key: 'strainName', label: 'Strain' },
      { key: 'biologicalSexName', label: 'Sex' },
    ],
    rows,
    computedAt: new Date().toISOString(),
    schemaVersion: 'pivot:v1',
    totalRows: rows.length,
  };
}

describe('PivotView — grain selector', () => {
  it('populates options for grains with count >= 1', () => {
    vi.spyOn(datasetsApi, 'useDatasetSummary').mockReturnValue(
      stubSummary({
        data: baseSummary({ subjects: 3, sessions: 1, elements: 0 }),
      }),
    );
    vi.spyOn(datasetsApi, 'useDatasetPivot').mockReturnValue(
      stubPivot({ data: pivotResponse([]) }),
    );

    render(withProviders(<PivotView />));
    const selector = screen.getByTestId('pivot-grain-selector');
    const subjectOpt = within(selector).getByTestId(
      'pivot-grain-option-subject',
    ) as HTMLOptionElement;
    const sessionOpt = within(selector).getByTestId(
      'pivot-grain-option-session',
    ) as HTMLOptionElement;
    const elementOpt = within(selector).getByTestId(
      'pivot-grain-option-element',
    ) as HTMLOptionElement;

    expect(subjectOpt.disabled).toBe(false);
    expect(sessionOpt.disabled).toBe(false);
    // Elements has zero count → option is disabled (greyed out with "(0)").
    expect(elementOpt.disabled).toBe(true);
    expect(elementOpt.textContent).toContain('(0)');
  });

  it('disables all grain options when summary is loading', () => {
    vi.spyOn(datasetsApi, 'useDatasetSummary').mockReturnValue(
      stubSummary({ data: undefined, isLoading: true }),
    );
    vi.spyOn(datasetsApi, 'useDatasetPivot').mockReturnValue(
      stubPivot({ isLoading: true }),
    );

    render(withProviders(<PivotView />));
    const selector = screen.getByTestId(
      'pivot-grain-selector',
    ) as HTMLElement;
    const select = within(selector).getByLabelText('Pivot grain');
    expect(select).toBeDisabled();
  });
});

describe('PivotView — table rendering', () => {
  it('renders the pivot table when rows are present', () => {
    vi.spyOn(datasetsApi, 'useDatasetSummary').mockReturnValue(stubSummary());
    vi.spyOn(datasetsApi, 'useDatasetPivot').mockReturnValue(
      stubPivot({
        data: pivotResponse([
          {
            subjectDocumentIdentifier: 'ndi-sub-A',
            subjectLocalIdentifier: 'A@lab.edu',
            speciesName: 'Caenorhabditis elegans',
            strainName: 'N2',
            biologicalSexName: 'hermaphrodite',
          },
        ]),
      }),
    );

    render(withProviders(<PivotView />));
    const table = screen.getByTestId('pivot-table');
    expect(within(table).getByText('Subject Doc ID')).toBeInTheDocument();
    expect(within(table).getByText('A@lab.edu')).toBeInTheDocument();
    expect(
      within(table).getByText('Caenorhabditis elegans'),
    ).toBeInTheDocument();
    expect(within(table).getByText('N2')).toBeInTheDocument();
  });

  it('renders em-dash for null cells (matches MATLAB blank-cell convention)', () => {
    vi.spyOn(datasetsApi, 'useDatasetSummary').mockReturnValue(stubSummary());
    vi.spyOn(datasetsApi, 'useDatasetPivot').mockReturnValue(
      stubPivot({
        data: pivotResponse([
          {
            subjectDocumentIdentifier: 'ndi-sub-A',
            subjectLocalIdentifier: 'A@lab.edu',
            speciesName: null,
            strainName: null,
            biologicalSexName: null,
          },
        ]),
      }),
    );

    render(withProviders(<PivotView />));
    const table = screen.getByTestId('pivot-table');
    const dashes = within(table).getAllByText('—');
    // 3 null columns for one row.
    expect(dashes.length).toBe(3);
  });

  it('renders an empty-state message when the pivot returns zero rows', () => {
    vi.spyOn(datasetsApi, 'useDatasetSummary').mockReturnValue(stubSummary());
    vi.spyOn(datasetsApi, 'useDatasetPivot').mockReturnValue(
      stubPivot({ data: pivotResponse([]) }),
    );

    render(withProviders(<PivotView />));
    expect(screen.getByTestId('pivot-empty')).toBeInTheDocument();
  });
});

describe('PivotView — feature-flag-off behavior', () => {
  it('renders the disabled card when the pivot endpoint returns 503', () => {
    vi.spyOn(datasetsApi, 'useDatasetSummary').mockReturnValue(stubSummary());
    vi.spyOn(datasetsApi, 'useDatasetPivot').mockReturnValue(
      stubPivot({
        isError: true,
        error: new ApiError(
          {
            code: 'INTERNAL',
            message:
              'Grain-selectable pivot is disabled. Set FEATURE_PIVOT_V1=true to enable.',
            recovery: 'contact_support',
            requestId: null,
          },
          503,
        ),
      }),
    );

    render(withProviders(<PivotView />));
    expect(screen.getByTestId('pivot-disabled')).toBeInTheDocument();
    // The table / selector do NOT render when the feature is disabled.
    expect(screen.queryByTestId('pivot-grain-selector')).toBeNull();
    expect(screen.queryByTestId('pivot-table')).toBeNull();
  });
});

describe('DatasetPivotNavGuard', () => {
  it('hides wrapped nav when the pivot endpoint returns 503', () => {
    vi.spyOn(datasetsApi, 'useDatasetPivot').mockReturnValue(
      stubPivot({
        isError: true,
        error: new ApiError(
          {
            code: 'INTERNAL',
            message: 'disabled',
            recovery: 'contact_support',
            requestId: null,
          },
          503,
        ),
      }),
    );

    const qc = new QueryClient({
      defaultOptions: { queries: { retry: false } },
    });
    render(
      <QueryClientProvider client={qc}>
        <MemoryRouter>
          <DatasetPivotNavGuard datasetId="DSX">
            <span data-testid="pivot-nav-link">Pivot nav</span>
          </DatasetPivotNavGuard>
        </MemoryRouter>
      </QueryClientProvider>,
    );
    expect(screen.queryByTestId('pivot-nav-link')).toBeNull();
  });

  it('shows wrapped nav when the pivot endpoint succeeds', () => {
    vi.spyOn(datasetsApi, 'useDatasetPivot').mockReturnValue(
      stubPivot({ data: pivotResponse([]) }),
    );

    const qc = new QueryClient({
      defaultOptions: { queries: { retry: false } },
    });
    render(
      <QueryClientProvider client={qc}>
        <MemoryRouter>
          <DatasetPivotNavGuard datasetId="DSX">
            <span data-testid="pivot-nav-link">Pivot nav</span>
          </DatasetPivotNavGuard>
        </MemoryRouter>
      </QueryClientProvider>,
    );
    expect(screen.getByTestId('pivot-nav-link')).toBeInTheDocument();
  });
});
