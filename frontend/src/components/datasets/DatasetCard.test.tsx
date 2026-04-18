/**
 * DatasetCard — Plan B B2 behavior: renders compact-summary pills +
 * subject count when the backend attaches ``summary``, and falls back to
 * raw-record fields when it's ``null`` or ``undefined``.
 */
import { describe, expect, it } from 'vitest';
import { render, screen, within } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';

import type { DatasetRecord } from '@/api/datasets';
import type { CompactDatasetSummary } from '@/types/dataset-summary';

import { DatasetCard } from './DatasetCard';

function baseDataset(overrides: Partial<DatasetRecord> = {}): DatasetRecord {
  return {
    id: 'DS1',
    name: 'A Testing Dataset',
    abstract: 'Experimental data from rats and mice.',
    license: 'CC-BY-4.0',
    organizationId: 'org-abc-123',
    createdAt: '2025-06-01T00:00:00.000Z',
    updatedAt: '2026-03-01T00:00:00.000Z',
    documentCount: 123,
    ...overrides,
  };
}

function compactSummary(
  overrides: Partial<CompactDatasetSummary> = {},
): CompactDatasetSummary {
  return {
    datasetId: 'DS1',
    counts: { subjects: 5, totalDocuments: 120 },
    species: [{ label: 'Rattus norvegicus', ontologyId: 'NCBITaxon:10116' }],
    brainRegions: [
      { label: 'primary visual cortex', ontologyId: 'UBERON:0002436' },
    ],
    citation: {
      title: 'A Testing Dataset',
      license: 'CC-BY-4.0',
      datasetDoi: 'https://doi.org/10.63884/xyz',
      year: 2025,
    },
    schemaVersion: 'summary:v1',
    ...overrides,
  };
}

function renderCard(dataset: DatasetRecord) {
  return render(
    <MemoryRouter>
      <DatasetCard dataset={dataset} />
    </MemoryRouter>,
  );
}

describe('DatasetCard — compact summary', () => {
  it('renders species pills, brain-region pills, and subject count when summary is present', () => {
    renderCard(baseDataset({ summary: compactSummary() }));

    const summarySection = screen.getByTestId('dataset-card-summary');
    expect(summarySection).toBeInTheDocument();

    const species = within(summarySection).getByTestId(
      'dataset-card-summary-species',
    );
    expect(within(species).getByText('Rattus norvegicus')).toBeInTheDocument();

    const regions = within(summarySection).getByTestId(
      'dataset-card-summary-brain-regions',
    );
    expect(
      within(regions).getByText('primary visual cortex'),
    ).toBeInTheDocument();

    expect(
      within(summarySection).getByTestId('dataset-card-summary-subjects'),
    ).toHaveTextContent('5 subjects');
  });

  it('prefers summary.counts.totalDocuments over dataset.documentCount for the docs count chip', () => {
    renderCard(
      baseDataset({
        documentCount: 123,
        summary: compactSummary({
          counts: { subjects: 5, totalDocuments: 999 },
        }),
      }),
    );
    // Synth count wins — 999 docs rendered, not 123.
    expect(screen.getByText(/999 docs/i)).toBeInTheDocument();
    expect(screen.queryByText(/123 docs/i)).not.toBeInTheDocument();
  });

  it('falls back to raw-record rendering when summary is null', () => {
    renderCard(baseDataset({ summary: null }));

    // Compact section NOT rendered.
    expect(
      screen.queryByTestId('dataset-card-summary'),
    ).not.toBeInTheDocument();

    // Raw-record docs count still rendered.
    expect(screen.getByText(/123 docs/i)).toBeInTheDocument();
    // License badge still shown.
    expect(screen.getByText('CC-BY-4.0')).toBeInTheDocument();
  });

  it('falls back to raw-record rendering when summary is undefined (pre-B2 backend)', () => {
    renderCard(baseDataset());  // no `summary` key at all

    expect(
      screen.queryByTestId('dataset-card-summary'),
    ).not.toBeInTheDocument();
    expect(screen.getByText(/123 docs/i)).toBeInTheDocument();
  });

  it('omits the summary section entirely when all facts are empty/zero', () => {
    renderCard(
      baseDataset({
        summary: compactSummary({
          species: [],
          brainRegions: [],
          counts: { subjects: 0, totalDocuments: 0 },
        }),
      }),
    );

    // Empty summary → no summary section rendered.
    expect(
      screen.queryByTestId('dataset-card-summary'),
    ).not.toBeInTheDocument();
    // But the raw card still renders.
    expect(screen.getByText('A Testing Dataset')).toBeInTheDocument();
  });

  it('omits the summary section when species and regions are null and subjects=0', () => {
    renderCard(
      baseDataset({
        summary: compactSummary({
          species: null,
          brainRegions: null,
          counts: { subjects: 0, totalDocuments: 50 },
        }),
      }),
    );
    expect(
      screen.queryByTestId('dataset-card-summary'),
    ).not.toBeInTheDocument();
  });

  it('shows subject count even when species / regions are unavailable', () => {
    renderCard(
      baseDataset({
        summary: compactSummary({
          species: null,
          brainRegions: null,
          counts: { subjects: 7, totalDocuments: 140 },
        }),
      }),
    );
    const subjects = screen.getByTestId('dataset-card-summary-subjects');
    expect(subjects).toHaveTextContent('7 subjects');
    // Species / region rows are absent.
    expect(
      screen.queryByTestId('dataset-card-summary-species'),
    ).not.toBeInTheDocument();
    expect(
      screen.queryByTestId('dataset-card-summary-brain-regions'),
    ).not.toBeInTheDocument();
  });

  it('caps pill rows at 3 items and shows an "+N" overflow count', () => {
    const manySpecies = Array.from({ length: 5 }, (_, i) => ({
      label: `Species ${i + 1}`,
      ontologyId: `NCBITaxon:${10000 + i}`,
    }));
    renderCard(
      baseDataset({
        summary: compactSummary({ species: manySpecies }),
      }),
    );
    const species = screen.getByTestId('dataset-card-summary-species');
    // First 3 rendered as pills.
    expect(within(species).getByText('Species 1')).toBeInTheDocument();
    expect(within(species).getByText('Species 2')).toBeInTheDocument();
    expect(within(species).getByText('Species 3')).toBeInTheDocument();
    // 4 and 5 hidden behind overflow badge.
    expect(within(species).queryByText('Species 4')).not.toBeInTheDocument();
    expect(
      within(species).getByTestId('dataset-card-summary-species-overflow'),
    ).toHaveTextContent('+2');
  });

  it('renders OntologyTermPill WITHOUT a resolver link inside the card (nested <a> is invalid HTML)', () => {
    renderCard(baseDataset({ summary: compactSummary() }));
    // The pills still render their label content with ontologyId data
    // attribute for tooltip hover…
    const pills = screen.getAllByTestId('ontology-term-pill');
    expect(pills.length).toBeGreaterThan(0);
    // …but the resolver anchor is suppressed. The whole card is a
    // single <Link>, and nested <a> would fail HTML validation. Users
    // reach the ontology resolver from the detail-page pill instead.
    expect(screen.queryAllByTestId('ontology-term-link')).toHaveLength(0);
  });

  it('preserves card link target to the dataset detail page', () => {
    renderCard(baseDataset({ summary: compactSummary() }));
    const link = screen.getByRole('link', {
      name: /open dataset A Testing Dataset/i,
    });
    expect(link).toHaveAttribute('href', '/datasets/DS1');
  });
});
