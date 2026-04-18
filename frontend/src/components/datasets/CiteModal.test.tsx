/**
 * CiteModal — verifies BibTeX/RIS output matches the expected format,
 * the copy button writes to clipboard, the two DOIs render with their
 * primary/secondary labelling, and modal dismissal works.
 */
import { beforeEach, describe, expect, it, vi } from 'vitest';
import { fireEvent, render, screen, within } from '@testing-library/react';

import { CiteModal } from './CiteModal';
import type { DatasetSummaryCitation } from '@/types/dataset-summary';

function citation(
  overrides: Partial<DatasetSummaryCitation> = {},
): DatasetSummaryCitation {
  return {
    title: 'Acute slice recordings',
    license: 'CC-BY-4.0',
    datasetDoi: 'https://doi.org/10.63884/abc',
    paperDois: ['https://doi.org/10.1/xyz'],
    contributors: [
      { firstName: 'Ada', lastName: 'Lovelace', orcid: null },
    ],
    year: 2025,
    ...overrides,
  };
}

describe('CiteModal', () => {
  let writeText: ReturnType<typeof vi.fn>;

  beforeEach(() => {
    writeText = vi.fn().mockResolvedValue(undefined);
    // jsdom does not provide navigator.clipboard by default; assign an
    // object with just the method the CopyButton needs.
    Object.defineProperty(navigator, 'clipboard', {
      value: { writeText },
      configurable: true,
    });
  });

  it('renders plain-text, BibTeX, and RIS blocks', () => {
    render(<CiteModal open onClose={() => {}} citation={citation()} />);
    expect(screen.getByTestId('cite-plain')).toBeInTheDocument();
    expect(screen.getByTestId('cite-bibtex')).toBeInTheDocument();
    expect(screen.getByTestId('cite-ris')).toBeInTheDocument();
  });

  it('BibTeX content matches expected @dataset entry', () => {
    render(<CiteModal open onClose={() => {}} citation={citation()} />);
    const pre = screen.getByTestId('cite-bibtex-content');
    expect(pre.textContent ?? '').toMatch(/^@dataset\{lovelace2025_acute,/);
    expect(pre.textContent).toContain('title = {Acute slice recordings}');
    expect(pre.textContent).toContain('doi = {10.63884/abc}');
  });

  it('RIS content matches expected TY - DATA record', () => {
    render(<CiteModal open onClose={() => {}} citation={citation()} />);
    const pre = screen.getByTestId('cite-ris-content');
    expect((pre.textContent ?? '').startsWith('TY  - DATA')).toBe(true);
    expect(pre.textContent).toContain('DO  - 10.63884/abc');
    expect(pre.textContent).toMatch(/ER {2}- \n?$/);
  });

  it('plain-text content labels the year as Upload year', () => {
    render(<CiteModal open onClose={() => {}} citation={citation()} />);
    const pre = screen.getByTestId('cite-plain-content');
    expect(pre.textContent).toContain('Upload year: 2025');
  });

  it('BibTeX copy button calls clipboard.writeText with the BibTeX string', async () => {
    render(<CiteModal open onClose={() => {}} citation={citation()} />);
    const btn = screen.getByTestId('cite-bibtex-copy');
    fireEvent.click(btn);
    // React state transition after the async writeText promise
    await Promise.resolve();
    expect(writeText).toHaveBeenCalledTimes(1);
    const arg = writeText.mock.calls[0]![0] as string;
    expect(arg).toMatch(/^@dataset\{lovelace2025_acute,/);
    expect(arg).toContain('doi = {10.63884/abc}');
  });

  it('RIS copy button calls clipboard.writeText with RIS string', async () => {
    render(<CiteModal open onClose={() => {}} citation={citation()} />);
    fireEvent.click(screen.getByTestId('cite-ris-copy'));
    await Promise.resolve();
    const arg = writeText.mock.calls[0]![0] as string;
    expect(arg.startsWith('TY  - DATA')).toBe(true);
  });

  it('plain-text copy button calls clipboard.writeText with the plain-text string', async () => {
    render(<CiteModal open onClose={() => {}} citation={citation()} />);
    fireEvent.click(screen.getByTestId('cite-plain-copy'));
    await Promise.resolve();
    const arg = writeText.mock.calls[0]![0] as string;
    expect(arg).toContain('Lovelace A.');
    expect(arg).toContain('doi:10.63884/abc');
  });

  it('visually distinguishes dataset DOI (primary) from paper DOI (secondary)', () => {
    render(<CiteModal open onClose={() => {}} citation={citation()} />);
    const primary = screen.getByTestId('cite-dataset-doi');
    expect(within(primary).getByText(/Dataset DOI \(preferred\)/)).toBeInTheDocument();
    const secondary = screen.getByTestId('cite-paper-dois');
    expect(within(secondary).getByText('Paper DOI')).toBeInTheDocument();
  });

  it('renders only paper DOI block when no dataset DOI is on record', () => {
    render(
      <CiteModal
        open
        onClose={() => {}}
        citation={citation({ datasetDoi: null })}
      />,
    );
    expect(screen.queryByTestId('cite-dataset-doi')).toBeNull();
    expect(screen.getByTestId('cite-paper-dois')).toBeInTheDocument();
  });

  it('shows the no-DOI fallback when both DOIs are absent', () => {
    render(
      <CiteModal
        open
        onClose={() => {}}
        citation={citation({ datasetDoi: null, paperDois: [] })}
      />,
    );
    expect(screen.getByTestId('cite-no-doi')).toBeInTheDocument();
  });

  it('closes on backdrop click', () => {
    const onClose = vi.fn();
    render(<CiteModal open onClose={onClose} citation={citation()} />);
    fireEvent.click(screen.getByTestId('modal-backdrop'));
    expect(onClose).toHaveBeenCalledTimes(1);
  });
});
