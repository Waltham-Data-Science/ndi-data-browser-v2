/**
 * UseThisDataModal — verifies the Python and MATLAB tabs show the
 * literal snippets from amendment §4.B4, tab switching swaps the
 * visible snippet, <DATASET_ID> is substituted, and the dissonance
 * note renders.
 */
import { beforeEach, describe, expect, it, vi } from 'vitest';
import { fireEvent, render, screen } from '@testing-library/react';

import { UseThisDataModal, substituteDatasetId } from './UseThisDataModal';

describe('substituteDatasetId', () => {
  it('replaces every occurrence of <DATASET_ID>', () => {
    const t = '<DATASET_ID> and <DATASET_ID>';
    expect(substituteDatasetId(t, 'abc')).toBe('abc and abc');
  });
  it('is a no-op when the token is absent', () => {
    expect(substituteDatasetId('hello', 'abc')).toBe('hello');
  });
  it('safely handles ids that contain regex metacharacters', () => {
    expect(substituteDatasetId('<DATASET_ID>', '(a|b).*')).toBe('(a|b).*');
  });
});

describe('UseThisDataModal', () => {
  let writeText: ReturnType<typeof vi.fn>;
  const DATASET_ID = 'ds-1234-abcd';

  beforeEach(() => {
    writeText = vi.fn().mockResolvedValue(undefined);
    Object.defineProperty(navigator, 'clipboard', {
      value: { writeText },
      configurable: true,
    });
  });

  it('renders the Python tab by default with the literal snippet', () => {
    render(
      <UseThisDataModal
        open
        onClose={() => {}}
        datasetId={DATASET_ID}
      />,
    );
    const pre = screen.getByTestId('snippet-python-content');
    const text = pre.textContent ?? '';
    // Each literal line from amendment §4.B4
    expect(text).toContain('import os');
    expect(text).toContain('from ndi.cloud import downloadDataset');
    expect(text).toContain('from ndi.cloud.auth import login');
    expect(text).toContain('from ndi.cloud.client import CloudClient');
    expect(text).toContain('from ndi.fun.doc_table import subject, probe, epoch');
    expect(text).toContain(
      'config = login(os.environ["NDI_CLOUD_USERNAME"], os.environ["NDI_CLOUD_PASSWORD"])',
    );
    expect(text).toContain('client = CloudClient(config)');
    expect(text).toContain(
      `dataset = downloadDataset("${DATASET_ID}", "~/ndi-datasets", verbose=True, client=client)`,
    );
    expect(text).toContain('subject_df = subject(dataset)');
    // No unsubstituted token
    expect(text).not.toContain('<DATASET_ID>');
  });

  it('switches to MATLAB tab and shows the literal MATLAB snippet', () => {
    render(
      <UseThisDataModal
        open
        onClose={() => {}}
        datasetId={DATASET_ID}
      />,
    );
    fireEvent.click(screen.getByTestId('tab-matlab'));
    const pre = screen.getByTestId('snippet-matlab-content');
    const text = pre.textContent ?? '';
    expect(text).toContain("dataPath = [userpath filesep 'Datasets'];");
    expect(text).toContain(`datasetPath = fullfile(dataPath, '${DATASET_ID}');`);
    expect(text).toContain('if isfolder(datasetPath)');
    expect(text).toContain('dataset = ndi.dataset.dir(datasetPath);');
    expect(text).toContain(
      `dataset = ndi.cloud.downloadDataset('${DATASET_ID}', dataPath);`,
    );
    expect(text).toContain('subjectSummary = ndi.fun.docTable.subject(dataset);');
    expect(text).not.toContain('<DATASET_ID>');
  });

  it('shows the dissonance note in both tabs', () => {
    render(
      <UseThisDataModal
        open
        onClose={() => {}}
        datasetId={DATASET_ID}
      />,
    );
    const note = screen.getByTestId('dissonance-note');
    expect(note.textContent).toMatch(
      /download.*local.*v2's browser.*without downloading/i,
    );
    // Switch to MATLAB — note still present
    fireEvent.click(screen.getByTestId('tab-matlab'));
    expect(screen.getByTestId('dissonance-note').textContent).toMatch(
      /without downloading/i,
    );
  });

  it('copy button writes the Python snippet to clipboard', async () => {
    render(
      <UseThisDataModal
        open
        onClose={() => {}}
        datasetId={DATASET_ID}
      />,
    );
    fireEvent.click(screen.getByTestId('snippet-python-copy'));
    await Promise.resolve();
    expect(writeText).toHaveBeenCalledTimes(1);
    const arg = writeText.mock.calls[0]![0] as string;
    expect(arg).toContain(`downloadDataset("${DATASET_ID}"`);
  });

  it('copy button writes the MATLAB snippet to clipboard', async () => {
    render(
      <UseThisDataModal
        open
        onClose={() => {}}
        datasetId={DATASET_ID}
      />,
    );
    fireEvent.click(screen.getByTestId('tab-matlab'));
    fireEvent.click(screen.getByTestId('snippet-matlab-copy'));
    await Promise.resolve();
    expect(writeText).toHaveBeenCalledTimes(1);
    const arg = writeText.mock.calls[0]![0] as string;
    expect(arg).toContain(`ndi.cloud.downloadDataset('${DATASET_ID}'`);
  });

  it('the active tab is tracked via aria-selected', () => {
    render(
      <UseThisDataModal
        open
        onClose={() => {}}
        datasetId={DATASET_ID}
      />,
    );
    expect(screen.getByTestId('tab-python').getAttribute('aria-selected')).toBe(
      'true',
    );
    fireEvent.click(screen.getByTestId('tab-matlab'));
    expect(screen.getByTestId('tab-matlab').getAttribute('aria-selected')).toBe(
      'true',
    );
    expect(screen.getByTestId('tab-python').getAttribute('aria-selected')).toBe(
      'false',
    );
  });

  it('closes on backdrop click', () => {
    const onClose = vi.fn();
    render(
      <UseThisDataModal
        open
        onClose={onClose}
        datasetId={DATASET_ID}
      />,
    );
    fireEvent.click(screen.getByTestId('modal-backdrop'));
    expect(onClose).toHaveBeenCalledTimes(1);
  });
});
