/**
 * CiteModal — Plan B B4 "Cite" affordance.
 *
 * Renders BibTeX / RIS / plain-text citation blocks next to a
 * per-block copy-to-clipboard button. The dataset DOI (``10.63884/``
 * prefix) is visually distinguished as the preferred cite target;
 * paper DOIs render as a secondary list (see amendment §4.B4 "Two DOIs
 * per dataset").
 *
 * The year field is labelled "Upload year" — the
 * :ts:`DatasetSummaryCitation.year` field is record-creation year, not
 * paper publication year (see the FROZEN shape docstring).
 *
 * This modal reads from ``citation`` only. It does not touch the
 * broader :class:`DatasetSummary` — all cite-format code works off the
 * citation sub-shape alone.
 */
import { useMemo } from 'react';

import { ExternalAnchor } from '@/components/ExternalAnchor';
import { Badge } from '@/components/ui/Badge';
import { CopyButton } from '@/components/ui/CopyButton';
import { Modal } from '@/components/ui/Modal';
import {
  stripDoiPrefix,
  toBibtex,
  toPlainText,
  toRis,
} from '@/lib/citation-formats';
import type { DatasetSummaryCitation } from '@/types/dataset-summary';

export interface CiteModalProps {
  open: boolean;
  onClose: () => void;
  citation: DatasetSummaryCitation;
}

export function CiteModal({ open, onClose, citation }: CiteModalProps) {
  const bibtex = useMemo(() => toBibtex(citation), [citation]);
  const ris = useMemo(() => toRis(citation), [citation]);
  const plain = useMemo(() => toPlainText(citation), [citation]);

  return (
    <Modal
      open={open}
      onClose={onClose}
      title="Cite this dataset"
      description="Ready-to-paste citations in three formats. The dataset DOI is the preferred citation target."
      size="lg"
    >
      <div className="space-y-5" data-testid="cite-modal-body">
        <DoiBlock citation={citation} />

        <CiteBlock
          label="Plain text"
          value={plain}
          testId="cite-plain"
        />
        <CiteBlock
          label="BibTeX"
          value={bibtex}
          testId="cite-bibtex"
          monospace
        />
        <CiteBlock
          label="RIS"
          value={ris}
          testId="cite-ris"
          monospace
        />
      </div>
    </Modal>
  );
}

// ---------------------------------------------------------------------------
// DoiBlock — the visual primary/secondary distinction required by §4.B4.
// ---------------------------------------------------------------------------

function DoiBlock({ citation }: { citation: DatasetSummaryCitation }) {
  const hasDatasetDoi = !!citation.datasetDoi;
  const hasPaperDois = citation.paperDois.length > 0;

  if (!hasDatasetDoi && !hasPaperDois) {
    return (
      <p
        className="rounded-md bg-amber-50 px-3 py-2 text-xs text-amber-800 ring-1 ring-amber-200 dark:bg-amber-900/30 dark:text-amber-200 dark:ring-amber-800"
        data-testid="cite-no-doi"
      >
        No DOI on record for this dataset yet. Quote the title and NDI Cloud
        URL until the dataset DOI is minted.
      </p>
    );
  }

  return (
    <div className="space-y-3" data-testid="cite-doi-block">
      {hasDatasetDoi && (
        <div
          className="rounded-md border border-brand-300 bg-brand-50 p-3 dark:border-brand-700 dark:bg-brand-900/40"
          data-testid="cite-dataset-doi"
        >
          <div className="mb-1 flex items-center gap-2">
            <Badge variant="default">Dataset DOI (preferred)</Badge>
          </div>
          <ExternalAnchor
            href={citation.datasetDoi!}
            label={stripDoiPrefix(citation.datasetDoi!)}
            className="font-mono text-xs"
          />
        </div>
      )}
      {hasPaperDois && (
        <div
          className="rounded-md border border-slate-200 bg-slate-50 p-3 dark:border-slate-700 dark:bg-slate-900"
          data-testid="cite-paper-dois"
        >
          <div className="mb-1 flex items-center gap-2">
            <Badge variant="outline">Paper DOI</Badge>
            <span className="text-[11px] text-slate-500 dark:text-slate-400">
              Secondary — cite when specifically referencing the paper.
            </span>
          </div>
          <ul className="space-y-0.5">
            {citation.paperDois.map((doi) => (
              <li key={doi}>
                <ExternalAnchor
                  href={doi}
                  label={stripDoiPrefix(doi)}
                  className="font-mono text-xs"
                />
              </li>
            ))}
          </ul>
        </div>
      )}
      {citation.year != null && (
        <p
          className="text-[11px] text-slate-500 dark:text-slate-400"
          data-testid="cite-upload-year-note"
        >
          Upload year: <span className="font-mono">{citation.year}</span>. This
          is the record-creation year in NDI Cloud, not the paper publication
          year — resolve paper DOIs externally for the publication year.
        </p>
      )}
    </div>
  );
}

// ---------------------------------------------------------------------------
// CiteBlock — generic labelled block with copy button.
// ---------------------------------------------------------------------------

function CiteBlock({
  label,
  value,
  testId,
  monospace = false,
}: {
  label: string;
  value: string;
  testId: string;
  monospace?: boolean;
}) {
  return (
    <section className="space-y-1.5" data-testid={testId}>
      <div className="flex items-center justify-between gap-2">
        <h3 className="text-xs font-semibold uppercase tracking-wide text-slate-600 dark:text-slate-300">
          {label}
        </h3>
        <CopyButton
          value={value}
          ariaLabel={`Copy ${label} citation`}
          testId={`${testId}-copy`}
        />
      </div>
      <pre
        className={
          monospace
            ? 'whitespace-pre-wrap break-words rounded-md border border-slate-200 bg-slate-50 p-3 font-mono text-[11px] text-slate-800 dark:border-slate-700 dark:bg-slate-900 dark:text-slate-100'
            : 'whitespace-pre-wrap break-words rounded-md border border-slate-200 bg-slate-50 p-3 text-xs text-slate-800 dark:border-slate-700 dark:bg-slate-900 dark:text-slate-100'
        }
        data-testid={`${testId}-content`}
      >
        {value}
      </pre>
    </section>
  );
}
