/**
 * FacetPanel — Plan B B3 tests.
 *
 * Covers:
 *  - loading / error states
 *  - renders one chip per term across species/brainRegions/strains/sexes
 *  - probeTypes render as free-text outlines
 *  - click handlers fire with the right arguments (kind + OntologyTerm /
 *    probeType)
 *  - empty lists hide the section entirely
 */
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import { FacetPanel } from './FacetPanel';
import type { FacetsResponse } from '@/types/facets';

vi.mock('@/api/client', () => ({
  apiFetch: vi.fn(),
}));

import { apiFetch } from '@/api/client';

function makeFacets(overrides: Partial<FacetsResponse> = {}): FacetsResponse {
  return {
    species: [
      { label: 'Rattus norvegicus', ontologyId: 'NCBITaxon:10116' },
      { label: 'Mus musculus', ontologyId: 'NCBITaxon:10090' },
    ],
    brainRegions: [
      { label: 'primary visual cortex', ontologyId: 'UBERON:0002436' },
    ],
    strains: [{ label: 'N2', ontologyId: 'WBStrain:00000001' }],
    sexes: [{ label: 'male', ontologyId: 'PATO:0000384' }],
    probeTypes: ['patch-Vm', 'stimulator'],
    // `licenses` field is additive (added in the facet-dedupe + license-
    // normalization PR). Default empty here — individual tests that need
    // license-specific behavior override via the `overrides` arg.
    licenses: [],
    datasetCount: 7,
    computedAt: '2026-04-17T00:00:00Z',
    schemaVersion: 'facets:v1',
    ...overrides,
  };
}

function renderWithClient(node: React.ReactNode) {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
  return render(
    <QueryClientProvider client={client}>{node}</QueryClientProvider>,
  );
}

describe('FacetPanel', () => {
  beforeEach(() => {
    vi.mocked(apiFetch).mockReset();
  });
  afterEach(() => {
    vi.mocked(apiFetch).mockReset();
  });

  it('shows the loading state while the query is pending', async () => {
    vi.mocked(apiFetch).mockImplementation(
      () => new Promise(() => {}) as Promise<never>,
    );
    renderWithClient(
      <FacetPanel
        onSelectOntologyFacet={vi.fn()}
        onSelectProbeType={vi.fn()}
      />,
    );
    expect(screen.getByText(/Loading facets/)).toBeInTheDocument();
  });

  it('renders error state when the fetch rejects', async () => {
    vi.mocked(apiFetch).mockRejectedValueOnce(new Error('boom'));
    renderWithClient(
      <FacetPanel
        onSelectOntologyFacet={vi.fn()}
        onSelectProbeType={vi.fn()}
      />,
    );
    await waitFor(() => {
      expect(
        screen.getByText(/Couldn.+load research facets/i),
      ).toBeInTheDocument();
    });
  });

  it('renders one chip per term across each ontology facet list', async () => {
    vi.mocked(apiFetch).mockResolvedValueOnce(makeFacets());
    renderWithClient(
      <FacetPanel
        onSelectOntologyFacet={vi.fn()}
        onSelectProbeType={vi.fn()}
      />,
    );
    await waitFor(() => {
      expect(screen.getByText('Species')).toBeInTheDocument();
    });
    expect(screen.getByText('Rattus norvegicus')).toBeInTheDocument();
    expect(screen.getByText('Mus musculus')).toBeInTheDocument();
    expect(screen.getByText('primary visual cortex')).toBeInTheDocument();
    expect(screen.getByText('N2')).toBeInTheDocument();
    expect(screen.getByText('male')).toBeInTheDocument();
  });

  it('renders probeTypes as free-text chips', async () => {
    vi.mocked(apiFetch).mockResolvedValueOnce(makeFacets());
    renderWithClient(
      <FacetPanel
        onSelectOntologyFacet={vi.fn()}
        onSelectProbeType={vi.fn()}
      />,
    );
    await waitFor(() => {
      expect(screen.getByText('Probe types')).toBeInTheDocument();
    });
    expect(screen.getByText('patch-Vm')).toBeInTheDocument();
    expect(screen.getByText('stimulator')).toBeInTheDocument();
  });

  it('includes datasetCount in the header', async () => {
    vi.mocked(apiFetch).mockResolvedValueOnce(makeFacets({ datasetCount: 42 }));
    renderWithClient(
      <FacetPanel
        onSelectOntologyFacet={vi.fn()}
        onSelectProbeType={vi.fn()}
      />,
    );
    await waitFor(() => {
      expect(screen.getByText(/42 datasets/)).toBeInTheDocument();
    });
  });

  it('fires onSelectOntologyFacet with kind + term on click', async () => {
    const onOntology = vi.fn();
    vi.mocked(apiFetch).mockResolvedValueOnce(makeFacets());
    renderWithClient(
      <FacetPanel
        onSelectOntologyFacet={onOntology}
        onSelectProbeType={vi.fn()}
      />,
    );
    await waitFor(() => {
      expect(screen.getByText('Rattus norvegicus')).toBeInTheDocument();
    });
    fireEvent.click(screen.getByText('Rattus norvegicus'));
    expect(onOntology).toHaveBeenCalledWith('species', {
      label: 'Rattus norvegicus',
      ontologyId: 'NCBITaxon:10116',
    });
  });

  it('fires onSelectProbeType with the free-text label on click', async () => {
    const onProbeType = vi.fn();
    vi.mocked(apiFetch).mockResolvedValueOnce(makeFacets());
    renderWithClient(
      <FacetPanel
        onSelectOntologyFacet={vi.fn()}
        onSelectProbeType={onProbeType}
      />,
    );
    await waitFor(() => {
      expect(screen.getByText('patch-Vm')).toBeInTheDocument();
    });
    fireEvent.click(screen.getByText('patch-Vm'));
    expect(onProbeType).toHaveBeenCalledWith('patch-Vm');
  });

  it('hides an empty facet section entirely', async () => {
    vi.mocked(apiFetch).mockResolvedValueOnce(
      makeFacets({ sexes: [], probeTypes: [] }),
    );
    renderWithClient(
      <FacetPanel
        onSelectOntologyFacet={vi.fn()}
        onSelectProbeType={vi.fn()}
      />,
    );
    await waitFor(() => {
      expect(screen.getByText('Species')).toBeInTheDocument();
    });
    expect(screen.queryByText('Sex')).not.toBeInTheDocument();
    expect(screen.queryByText('Probe types')).not.toBeInTheDocument();
  });
});
