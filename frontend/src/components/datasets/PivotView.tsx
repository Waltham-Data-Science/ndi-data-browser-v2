/**
 * PivotView — Plan B B6e grain-selectable pivot v1.
 *
 * Behind the ``FEATURE_PIVOT_V1`` backend flag. The parent route mounts
 * this under ``/datasets/:id/pivot/:grain``. The grain selector is auto-
 * populated from the dataset summary's counts block (any grain with
 * count ≥ 1 is offered). Per amendment §4.B6e only subject/session/element
 * grains ship in v1 — exotic edges defer.
 *
 * Feature-flag discovery: the first fetch for any grain surfaces a 503 when
 * the flag is off. ``DatasetPivotNavGuard`` below probes ``/pivot/subject``
 * once and hides the entire pivot surface when disabled — the route still
 * renders something (a "feature disabled" message) if a user lands directly
 * via URL. Non-intrusive: nothing shows in the sidebar / detail layout.
 *
 * Row shape contract: ``PivotResponse.rows`` is per-grain-specific. We render
 * columns in the server-provided order. Column tooltip hints come from the
 * shared ``frontend/src/data/table-column-definitions.ts`` per-class dictionary
 * when a key matches — see the TODO below for coordination with B6a.
 */
import { useMemo } from 'react';
import { useNavigate, useParams } from 'react-router-dom';
import {
  flexRender,
  getCoreRowModel,
  useReactTable,
  type ColumnDef,
} from '@tanstack/react-table';

import { ApiError } from '@/api/errors';
import {
  useDatasetPivot,
  useDatasetSummary,
  type PivotGrain,
  type PivotResponse,
} from '@/api/datasets';
import { ErrorState } from '@/components/errors/ErrorState';
import {
  Card,
  CardBody,
  CardDescription,
  CardHeader,
  CardTitle,
} from '@/components/ui/Card';
import { TableLoadingPanel } from '@/components/ui/Skeleton';
// TODO(B6a): switch to the per-grain column defaults dictionary once B6a
// ships its expanded export shape. For now we reuse the flat
// ``getColumnDefinition`` lookup — keys are camelCase and already match
// the pivot's column keys for subject/element grains.
import { getColumnDefinition } from '@/data/table-column-definitions';

/** Grains offered in v1. Order drives the selector dropdown order. */
const GRAIN_ORDER: PivotGrain[] = ['subject', 'session', 'element'];

/** Per-grain human-readable label in the selector. */
const GRAIN_LABELS: Record<PivotGrain, string> = {
  subject: 'Subject',
  session: 'Session',
  element: 'Element',
};

/** Map each grain to the `DatasetSummary.counts` field that populates it. */
function grainCount(
  grain: PivotGrain,
  counts: { subjects: number; sessions: number; elements: number },
): number {
  if (grain === 'subject') return counts.subjects;
  if (grain === 'session') return counts.sessions;
  return counts.elements;
}

export function PivotView() {
  const { id, grain: rawGrain } = useParams<{ id: string; grain?: string }>();
  const navigate = useNavigate();
  const grain = coerceGrain(rawGrain);
  const summary = useDatasetSummary(id);
  const pivot = useDatasetPivot(id, grain);

  // Hooks MUST run in the same order on every render — compute
  // `availableGrains` before any early return.
  const availableGrains: PivotGrain[] = useMemo(() => {
    if (!summary.data) return [];
    return GRAIN_ORDER.filter(
      (g) => grainCount(g, summary.data!.counts) >= 1,
    );
  }, [summary.data]);

  const handleGrainChange = (next: PivotGrain) => {
    if (!id) return;
    navigate(`/datasets/${id}/pivot/${next}`);
  };

  // 503 on any pivot fetch means the feature flag is off. Surface a
  // dedicated disabled-state card — the dataset detail layout's sidebar
  // card list (cc, summary) keeps rendering.
  if (pivot.isError && isFeatureDisabled(pivot.error)) {
    return <PivotDisabledCard />;
  }

  return (
    <Card>
      <CardHeader>
        <div className="flex items-start justify-between gap-2">
          <div>
            <CardTitle className="text-base">Grain pivot</CardTitle>
            <CardDescription className="text-xs">
              Cross-class pivot keyed by a single grain. v1 supports
              subject, session, and element grains.
            </CardDescription>
          </div>
          <GrainSelector
            active={grain}
            available={availableGrains}
            onChange={handleGrainChange}
            disabled={summary.isLoading}
          />
        </div>
      </CardHeader>
      <CardBody>
        <PivotBody
          pivot={pivot}
          grain={grain}
        />
      </CardBody>
    </Card>
  );
}

/**
 * Renders only the pivot selector, for composing inside a sidebar navigation
 * or tab bar. Hides itself when the feature flag is off (probe: issues a
 * single /pivot/subject fetch on mount; on 503, render nothing).
 */
export function DatasetPivotNavGuard({
  datasetId,
  children,
}: {
  datasetId: string | undefined;
  children: React.ReactNode;
}) {
  // A minimal probe — we don't need the full body, only the status. Cheap
  // enough to piggy-back on the shared query cache so a subsequent
  // `useDatasetPivot(datasetId, 'subject')` reuses the same response.
  const probe = useDatasetPivot(datasetId, 'subject');
  if (probe.isError && isFeatureDisabled(probe.error)) {
    return null;
  }
  return <>{children}</>;
}

