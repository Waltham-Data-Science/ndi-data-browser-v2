/**
 * UseThisDataModal — Plan B B4 "Use this data" affordance.
 *
 * Two tabs (Python / MATLAB), each showing the canonical local-analysis
 * snippet for the matching NDI toolkit. The snippets are LITERAL from
 * amendment §4.B4 — do not paraphrase. ``<DATASET_ID>`` is substituted
 * with the real id at render time.
 *
 * Both tabs surface a "dissonance note" acknowledging that these
 * snippets download the dataset for local work, whereas v2's browser is
 * cloud-first (no download needed). This is the amendment's explicit
 * ask — do not remove.
 */
import { useMemo, useState } from 'react';
import { AlertTriangle } from 'lucide-react';

import { CopyButton } from '@/components/ui/CopyButton';
import { Modal } from '@/components/ui/Modal';
import { Tabs, type TabItem } from '@/components/ui/Tabs';
import { cn } from '@/lib/cn';

export interface UseThisDataModalProps {
  open: boolean;
  onClose: () => void;
  datasetId: string;
}

type SnippetTab = 'python' | 'matlab';

const TABS: TabItem<SnippetTab>[] = [
  { id: 'python', label: 'Python' },
  { id: 'matlab', label: 'MATLAB' },
];

/**
 * Dissonance note rendered above each snippet (amendment §4.B4).
 * Copy intentionally identical in both tabs.
 */
const DISSONANCE_NOTE =
  "These snippets download the dataset for local analysis. v2's browser lets you explore without downloading — this is for local work.";

/**
 * Literal Python snippet from amendment §4.B4. ``<DATASET_ID>`` is
 * substituted at render time.
 */
const PYTHON_TEMPLATE = `import os
from ndi.cloud import downloadDataset
from ndi.cloud.auth import login
from ndi.cloud.client import CloudClient
from ndi.fun.doc_table import subject, probe, epoch

config = login(os.environ["NDI_CLOUD_USERNAME"], os.environ["NDI_CLOUD_PASSWORD"])
client = CloudClient(config)
dataset = downloadDataset("<DATASET_ID>", "~/ndi-datasets", verbose=True, client=client)
subject_df = subject(dataset)
`;

/**
 * Literal MATLAB snippet from amendment §4.B4. ``<DATASET_ID>`` is
 * substituted at render time.
 */
const MATLAB_TEMPLATE = `dataPath = [userpath filesep 'Datasets'];
datasetPath = fullfile(dataPath, '<DATASET_ID>');
if isfolder(datasetPath)
    dataset = ndi.dataset.dir(datasetPath);
else
    dataset = ndi.cloud.downloadDataset('<DATASET_ID>', dataPath);
end
subjectSummary = ndi.fun.docTable.subject(dataset);
`;

/** Replace every ``<DATASET_ID>`` token in the template with the
 *  supplied dataset id. Uses a literal ``<DATASET_ID>`` match (not a
 *  regex placeholder) so ids that happen to contain regex metacharacters
 *  round-trip losslessly. */
export function substituteDatasetId(template: string, datasetId: string): string {
  // Use split/join over String.replaceAll so that the substitution is
  // literal — replaceAll with a string pattern is safe, but going via
  // split keeps the behaviour identical if a caller ever passes a
  // RegExp-like string.
  return template.split('<DATASET_ID>').join(datasetId);
}

export function UseThisDataModal({
  open,
  onClose,
  datasetId,
}: UseThisDataModalProps) {
  const [active, setActive] = useState<SnippetTab>('python');

  const pythonSnippet = useMemo(
    () => substituteDatasetId(PYTHON_TEMPLATE, datasetId),
    [datasetId],
  );
  const matlabSnippet = useMemo(
    () => substituteDatasetId(MATLAB_TEMPLATE, datasetId),
    [datasetId],
  );

  return (
    <Modal
      open={open}
      onClose={onClose}
      title="Use this data"
      description="Canonical local-analysis snippets. Python for ndi-python, MATLAB for NDI-matlab."
      size="lg"
    >
      <div className="space-y-4" data-testid="use-data-modal-body">
        <Tabs
          tabs={TABS}
          active={active}
          onSelect={(id) => setActive(id)}
          className="mb-2"
        />

        <DissonanceNote />

        {active === 'python' ? (
          <SnippetPanel
            language="python"
            snippet={pythonSnippet}
            testId="snippet-python"
            filename={`ndi-${datasetId}-local.py`}
          />
        ) : (
          <SnippetPanel
            language="matlab"
            snippet={matlabSnippet}
            testId="snippet-matlab"
            filename={`ndi_${datasetId}_local.m`}
          />
        )}
      </div>
    </Modal>
  );
}

function DissonanceNote() {
  return (
    <aside
      role="note"
      className={cn(
        'flex gap-2 rounded-md bg-amber-50 px-3 py-2 text-xs',
        'text-amber-800 ring-1 ring-amber-200',
      )}
      data-testid="dissonance-note"
    >
      <AlertTriangle className="mt-0.5 h-3.5 w-3.5 shrink-0" aria-hidden />
      <p>{DISSONANCE_NOTE}</p>
    </aside>
  );
}

function SnippetPanel({
  language,
  snippet,
  testId,
  filename,
}: {
  language: 'python' | 'matlab';
  snippet: string;
  testId: string;
  filename: string;
}) {
  return (
    <section
      role="tabpanel"
      aria-label={language === 'python' ? 'Python' : 'MATLAB'}
      className="space-y-2"
      data-testid={testId}
    >
      <div className="flex items-center justify-between">
        <span className="font-mono text-[11px] text-gray-500">
          {filename}
        </span>
        <CopyButton
          value={snippet}
          ariaLabel={`Copy ${language} snippet`}
          testId={`${testId}-copy`}
        />
      </div>
      <pre
        className={cn(
          'overflow-x-auto rounded-md border border-gray-200 bg-gray-900 p-3',
          'font-mono text-[12px] leading-relaxed text-gray-100',
        )}
        data-language={language}
        data-testid={`${testId}-content`}
      >
        <code>{snippet}</code>
      </pre>
    </section>
  );
}
