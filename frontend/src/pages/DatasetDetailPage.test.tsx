/**
 * DatasetDetailPage integration smoke — verifies that the Cite and
 * Use-this-data buttons mount and open their respective modals when
 * the page receives dataset + summary data. This is the integration
 * required by amendment §4.B4 tests: mount both modals on the detail
 * page and render-smoke-check the triggers.
 */
import { describe, expect, it, vi } from 'vitest';
import { act, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { MemoryRouter, Route, Routes } from 'react-router-dom';

import type { DatasetRecord, ClassCountsResponse } from '@/api/datasets';
import type { DatasetSummary } from '@/types/dataset-summary';

// Mock apiFetch up-front so the hooks running inside DatasetDetailPage
// resolve synchronously (from React's POV) without touching the
// network. Endpoints resolve by URL prefix.
vi.mock('@/api/client', () => {
  const DATASET_ID = 'ds-test-1';
  const ds: DatasetRecord = {
    id: DATASET_ID,
    name: 'Integration test dataset',
    description: 'A test dataset used by the detail-page integration suite.',
    doi: 'https://doi.org/10.63884/test',
    license: 'CC-BY-4.0',
    createdAt: '2026-01-02T00:00:00Z',
    updatedAt: '2026-01-03T00:00:00Z',
    associatedPublications: [],
  };
  const summary: DatasetSummary = {
    datasetId: DATASET_ID,
    counts: {
      sessions: 1,
      subjects: 2,
      probes: 3,
      elements: 4,
      epochs: 5,
      totalDocuments: 15,
    },
    species: [{ label: 'Rattus norvegicus', ontologyId: 'NCBITaxon:10116' }],
    strains: [],
    sexes: [],
    brainRegions: [],
    probeTypes: [],
    dateRange: { earliest: null, latest: null },
    totalSizeBytes: null,
    citation: {
      title: 'Integration test dataset',
      license: 'CC-BY-4.0',
      datasetDoi: 'https://doi.org/10.63884/test',
      paperDois: [],
      contributors: [{ firstName: 'Ada', lastName: 'Lovelace', orcid: null }],
      year: 2026,
    },
    computedAt: new Date().toISOString(),
    schemaVersion: 'summary:v1',
    extractionWarnings: [],
  };
  const classCounts: ClassCountsResponse = {
    datasetId: DATASET_ID,
    totalDocuments: 15,
    classCounts: { subject: 2, element: 3, element_epoch: 4 },
  };
  return {
    apiFetch: vi.fn(async (path: string) => {
      if (path.endsWith('/summary')) return summary;
      if (path.endsWith('/class-counts')) return classCounts;
      if (/\/api\/datasets\/[^/]+$/.test(path)) return ds;
      throw new Error(`Unexpected apiFetch path: ${path}`);
    }),
  };
});

// DatasetDetailPage imports DatasetDetailPage via the normal module.
// The Cite + Use-this-data buttons live in the Overview tab now, so we
// mount the full nested route tree and land the MemoryRouter on
// /datasets/ds-test-1/overview directly (skips the index redirect,
// which MemoryRouter would still resolve correctly but makes the test
// read cleaner).
import { DatasetDetailPage, OverviewTab } from './DatasetDetailPage';
import { Navigate } from 'react-router-dom';

function mount() {
  const qc = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
  Object.defineProperty(navigator, 'clipboard', {
    value: { writeText: vi.fn().mockResolvedValue(undefined) },
    configurable: true,
  });
  return render(
    <QueryClientProvider client={qc}>
      <MemoryRouter initialEntries={['/datasets/ds-test-1/overview']}>
        <Routes>
          <Route path="/datasets/:id" element={<DatasetDetailPage />}>
            <Route index element={<Navigate to="overview" replace />} />
            <Route path="overview" element={<OverviewTab />} />
          </Route>
        </Routes>
      </MemoryRouter>
    </QueryClientProvider>,
  );
}

describe('DatasetDetailPage — Cite + Use-this-data affordances', () => {
  it('renders both action buttons once the dataset loads', async () => {
    mount();
    // Wait for the hooks to resolve and render.
    await waitFor(() =>
      expect(screen.getByTestId('dataset-actions')).toBeInTheDocument(),
    );
    expect(screen.getByTestId('open-cite-modal')).toBeInTheDocument();
    expect(screen.getByTestId('open-use-data-modal')).toBeInTheDocument();
  });

  it('clicking Cite opens the CiteModal with BibTeX, RIS, and plain-text blocks', async () => {
    mount();
    await waitFor(() =>
      expect(screen.getByTestId('open-cite-modal')).toBeInTheDocument(),
    );
    await act(async () => {
      fireEvent.click(screen.getByTestId('open-cite-modal'));
    });
    expect(screen.getByTestId('cite-modal-body')).toBeInTheDocument();
    expect(screen.getByTestId('cite-bibtex')).toBeInTheDocument();
    expect(screen.getByTestId('cite-ris')).toBeInTheDocument();
    expect(screen.getByTestId('cite-plain')).toBeInTheDocument();
  });

  it('clicking Use this data opens the UseThisDataModal with the Python tab active', async () => {
    mount();
    await waitFor(() =>
      expect(screen.getByTestId('open-use-data-modal')).toBeInTheDocument(),
    );
    await act(async () => {
      fireEvent.click(screen.getByTestId('open-use-data-modal'));
    });
    expect(screen.getByTestId('use-data-modal-body')).toBeInTheDocument();
    expect(screen.getByTestId('snippet-python-content')).toBeInTheDocument();
    expect(
      screen
        .getByTestId('snippet-python-content')
        .textContent,
    ).toContain('downloadDataset("ds-test-1"');
  });
});