function PivotBody({
  pivot,
  grain,
}: {
  pivot: ReturnType<typeof useDatasetPivot>;
  grain: PivotGrain;
}) {
  if (pivot.isLoading) {
    return <TableLoadingPanel tableType={`${grain} pivot`} rows={10} />;
  }
  if (pivot.isError) {
    return <ErrorState error={pivot.error} onRetry={() => pivot.refetch()} />;
  }
  if (!pivot.data || pivot.data.rows.length === 0) {
    return (
      <p
        className="text-sm text-slate-500 dark:text-slate-400"
        data-testid="pivot-empty"
      >
        No {GRAIN_LABELS[grain].toLowerCase()} rows for this dataset.
      </p>
    );
  }
  return <PivotTable data={pivot.data} grain={grain} />;
}

function PivotTable({ data, grain }: { data: PivotResponse; grain: PivotGrain }) {
  const columns = useMemo<ColumnDef<Record<string, unknown>>[]>(
    () =>
      data.columns.map((col) => ({
        accessorKey: col.key,
        id: col.key,
        header: col.label,
        cell: (info) => formatCell(info.getValue()),
      })),
    [data.columns],
  );
  const table = useReactTable({
    data: data.rows,
    columns,
    getCoreRowModel: getCoreRowModel(),
  });
  return (
    <div className="overflow-x-auto" data-testid="pivot-table">
      <table className="min-w-full text-xs">
        <thead>
          {table.getHeaderGroups().map((hg) => (
            <tr key={hg.id} className="border-b border-slate-200 dark:border-slate-700">
              {hg.headers.map((h) => {
                const def = getColumnDefinition(grain, h.column.id);
                return (
                  <th
                    key={h.id}
                    scope="col"
                    title={def?.description}
                    className="px-2 py-1.5 text-left font-semibold text-slate-700 dark:text-slate-300"
                  >
                    {flexRender(h.column.columnDef.header, h.getContext())}
                  </th>
                );
              })}
            </tr>
          ))}
        </thead>
        <tbody>
          {table.getRowModel().rows.map((row) => (
            <tr
              key={row.id}
              className="border-b border-slate-100 dark:border-slate-800 hover:bg-slate-50 dark:hover:bg-slate-800"
            >
              {row.getVisibleCells().map((cell) => (
                <td
                  key={cell.id}
                  className="px-2 py-1 text-slate-700 dark:text-slate-300 font-mono"
                >
                  {flexRender(cell.column.columnDef.cell, cell.getContext())}
                </td>
              ))}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

function GrainSelector({
  active,
  available,
  onChange,
  disabled,
}: {
  active: PivotGrain;
  available: PivotGrain[];
  onChange: (next: PivotGrain) => void;
  disabled?: boolean;
}) {
  return (
    <label
      className="flex items-center gap-2 text-xs text-slate-600 dark:text-slate-300"
      data-testid="pivot-grain-selector"
    >
      <span className="font-medium">Grain</span>
      <select
        aria-label="Pivot grain"
        className="rounded-md border border-slate-300 dark:border-slate-600 bg-white dark:bg-slate-900 px-2 py-1 text-xs"
        value={active}
        disabled={disabled}
        onChange={(e) => onChange(e.target.value as PivotGrain)}
      >
        {GRAIN_ORDER.map((g) => (
          <option
            key={g}
            value={g}
            disabled={!available.includes(g)}
            data-testid={`pivot-grain-option-${g}`}
          >
            {GRAIN_LABELS[g]}
            {!available.includes(g) && ' (0)'}
          </option>
        ))}
      </select>
    </label>
  );
}

function PivotDisabledCard() {
  return (
    <Card data-testid="pivot-disabled">
      <CardHeader>
        <CardTitle className="text-base">Grain pivot</CardTitle>
        <CardDescription className="text-xs">
          This feature is disabled on the current deployment. Set{' '}
          <code className="font-mono">FEATURE_PIVOT_V1=true</code> to enable.
        </CardDescription>
      </CardHeader>
    </Card>
  );
}

/** Coerce an unknown URL segment to a supported grain; fallback to subject. */
function coerceGrain(raw: string | undefined): PivotGrain {
  if (raw === 'session' || raw === 'element' || raw === 'subject') return raw;
  return 'subject';
}

/**
 * A 503 from /api/datasets/:id/pivot/:grain is exactly the signal that the
 * backend flag is off — the router raises an HTTPException with the
 * feature-flag message. The error code comes through as ``INTERNAL`` per
 * app.py's generic 5xx handler.
 */
function isFeatureDisabled(err: unknown): boolean {
  if (err instanceof ApiError && err.status === 503) {
    return true;
  }
  return false;
}

/** Format a row cell:
 * - ``null`` / ``undefined`` → em-dash (matches MATLAB's blank-cell convention).
 * - Strings: verbatim (never truncate, amendment §4.B1 rule).
 * - Numbers / booleans: ``String(v)``.
 * - Objects: JSON-encoded (rare — e.g. epoch-time structured objects surfaced
 *   from the pivot via depends_on chains in a future extension).
 */
function formatCell(value: unknown): string {
  if (value === null || value === undefined) return '—';
  if (typeof value === 'string') return value;
  if (typeof value === 'number' || typeof value === 'boolean') return String(value);
  try {
    return JSON.stringify(value);
  } catch {
    return String(value);
  }
}
