/**
 * OutputShapePreview — Plan B B3 tests.
 *
 * Static component; no hooks. Just verifies the rendered column-set headers
 * match the canonical B6a defaults, and that the grain filter works.
 */
import { describe, expect, it } from 'vitest';
import { render, screen } from '@testing-library/react';

import {
  EPOCH_DEFAULT_COLUMNS,
  PROBE_DEFAULT_COLUMNS,
  SUBJECT_DEFAULT_COLUMNS,
} from '@/data/table-column-definitions';

import { OutputShapePreview } from './OutputShapePreview';

describe('OutputShapePreview', () => {
  it('renders all three grains by default', () => {
    render(<OutputShapePreview />);
    expect(screen.getByText(/Subject grain/)).toBeInTheDocument();
    expect(screen.getByText(/Probe grain/)).toBeInTheDocument();
    expect(screen.getByText(/Epoch grain/)).toBeInTheDocument();
  });

  it('shows the subject column headers from the canonical default set', () => {
    render(<OutputShapePreview grains={['subject']} />);
    // Should contain every visible column in SUBJECT_DEFAULT_COLUMNS.
    for (const col of SUBJECT_DEFAULT_COLUMNS.filter((c) => c.visible)) {
      expect(screen.getByText(col.header)).toBeInTheDocument();
    }
  });

  it('shows the probe column headers from the canonical default set', () => {
    render(<OutputShapePreview grains={['probe']} />);
    for (const col of PROBE_DEFAULT_COLUMNS.filter((c) => c.visible)) {
      expect(screen.getAllByText(col.header).length).toBeGreaterThan(0);
    }
  });

  it('shows the epoch column headers from the canonical default set', () => {
    render(<OutputShapePreview grains={['epoch']} />);
    for (const col of EPOCH_DEFAULT_COLUMNS.filter((c) => c.visible)) {
      expect(screen.getAllByText(col.header).length).toBeGreaterThan(0);
    }
  });

  it('filters to a single grain when grains prop is provided', () => {
    render(<OutputShapePreview grains={['subject']} />);
    expect(screen.getByText(/Subject grain/)).toBeInTheDocument();
    expect(screen.queryByText(/Probe grain/)).not.toBeInTheDocument();
    expect(screen.queryByText(/Epoch grain/)).not.toBeInTheDocument();
  });

  it('cites the NDI-matlab Francesconi tutorial with external link', () => {
    render(<OutputShapePreview />);
    const tutorialLink = screen.getByRole('link', {
      name: /Francesconi et al. 2025 tutorial/i,
    });
    expect(tutorialLink).toHaveAttribute(
      'href',
      expect.stringContaining('Francesconi_et_al_2025'),
    );
    expect(tutorialLink).toHaveAttribute('target', '_blank');
    expect(tutorialLink).toHaveAttribute('rel', 'noreferrer noopener');
  });
});
