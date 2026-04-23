/**
 * DatasetCard — wide-format catalog row.
 *
 * The card renders at full 1200px width (one per row on /datasets)
 * with a tag row, title, byline, a 5-column metadata strip (species
 * / region / docs / size / DOI), and a 2-line abstract clamp.
 *
 * The synthesized `summary` field is preferred over raw-record
 * fields when present, and the card gracefully degrades when the
 * synthesizer hasn't run (`summary === null`) or the backend is on
 * a pre-B2 deploy (`summary === undefined`).
 */
import { describe, expect, it } from 'vitest';
import { render, screen } from '@testing-library/react';
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

describe('DatasetCard — wide-format card', () => {
  it('renders the title, abstract, and license badge', () => {
    renderCard(baseDataset({ summary: compactSummary() }));
    expect(
      screen.getByRole('heading', { name: 'A Testing Dataset' }),
    ).toBeInTheDocument();
    expect(
      screen.getByText(/Experimental data from rats and mice\./i),
    ).toBeInTheDocument();
    expect(screen.getByText('CC-BY-4.0')).toBeInTheDocument();
  });

  it('renders the published status pill', () => {
    renderCard(baseDataset({ summary: compactSummary() }));
    // Unicode bullet (•) + "Published" — live text check avoids
    // relying on the internal Badge implementation.
    expect(screen.getByText(/Published/i)).toBeInTheDocument();
  });

  it('prefers summary.species over dataset.species in the Species cell', () => {
    renderCard(
      baseDataset({
        species: 'Mus musculus',
        summary: compactSummary({
          species: [{ label: 'Rattus norvegicus', ontologyId: 'NCBITaxon:10116' }],
        }),
      }),
    );
    // Summary value wins; raw record hidden.
    expect(screen.getByText('Rattus norvegicus')).toBeInTheDocument();
    expect(screen.queryByText('Mus musculus')).not.toBeInTheDocument();
  });

  it('falls back to dataset.species when no summary is attached', () => {
    renderCard(
      baseDataset({ species: 'Mus musculus', summary: null }),
    );
    expect(screen.getByText('Mus musculus')).toBeInTheDocument();
  });

  it('prefers summary.counts.totalDocuments over dataset.documentCount', () => {
    renderCard(
      baseDataset({
        documentCount: 123,
        summary: compactSummary({
          counts: { subjects: 5, totalDocuments: 999 },
        }),
      }),
    );
    // Synth count wins — 999 rendered, not 123.
    expect(screen.getByText('999')).toBeInTheDocument();
    expect(screen.queryByText('123')).not.toBeInTheDocument();
  });

  it('falls back to dataset.documentCount when summary is null', () => {
    renderCard(baseDataset({ summary: null }));
    expect(screen.getByText('123')).toBeInTheDocument();
  });

  it('falls back to dataset.documentCount when summary is undefined (pre-B2 backend)', () => {
    renderCard(baseDataset()); // no `summary` key at all
    expect(screen.getByText('123')).toBeInTheDocument();
  });

  it('surfaces the Subjects MetaCell only when summary.counts.subjects > 0', () => {
    // With subjects=5, Subjects row is present.
    const { rerender } = renderCard(
      baseDataset({
        summary: compactSummary({
          counts: { subjects: 5, totalDocuments: 120 },
        }),
      }),
    );
    expect(screen.getByText('Subjects')).toBeInTheDocument();
    expect(screen.getByText('5')).toBeInTheDocument();

    // With subjects=0, Subjects row is hidden.
    rerender(
      <MemoryRouter>
        <DatasetCard
          dataset={baseDataset({
            summary: compactSummary({
              counts: { subjects: 0, totalDocuments: 120 },
            }),
          })}
        />
      </MemoryRouter>,
    );
    expect(screen.queryByText('Subjects')).not.toBeInTheDocument();
  });

  it('renders the DOI MetaCell without the https:// prefix when present', () => {
    renderCard(
      baseDataset({
        doi: 'https://doi.org/10.63884/xyz',
      }),
    );
    // Prefix stripped; the mono bucket shows just the registrar path.
    expect(screen.getByText('doi.org/10.63884/xyz')).toBeInTheDocument();
  });

  it('shows em-dash placeholders when a MetaCell has no data', () => {
    renderCard(
      baseDataset({
        species: undefined,
        brainRegions: undefined,
        summary: null,
      }),
    );
    // At least two em-dashes (species + region cells both empty).
    const dashes = screen.getAllByText('—');
    expect(dashes.length).toBeGreaterThanOrEqual(2);
  });

  it('wraps the entire card in a single Link to the dataset detail page', () => {
    renderCard(baseDataset({ summary: compactSummary() }));
    const link = screen.getByRole('link', {
      name: /open dataset A Testing Dataset/i,
    });
    expect(link).toHaveAttribute('href', '/datasets/DS1');
  });
});
