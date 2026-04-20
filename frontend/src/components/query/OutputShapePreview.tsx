import { Card, CardBody, CardHeader, CardTitle } from '@/components/ui/Card';
import {
  EPOCH_DEFAULT_COLUMNS,
  PROBE_DEFAULT_COLUMNS,
  SUBJECT_DEFAULT_COLUMNS,
  type ColumnDefault,
} from '@/data/table-column-definitions';

/**
 * Output-shape preview — renders the header row of the canonical
 * subject/probe/epoch column defaults (Plan B amendment §4.B3).
 *
 * Purely static: no rows, no backend call. Its point is to tell a
 * researcher "if you filter on the subject grain, this is the table shape
 * you'll get back." The columns come from B6a's canonical
 * ``{SUBJECT,PROBE,EPOCH}_DEFAULT_COLUMNS`` — those are the NDI-matlab
 * Francesconi tutorial column sets (13-col subject, 9-col probe, 12-col
 * epoch). Clicking through to the full tutorial is surfaced as an inline
 * citation.
 *
 * Props control which grains show. When the query page learns a caller's
 * ``isa`` filter it narrows to that grain; otherwise all three render.
 */
export interface OutputShapePreviewProps {
  /** Optional filter: show only these grains. Empty / undefined = show all. */
  grains?: ReadonlyArray<'subject' | 'probe' | 'epoch'>;
}

/**
 * Documentation link to the NDI-matlab tutorial that defines the canonical
 * column shapes. Surfaced as the "source" citation on every preview.
 */
const TUTORIAL_URL =
  'https://github.com/VH-Lab/NDI-matlab/blob/main/src/ndi/docs/NDI-matlab/tutorials/datasets/Francesconi_et_al_2025/1_getting_started.md';

const PAPER_URL = 'https://doi.org/10.1016/j.celrep.2025.115768';

const GRAIN_CONFIG = {
  subject: {
    title: 'Subject grain',
    matlabCall: 'docTable.subject',
    columns: SUBJECT_DEFAULT_COLUMNS,
  },
  probe: {
    title: 'Probe grain',
    matlabCall: 'docTable.probe',
    columns: PROBE_DEFAULT_COLUMNS,
  },
  epoch: {
    title: 'Epoch grain',
    matlabCall: 'docTable.epoch',
    columns: EPOCH_DEFAULT_COLUMNS,
  },
} as const;

export function OutputShapePreview({ grains }: OutputShapePreviewProps) {
  const visibleGrains = grains && grains.length > 0
    ? grains
    : (['subject', 'probe', 'epoch'] as const);

  return (
    <Card>
      <CardHeader>
        <CardTitle className="text-sm">Output shape preview</CardTitle>
      </CardHeader>
      <CardBody className="space-y-4">
        <p className="text-xs text-gray-600 dark:text-gray-400">
          These are the column sets a matching result table will use for each
          grain. Shape follows NDI-matlab&apos;s canonical{' '}
          <code className="font-mono text-[11px]">docTable</code> tutorial.
        </p>

        {visibleGrains.map((grain) => (
          <GrainPreview key={grain} grain={grain} />
        ))}

        <p className="text-[11px] text-gray-500 dark:text-gray-400 pt-2 border-t border-gray-200 dark:border-gray-700">
          Source: NDI-matlab{' '}
          <a
            href={TUTORIAL_URL}
            target="_blank"
            rel="noreferrer noopener"
            className="text-brand-600 dark:text-brand-400 hover:underline"
          >
            Francesconi et al. 2025 tutorial
          </a>{' '}
          ·{' '}
          <a
            href={PAPER_URL}
            target="_blank"
            rel="noreferrer noopener"
            className="text-brand-600 dark:text-brand-400 hover:underline"
          >
            Cell Reports paper
          </a>
          .
        </p>
      </CardBody>
    </Card>
  );
}

function GrainPreview({ grain }: { grain: 'subject' | 'probe' | 'epoch' }) {
  const cfg = GRAIN_CONFIG[grain];
  const visibleColumns = cfg.columns.filter((c: ColumnDefault) => c.visible);
  return (
    <div>
      <h3 className="text-xs font-medium text-gray-700 dark:text-gray-200 mb-1.5">
        {cfg.title}{' '}
        <code className="font-mono text-[11px] text-gray-500 dark:text-gray-400">
          {cfg.matlabCall}
        </code>{' '}
        <span className="text-gray-500 dark:text-gray-400">
          ({visibleColumns.length} columns)
        </span>
      </h3>
      <div className="overflow-x-auto rounded border border-gray-200 dark:border-gray-700">
        <table className="w-full text-xs">
          <thead className="bg-gray-50 dark:bg-gray-900">
            <tr>
              {visibleColumns.map((col) => (
                <th
                  key={col.id}
                  className="px-2 py-1.5 text-left font-medium text-gray-600 dark:text-gray-300 whitespace-nowrap"
                >
                  {col.header}
                </th>
              ))}
            </tr>
          </thead>
        </table>
      </div>
    </div>
  );
}
