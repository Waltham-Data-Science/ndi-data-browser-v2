/**
 * DatasetProvenanceCard — Plan B B5 tests.
 *
 * Covers:
 *  - Each empty state ("Not a branch", "No branches", "No cross-dataset
 *    dependencies").
 *  - Branch-of parent link + branch chip links (all routed via <Link>).
 *  - Expandable dependency list: closed by default, reveals grouped edges
 *    on click, groups by targetDatasetId preserving stable order.
 *  - Summary line singular/plural ("1 cross-dataset reference to 1 other dataset"
 *    vs "5 cross-dataset references to 2 other datasets"). The count is
 *    distinct target ndiIds, not source documents — see the `edgeCount`
 *    JSDoc in `types/dataset-provenance.ts` for the semantic rationale.
 */
import { describe, expect, it } from 'vitest';
import { fireEvent, render, screen, within } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';

import { DatasetProvenanceCard } from './DatasetProvenanceCard';
import type { DatasetProvenance } from '@/types/dataset-provenance';

function baseProvenance(
  overrides: Partial<DatasetProvenance> = {},
): DatasetProvenance {
  return {
    datasetId: 'DSX',
    branchOf: null,
    branches: [],
    documentDependencies: [],
    computedAt: new Date().toISOString(),
    schemaVersion: 'provenance:v1',
    ...overrides,
  };
}

function renderCard(provenance: DatasetProvenance) {
  return render(
    <MemoryRouter>
      <DatasetProvenanceCard provenance={provenance} />
    </MemoryRouter>,
  );
}

describe('DatasetProvenanceCard — card shell', () => {
  it('renders the title + datasetId in the description', () => {
    renderCard(baseProvenance());
    expect(screen.getByTestId('dataset-provenance-card')).toBeInTheDocument();
    expect(screen.getByText('Dataset provenance')).toBeInTheDocument();
    // Description embeds the datasetId verbatim.
    expect(screen.getByText('DSX')).toBeInTheDocument();
  });
});

describe('DatasetProvenanceCard — empty states', () => {
  it('renders "Not a branch" when branchOf is null', () => {
    renderCard(baseProvenance({ branchOf: null }));
    const section = screen.getByTestId('provenance-branch-of');
    expect(within(section).getByTestId('provenance-not-a-branch')).toHaveTextContent(
      'Not a branch',
    );
    // No parent link rendered.
    expect(
      within(section).queryByTestId('provenance-branch-of-link'),
    ).toBeNull();
  });

  it('renders "No branches" when branches is empty', () => {
    renderCard(baseProvenance({ branches: [] }));
    const section = screen.getByTestId('provenance-branches');
    expect(within(section).getByTestId('provenance-no-branches')).toHaveTextContent(
      'No branches',
    );
    expect(within(section).queryByTestId('provenance-branch-chip')).toBeNull();
  });

  it('renders "No cross-dataset dependencies" when the list is empty', () => {
    renderCard(baseProvenance({ documentDependencies: [] }));
    const section = screen.getByTestId('provenance-dependencies');
    expect(
      within(section).getByTestId('provenance-no-dependencies'),
    ).toHaveTextContent('No cross-dataset dependencies');
    // No toggle when there are no edges.
    expect(
      within(section).queryByTestId('provenance-dependencies-toggle'),
    ).toBeNull();
  });
});

describe('DatasetProvenanceCard — branch-of link', () => {
  it('links to the parent dataset detail page when branchOf is set', () => {
    renderCard(baseProvenance({ branchOf: 'DSPARENT' }));
    const link = screen.getByTestId('provenance-branch-of-link');
    expect(link).toHaveTextContent('DSPARENT');
    expect(link.getAttribute('href')).toBe('/datasets/DSPARENT');
    // Not a "Not a branch" placeholder anymore.
    expect(screen.queryByTestId('provenance-not-a-branch')).toBeNull();
  });
});

describe('DatasetProvenanceCard — branches list', () => {
  it('renders one chip per child dataset, each a routed <Link>', () => {
    renderCard(baseProvenance({ branches: ['DSCHILD1', 'DSCHILD2'] }));
    const chips = screen.getAllByTestId('provenance-branch-chip');
    expect(chips).toHaveLength(2);
    expect(chips[0]).toHaveTextContent('DSCHILD1');
    expect(chips[0].getAttribute('href')).toBe('/datasets/DSCHILD1');
    expect(chips[1]).toHaveTextContent('DSCHILD2');
    expect(chips[1].getAttribute('href')).toBe('/datasets/DSCHILD2');
  });
});

