/**
 * DatasetSummaryCard — covers the rendering contract for each section,
 * the `[]` vs `null` UI differentiation, the ontology-term pill + tooltip
 * + resolver link, and the warnings debug tooltip.
 */
import { describe, expect, it } from 'vitest';
import { fireEvent, render, screen, within } from '@testing-library/react';

import {
  DatasetSummaryCard,
  OntologyTermPill,
  resolverUrl,
} from './DatasetSummaryCard';
import type { DatasetSummary } from '@/types/dataset-summary';

function baseSummary(overrides: Partial<DatasetSummary> = {}): DatasetSummary {
  return {
    datasetId: 'DSX',
    counts: {
      sessions: 3,
      subjects: 5,
      probes: 7,
      elements: 9,
      epochs: 11,
      totalDocuments: 35,
    },
    species: [{ label: 'Rattus norvegicus', ontologyId: 'NCBITaxon:10116' }],
    strains: [{ label: 'N2', ontologyId: 'WBStrain:00000001' }],
    sexes: [{ label: 'female', ontologyId: 'PATO:0000383' }],
    brainRegions: [{ label: 'primary visual cortex', ontologyId: 'UBERON:0002436' }],
    probeTypes: ['n-trode', 'tetrode'],
    dateRange: {
      earliest: '2025-06-01T00:00:00Z',
      latest: '2026-02-01T00:00:00Z',
    },
    totalSizeBytes: 10_485_760,
    citation: {
      title: 'A Testing Dataset',
      license: 'CC-BY-4.0',
      datasetDoi: 'https://doi.org/10.63884/xyz',
      paperDois: ['https://doi.org/10.1/abc'],
      contributors: [
        {
          firstName: 'Ada',
          lastName: 'Lovelace',
          orcid: 'https://orcid.org/0000-0001',
        },
      ],
      year: 2025,
    },
    computedAt: new Date(Date.now() - 5 * 60 * 1000).toISOString(),
    schemaVersion: 'summary:v1',
    extractionWarnings: [],
    ...overrides,
  };
}

describe('DatasetSummaryCard — sections', () => {
  it('renders every count chip with the formatted value', () => {
    render(<DatasetSummaryCard summary={baseSummary()} />);
    const counts = screen.getByTestId('dataset-summary-counts');
    expect(within(counts).getByTestId('counts-sessions')).toHaveTextContent('3');
    expect(within(counts).getByTestId('counts-subjects')).toHaveTextContent('5');
    expect(within(counts).getByTestId('counts-probes')).toHaveTextContent('7');
    expect(within(counts).getByTestId('counts-elements')).toHaveTextContent('9');
    expect(within(counts).getByTestId('counts-epochs')).toHaveTextContent('11');
    expect(within(counts).getByTestId('counts-total-documents')).toHaveTextContent(
      '35',
    );
  });

  it('renders biology, anatomy, probe-types, scale and citation sections', () => {
    render(<DatasetSummaryCard summary={baseSummary()} />);
    expect(screen.getByTestId('biology')).toBeInTheDocument();
    expect(screen.getByTestId('anatomy')).toBeInTheDocument();
    expect(screen.getByTestId('probe-types')).toBeInTheDocument();
    expect(screen.getByTestId('scale')).toBeInTheDocument();
    expect(screen.getByTestId('citation')).toBeInTheDocument();
    expect(screen.getByTestId('citation-title')).toHaveTextContent(
      'A Testing Dataset',
    );
    expect(screen.getByTestId('citation-license')).toHaveTextContent('CC-BY-4.0');
    expect(screen.getByTestId('citation-year')).toHaveTextContent('2025');
    // Probe types render as badges preserving the full strings.
    expect(screen.getByText('n-trode')).toBeInTheDocument();
    expect(screen.getByText('tetrode')).toBeInTheDocument();
  });

  it('renders a dataset DOI link and paper DOI links', () => {
    render(<DatasetSummaryCard summary={baseSummary()} />);
    const datasetDoi = screen.getByTestId('citation-dataset-doi');
    const datasetLink = within(datasetDoi).getByRole('link', {
      name: /10.63884/,
    });
    expect(datasetLink.getAttribute('href')).toBe(
      'https://doi.org/10.63884/xyz',
    );
    expect(datasetLink.getAttribute('target')).toBe('_blank');
    expect(datasetLink.getAttribute('rel')).toBe('noopener noreferrer');

    const paperDois = screen.getByTestId('citation-paper-dois');
    expect(
      within(paperDois).getByRole('link', { name: /10.1\/abc/ }),
    ).toBeInTheDocument();
  });

  it('shows contributor ORCID links', () => {
    render(<DatasetSummaryCard summary={baseSummary()} />);
    const contributors = screen.getByTestId('citation-contributors');
    expect(within(contributors).getByText('Ada Lovelace')).toBeInTheDocument();
    const orcid = within(contributors).getByRole('link', { name: /ORCID/ });
    expect(orcid.getAttribute('href')).toBe('https://orcid.org/0000-0001');
  });
});