describe('DatasetProvenanceCard — dependency list', () => {
  it('shows summary line with correct counts (plural)', () => {
    renderCard(
      baseProvenance({
        documentDependencies: [
          {
            sourceDatasetId: 'DSX',
            targetDatasetId: 'DSY',
            viaDocumentClass: 'element',
            edgeCount: 3,
          },
          {
            sourceDatasetId: 'DSX',
            targetDatasetId: 'DSZ',
            viaDocumentClass: 'element_epoch',
            edgeCount: 2,
          },
        ],
      }),
    );
    expect(screen.getByTestId('provenance-refs-count')).toHaveTextContent('5');
    expect(screen.getByTestId('provenance-targets-count')).toHaveTextContent('2');
    // Plural form: "5 cross-dataset references to 2 other datasets".
    const toggle = screen.getByTestId('provenance-dependencies-toggle');
    expect(toggle).toHaveTextContent(/5\s+cross-dataset references to\s+2\s+other datasets/);
  });

  it('uses singular form when counts are 1', () => {
    renderCard(
      baseProvenance({
        documentDependencies: [
          {
            sourceDatasetId: 'DSX',
            targetDatasetId: 'DSY',
            viaDocumentClass: 'element',
            edgeCount: 1,
          },
        ],
      }),
    );
    const toggle = screen.getByTestId('provenance-dependencies-toggle');
    expect(toggle).toHaveTextContent(/1\s+cross-dataset reference to\s+1\s+other dataset/);
  });

  it('is closed by default and toggles the list open on click', () => {
    renderCard(
      baseProvenance({
        documentDependencies: [
          {
            sourceDatasetId: 'DSX',
            targetDatasetId: 'DSY',
            viaDocumentClass: 'element',
            edgeCount: 3,
          },
        ],
      }),
    );
    const toggle = screen.getByTestId('provenance-dependencies-toggle');
    expect(toggle).toHaveAttribute('aria-expanded', 'false');
    // Closed: the edge list is not in the DOM.
    expect(screen.queryByTestId('provenance-dependencies-list')).toBeNull();

    fireEvent.click(toggle);
    expect(toggle).toHaveAttribute('aria-expanded', 'true');
    expect(screen.getByTestId('provenance-dependencies-list')).toBeInTheDocument();
  });

  it('groups edges by targetDatasetId and links to each target', () => {
    renderCard(
      baseProvenance({
        documentDependencies: [
          // DSY has two viaDocumentClass entries.
          {
            sourceDatasetId: 'DSX',
            targetDatasetId: 'DSY',
            viaDocumentClass: 'element',
            edgeCount: 3,
          },
          {
            sourceDatasetId: 'DSX',
            targetDatasetId: 'DSY',
            viaDocumentClass: 'element_epoch',
            edgeCount: 2,
          },
          {
            sourceDatasetId: 'DSX',
            targetDatasetId: 'DSZ',
            viaDocumentClass: 'element',
            edgeCount: 1,
          },
        ],
      }),
    );
    fireEvent.click(screen.getByTestId('provenance-dependencies-toggle'));
    const groups = screen.getAllByTestId('provenance-target-group');
    expect(groups).toHaveLength(2);
    // DSY group has 2 edges; DSZ group has 1.
    const dsyGroup = groups.find(
      (g) => g.getAttribute('data-target-dataset-id') === 'DSY',
    )!;
    const dszGroup = groups.find(
      (g) => g.getAttribute('data-target-dataset-id') === 'DSZ',
    )!;
    expect(
      within(dsyGroup).getAllByTestId('provenance-edge-row'),
    ).toHaveLength(2);
    expect(
      within(dszGroup).getAllByTestId('provenance-edge-row'),
    ).toHaveLength(1);
    // Target link points at the other dataset's detail page.
    const dsyLink = within(dsyGroup).getByTestId('provenance-target-link');
    expect(dsyLink.getAttribute('href')).toBe('/datasets/DSY');
    const dszLink = within(dszGroup).getByTestId('provenance-target-link');
    expect(dszLink.getAttribute('href')).toBe('/datasets/DSZ');
  });

  it('surfaces each edge count and class badge in the expanded list', () => {
    renderCard(
      baseProvenance({
        documentDependencies: [
          {
            sourceDatasetId: 'DSX',
            targetDatasetId: 'DSY',
            viaDocumentClass: 'element',
            edgeCount: 3,
          },
          {
            sourceDatasetId: 'DSX',
            targetDatasetId: 'DSY',
            viaDocumentClass: 'element_epoch',
            edgeCount: 1,
          },
        ],
      }),
    );
    fireEvent.click(screen.getByTestId('provenance-dependencies-toggle'));
    const rows = screen.getAllByTestId('provenance-edge-row');
    expect(rows).toHaveLength(2);
    // Row 1: element badge + "3 refs" (distinct target-ndiId count, per
    // edgeCount JSDoc — not a per-source-document count).
    expect(rows[0]).toHaveTextContent('element');
    expect(rows[0]).toHaveTextContent('3 refs');
    // Row 2: element_epoch badge + "1 ref" (singular).
    expect(rows[1]).toHaveTextContent('element_epoch');
    expect(rows[1]).toHaveTextContent('1 ref');
    // Singular is exactly "1 ref", not "1 refs".
    expect(rows[1].textContent?.includes('1 refs')).toBe(false);
  });
});

describe('DatasetProvenanceCard — coexistence of states', () => {
  it('renders branch-of + branches + dependencies together', () => {
    renderCard(
      baseProvenance({
        branchOf: 'DSPARENT',
        branches: ['DSCHILD'],
        documentDependencies: [
          {
            sourceDatasetId: 'DSX',
            targetDatasetId: 'DSY',
            viaDocumentClass: 'element',
            edgeCount: 2,
          },
        ],
      }),
    );
    expect(screen.getByTestId('provenance-branch-of-link')).toHaveTextContent(
      'DSPARENT',
    );
    expect(screen.getByTestId('provenance-branch-chip')).toHaveTextContent(
      'DSCHILD',
    );
    expect(
      screen.getByTestId('provenance-dependencies-toggle'),
    ).toBeInTheDocument();
    // No empty-state placeholders appear when the data is present.
    expect(screen.queryByTestId('provenance-not-a-branch')).toBeNull();
    expect(screen.queryByTestId('provenance-no-branches')).toBeNull();
    expect(screen.queryByTestId('provenance-no-dependencies')).toBeNull();
  });
});