describe('DatasetSummaryCard — null vs [] distinction', () => {
  it('renders "Not applicable" for null (extraction did not run)', () => {
    render(
      <DatasetSummaryCard
        summary={baseSummary({ species: null, strains: null, sexes: null })}
      />,
    );
    const biology = screen.getByTestId('biology');
    // Each of species/strains/sex is independently null.
    expect(
      within(biology).getAllByTestId('value-not-applicable'),
    ).toHaveLength(3);
  });

  it('renders em-dash for [] (fact genuinely absent)', () => {
    render(
      <DatasetSummaryCard
        summary={baseSummary({ species: [], strains: [], sexes: [] })}
      />,
    );
    const biology = screen.getByTestId('biology');
    expect(within(biology).getAllByTestId('value-empty')).toHaveLength(3);
  });

  it('distinguishes probeTypes null from probeTypes []', () => {
    const { rerender } = render(
      <DatasetSummaryCard summary={baseSummary({ probeTypes: null })} />,
    );
    expect(
      within(screen.getByTestId('probe-types')).getByTestId(
        'value-not-applicable',
      ),
    ).toBeInTheDocument();

    rerender(<DatasetSummaryCard summary={baseSummary({ probeTypes: [] })} />);
    expect(
      within(screen.getByTestId('probe-types')).getByTestId('value-empty'),
    ).toBeInTheDocument();
  });
});

describe('OntologyTermPill', () => {
  it('wraps the label in an anchor targeting the OBO resolver URL', () => {
    render(
      <OntologyTermPill
        term={{ label: 'primary visual cortex', ontologyId: 'UBERON:0002436' }}
      />,
    );
    const link = screen.getByTestId('ontology-term-link');
    expect(link.getAttribute('href')).toBe(
      'http://purl.obolibrary.org/obo/UBERON_0002436',
    );
    expect(link.getAttribute('target')).toBe('_blank');
    expect(link.getAttribute('rel')).toBe('noopener noreferrer');
  });

  it('renders a label-only pill (no anchor) when ontologyId is null', () => {
    render(
      <OntologyTermPill
        term={{ label: 'unknown region', ontologyId: null }}
      />,
    );
    expect(screen.queryByTestId('ontology-term-link')).toBeNull();
    expect(screen.getByTestId('ontology-term-pill')).toHaveTextContent(
      'unknown region',
    );
  });

  it('carries the ontology id in a data-attribute for tooltip hookup', () => {
    const { container } = render(
      <OntologyTermPill
        term={{ label: 'N2', ontologyId: 'WBStrain:00000001' }}
      />,
    );
    const wrapper = container.querySelector('[data-ontology-id]');
    expect(wrapper).not.toBeNull();
    expect(wrapper?.getAttribute('data-ontology-id')).toBe('WBStrain:00000001');
  });
});

describe('resolverUrl', () => {
  it.each([
    ['NCBITaxon:10116', 'http://purl.obolibrary.org/obo/NCBITAXON_10116'],
    ['UBERON:0002436', 'http://purl.obolibrary.org/obo/UBERON_0002436'],
    ['CL:0000598', 'http://purl.obolibrary.org/obo/CL_0000598'],
    ['CHEBI:73328', 'http://purl.obolibrary.org/obo/CHEBI_73328'],
    ['PATO:0000383', 'http://purl.obolibrary.org/obo/PATO_0000383'],
    ['RRID:RGD_70508', 'https://scicrunch.org/resolver/RRID:RGD_70508'],
    ['WBStrain:00000001', 'https://wormbase.org/species/c_elegans/strain/00000001'],
    ['PubChem:5280343', 'https://pubchem.ncbi.nlm.nih.gov/compound/5280343'],
  ])('builds the canonical resolver URL for %s', (id, expected) => {
    expect(resolverUrl(id)).toBe(expected);
  });

  it('returns null for IDs without a recognized provider', () => {
    expect(resolverUrl('nonsense:id')).toBeNull();
  });

  it('returns null for malformed IDs (no colon)', () => {
    expect(resolverUrl('NCBITaxon10116')).toBeNull();
  });
});

describe('DatasetSummaryCard — warnings footer', () => {
  it('does not show the warnings button when the list is empty', () => {
    render(<DatasetSummaryCard summary={baseSummary()} />);
    expect(
      screen.queryByTestId('summary-warnings-toggle'),
    ).not.toBeInTheDocument();
  });

  it('reveals the warnings tooltip when the user toggles it', () => {
    render(
      <DatasetSummaryCard
        summary={baseSummary({
          extractionWarnings: [
            'species extraction: at least one subject reported a Species name without an ontology identifier; fell back to label-only.',
            'brainRegions extraction: at least one probe_location had a name but no ontology_name; included as label-only.',
          ],
        })}
      />,
    );
    const toggle = screen.getByTestId('summary-warnings-toggle');
    expect(toggle).toHaveAttribute('aria-expanded', 'false');
    fireEvent.click(toggle);
    expect(toggle).toHaveAttribute('aria-expanded', 'true');
    const tooltip = screen.getByTestId('summary-warnings-tooltip');
    const items = within(tooltip).getAllByTestId('summary-warning');
    expect(items).toHaveLength(2);
    expect(items[0]).toHaveTextContent('species extraction');
  });
});

describe('DatasetSummaryCard — full-string preservation', () => {
  it('never truncates the long SubjectLocalIdentifier-style probe-type', () => {
    const long =
      'sd_rat_OTRCre_220819_175@dabrowska-lab.rosalindfranklin.edu';
    render(
      <DatasetSummaryCard summary={baseSummary({ probeTypes: [long] })} />,
    );
    expect(screen.getByText(long)).toBeInTheDocument();
  });

  it('shows computed-at footer text', () => {
    render(<DatasetSummaryCard summary={baseSummary()} />);
    const computedAt = screen.getByTestId('summary-computed-at');
    // default baseSummary sets computedAt 5m ago
    expect(computedAt.textContent).toMatch(/ago|just now/);
  });
});
