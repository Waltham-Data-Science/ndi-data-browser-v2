import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { useSearchParams } from 'react-router-dom';
import {
  getCoreRowModel,
  getFilteredRowModel,
  getSortedRowModel,
  useReactTable,
  type ColumnDef,
  type ColumnFiltersState,
  type SortingState,
  type VisibilityState,
} from '@tanstack/react-table';
import { ArrowUpDown, Download, Eye, EyeOff, Info } from 'lucide-react';

import type { TableResponse } from '@/api/tables';
import { Button } from '@/components/ui/Button';
import { FloatingPanel } from '@/components/ui/FloatingPanel';
import { Input } from '@/components/ui/Input';
import { OntologyPopover } from '@/components/ontology/OntologyPopover';
import { isOntologyTerm } from '@/components/ontology/ontology-utils';
import { useBatchOntologyLookup } from '@/api/ontology';
import { QuickPlot } from '@/components/visualization/QuickPlot';
import { VirtualizedTable } from '@/components/tables/VirtualizedTable';
import {
  getColumnDefinition,
  resolveDefaultColumns,
  type ColumnDefault,
  type ColumnFormatter,
} from '@/data/table-column-definitions';

interface SummaryTableViewProps {
  data: TableResponse;
  title?: string;
  /** Backend class name (`subject`, `element`, `element_epoch`, `treatment`,
   * `combined`, `ontology`) — drives the column-definition lookup that
   * powers header tooltips and per-column ontology hints. */
  tableType?: string;
  onRowClick?: (row: Record<string, unknown>) => void;
  /** Optional override — used by the ontology-table path where per-column
   * ontologyTerm comes from the backend, not our static definitions. */
  columnOntologyPrefixes?: Record<string, string | null>;
  /** When the page knows which dataset backs this table, pass it in to
   * enable the QuickPlot card (calls /api/visualize/distribution). */
  datasetId?: string;
}

/**
 * Fully-featured summary table. Ported from v1 with four v2 adaptations:
 *
 * 1. Columns are `{key, label}[]` objects (v2 backend) instead of `string[]`
 *    (v1). Accessor / header / filter all key off `col.key`; `col.label` is
 *    the default header text when no column-definition tooltip applies.
 * 2. Cell renderer handles structured values — specifically the
 *    `{devTime, globalTime}` epochStart/epochStop objects from
 *    `_normalize_t0_t1()`. Everything else falls through to JSON.
 * 3. Ontology-term detection still runs on stringified values; the
 *    ported `normalizeOntologyTerm` handles Van Hooser's bare NCBI ids.
 * 4. `useBatchOntologyLookup` pre-seeds the TanStack Query cache for every
 *    ontology term visible in the table so popover opens hit warm.
 */
export function SummaryTableView({
  data,
  title,
  tableType,
  onRowClick,
  columnOntologyPrefixes,
  datasetId,
}: SummaryTableViewProps) {
  // URL-persisted table state (M7 §5). Three params, deliberately short
  // names to keep deep-link URLs readable:
  //   ?tq=<global-filter>
  //   ?tsort=<columnKey>:<asc|desc>
  //   ?thide=<comma-separated column keys to FORCE-hide>
  // The prefix `t` scopes these under the SummaryTableView namespace so they
  // don't collide with page-level params (DocumentExplorerPage uses `mode`
  // `class` `page`; DatasetsPage uses `q` `page`; QueryBuilder owns `op`
  // `field` `param1` `scope`). Per-column filters are intentionally NOT
  // serialized — the table UI currently exposes only the global filter, and
  // serializing per-column state would multiply URL noise for a feature
  // nobody can trigger from the UI.
  const [searchParams, setSearchParams] = useSearchParams();

  const initialGlobalFilter = searchParams.get('tq') ?? '';
  const initialSorting = useMemo<SortingState>(() => {
    const raw = searchParams.get('tsort');
    if (!raw) return [];
    const [col, dir] = raw.split(':');
    if (!col) return [];
    return [{ id: col, desc: dir === 'desc' }];
  }, [searchParams]);
  const initialHidden = useMemo<VisibilityState>(() => {
    const raw = searchParams.get('thide');
    if (!raw) return {};
    const out: VisibilityState = {};
    for (const k of raw.split(',')) {
      const trimmed = k.trim();
      if (trimmed) out[trimmed] = false;
    }
    return out;
  }, [searchParams]);

  const [sorting, setSorting] = useState<SortingState>(initialSorting);
  const [columnFilters, setColumnFilters] = useState<ColumnFiltersState>([]);
  const [globalFilter, setGlobalFilter] = useState(initialGlobalFilter);
  const [showColumnPicker, setShowColumnPicker] = useState(false);

  /** Write a single param to the URL, deleting when falsy to keep the URL
   * clean on the default/empty case. `replace: true` to avoid stacking
   * history entries on every keystroke. */
  const updateParam = useCallback(
    (key: string, value: string | null) => {
      setSearchParams(
        (prev) => {
          const next = new URLSearchParams(prev);
          if (value == null || value === '') next.delete(key);
          else next.set(key, value);
          return next;
        },
        { replace: true },
      );
    },
    [setSearchParams],
  );

  // B6a: canonical column defaults for subject/probe/epoch grains. The
  // column list and its default visibility come from NDI-matlab's
  // `docTable.subject`/`probe`/`epoch` per Plan B amendment §4.B6a. For
  // other grains (combined, ontology, treatment, probe_location,
  // openminds_subject) we fall back to the backend-provided column list
  // with no defaults.
  const canonicalDefaults = useMemo<ColumnDefault[]>(
    () => (tableType ? resolveDefaultColumns(tableType, data.rows) : []),
    [tableType, data.rows],
  );

  /** Index of canonical default rules by column id — consulted below for
   * per-column visibility and formatting. */
  const canonicalById = useMemo(() => {
    const out = new Map<string, ColumnDefault>();
    for (const c of canonicalDefaults) out.set(c.id, c);
    return out;
  }, [canonicalDefaults]);

  /** Ordered column list: canonical order first, then any backend columns
   * the canonical list doesn't know about (preserves server-driven columns
   * while still honoring tutorial ordering).
   *
   * Preserve the backend's `label` when no canonical rule exists — the
   * backend ships "Subject Doc ID" labels etc. that match our canonical
   * headers, and for unknown columns we want the server's text, not a
   * fallback. */
  const orderedColumns = useMemo(() => {
    const srcColumns = data.columns;
    if (canonicalDefaults.length === 0) return srcColumns;
    const bySrcKey = new Map(srcColumns.map((c) => [c.key, c]));
    const result: typeof srcColumns = [];
    const emitted = new Set<string>();
    for (const c of canonicalDefaults) {
      const src = bySrcKey.get(c.id);
      if (src) {
        result.push(src);
        emitted.add(c.id);
      }
    }
    for (const c of srcColumns) {
      if (!emitted.has(c.key)) result.push(c);
    }
    return result;
  }, [canonicalDefaults, data]);

  // Collect every ontology-shaped value in the current rows and fire a
  // batch lookup so the popovers open instantly.
  const ontologyTermIds = useMemo(() => {
    const terms = new Set<string>();
    for (const row of data.rows) {
      for (const col of orderedColumns) {
        const v = row[col.key];
        if (typeof v === 'string' && isOntologyTerm(v)) {
          terms.add(v.trim());
        }
      }
    }
    // Also include per-column ontology hints from the ontology-table path.
    if (columnOntologyPrefixes) {
      for (const term of Object.values(columnOntologyPrefixes)) {
        if (term && isOntologyTerm(term)) terms.add(term);
      }
    }
    return [...terms];
  }, [data.rows, orderedColumns, columnOntologyPrefixes]);

  useBatchOntologyLookup(ontologyTermIds);

  // Auto-hide columns whose values are all empty (null / undefined / '' /
  // 0 is kept — rows frequently legitimately use 0 as devTime).
  const autoHiddenColumns = useMemo(() => {
    const hidden: VisibilityState = {};
    if (data.rows.length === 0) return hidden;
    for (const col of orderedColumns) {
      const allEmpty = data.rows.every((row) => {
        const v = row[col.key];
        return v === null || v === undefined || v === '';
      });
      if (allEmpty) hidden[col.key] = false;
    }
    return hidden;
  }, [orderedColumns, data.rows]);

  /** B6a: canonical-default-visibility — columns that are `visible: false`
   * in the canonical list (e.g. `sessionDocumentIdentifier` on the subject
   * grain) start hidden but remain exposed in the column-toggle picker.
   * Merged below the auto-hide layer so explicit user toggles still win. */
  const canonicalHiddenColumns = useMemo<VisibilityState>(() => {
    const out: VisibilityState = {};
    for (const c of canonicalDefaults) {
      if (!c.visible) out[c.id] = false;
    }
    return out;
  }, [canonicalDefaults]);

  const [columnVisibility, setColumnVisibility] = useState<VisibilityState>(initialHidden);

  const mergedVisibility = useMemo(
    () => ({ ...canonicalHiddenColumns, ...autoHiddenColumns, ...columnVisibility }),
    [canonicalHiddenColumns, autoHiddenColumns, columnVisibility],
  );

  // Push state -> URL whenever the user changes filter/sort/visibility.
  // Read-back-from-URL uses useState initializers above; from here on the
  // URL is a projection of component state, not the source of truth.
  useEffect(() => {
    updateParam('tq', globalFilter);
  }, [globalFilter, updateParam]);

  useEffect(() => {
    if (sorting.length === 0) {
      updateParam('tsort', null);
    } else {
      const s = sorting[0];
      updateParam('tsort', `${s.id}:${s.desc ? 'desc' : 'asc'}`);
    }
  }, [sorting, updateParam]);

  useEffect(() => {
    // Only serialize user-forced hides, not auto-hidden-empty columns.
    const hidden = Object.entries(columnVisibility)
      .filter(([, visible]) => visible === false)
      .map(([k]) => k);
    updateParam('thide', hidden.length ? hidden.join(',') : null);
  }, [columnVisibility, updateParam]);

  const columns = useMemo<ColumnDef<Record<string, unknown>>[]>(
    () =>
      orderedColumns.map((col) => {
        const colDef = tableType ? getColumnDefinition(tableType, col.key) : undefined;
        const canonical = canonicalById.get(col.key);
        // Precedence: canonical header > tooltip label > backend label.
        // Tooltip description (when present) always wins for the info-icon.
        const label = canonical?.header ?? colDef?.label ?? col.label;
        const formatter = canonical?.formatter;
        return {
          id: col.key,
          accessorFn: (row) => row[col.key],
          header: ({ column }) => (
            <div className="flex items-center gap-1">
              <button
                type="button"
                className="flex items-center gap-1 hover:text-gray-900:text-gray-100 transition-colors text-left"
                onClick={() => column.toggleSorting(column.getIsSorted() === 'asc')}
              >
                <span className="truncate max-w-[200px] md:max-w-[300px] lg:max-w-[420px]">{label}</span>
                <ArrowUpDown className="h-3 w-3 shrink-0 opacity-50" />
              </button>
              {colDef?.description && (
                <ColumnInfoTip label={label} description={colDef.description} />
              )}
            </div>
          ),
          cell: ({ getValue }) => <TableCell value={getValue()} formatter={formatter} />,
          filterFn: 'includesString' as const,
        };
      }),
    [orderedColumns, tableType, canonicalById],
  );

  const table = useReactTable({
    data: data.rows,
    columns,
    state: {
      sorting,
      columnFilters,
      columnVisibility: mergedVisibility,
      globalFilter,
    },
    onSortingChange: setSorting,
    onColumnFiltersChange: setColumnFilters,
    onColumnVisibilityChange: setColumnVisibility,
    onGlobalFilterChange: setGlobalFilter,
    getCoreRowModel: getCoreRowModel(),
    getSortedRowModel: getSortedRowModel(),
    getFilteredRowModel: getFilteredRowModel(),
    /** Stringify every value (including structured {devTime, globalTime}
     * objects) before global-filter matching so "739256" matches. */
    globalFilterFn: (row, _columnId, filterValue: string) => {
      const needle = String(filterValue).toLowerCase();
      if (!needle) return true;
      for (const v of Object.values(row.original as Record<string, unknown>)) {
        if (v === null || v === undefined) continue;
        const stringified =
          typeof v === 'object' ? JSON.stringify(v) : String(v);
        if (stringified.toLowerCase().includes(needle)) return true;
      }
      return false;
    },
  });

  const exportCsv = () => {
    const rows = table.getFilteredRowModel().rows;
    const cols = table.getVisibleLeafColumns().map((c) => c.id);
    const header = cols.join(',');
    const body = rows
      .map((row) =>
        cols
          .map((colId) => {
            const val = row.getValue(colId);
            const raw =
              val === null || val === undefined
                ? ''
                : typeof val === 'object'
                  ? JSON.stringify(val)
                  : String(val);
            return raw.includes(',') || raw.includes('"') || raw.includes('\n')
              ? `"${raw.replace(/"/g, '""')}"`
              : raw;
          })
          .join(','),
      )
      .join('\n');
    const blob = new Blob([header + '\n' + body], { type: 'text/csv;charset=utf-8' });
    const url = URL.createObjectURL(blob);
    const a = document.createElement('a');
    a.href = url;
    a.download = `${title || 'table'}.csv`;
    a.click();
    URL.revokeObjectURL(url);
  };

  const exportJson = () => {
    const rows = table.getFilteredRowModel().rows.map((r) => r.original);
    const blob = new Blob([JSON.stringify(rows, null, 2)], {
      type: 'application/json',
    });
    const url = URL.createObjectURL(blob);
    const a = document.createElement('a');
    a.href = url;
    a.download = `${title || 'table'}.json`;
    a.click();
    URL.revokeObjectURL(url);
  };

  // Plan B B4 — amendment §4.B4 calls out CSV + XLS as first-class
  // because NDI-matlab's default is `.xls` via `writetable`. Dynamic
  // import keeps the xlsx chunk out of the main bundle. Cells are flat
  // primitives; structured values (e.g. the `{devTime, globalTime}`
  // pairs from `_normalize_t0_t1`) are JSON-stringified to a single
  // Excel cell so the spreadsheet stays tabular.
  const exportXlsx = async () => {
    const xlsx = await import('xlsx');
    const rows = table.getFilteredRowModel().rows;
    const cols = table.getVisibleLeafColumns().map((c) => c.id);
    const aoa: unknown[][] = [cols];
    for (const row of rows) {
      const record: unknown[] = [];
      for (const colId of cols) {
        const val = row.getValue(colId);
        if (val === null || val === undefined) {
          record.push('');
        } else if (typeof val === 'object') {
          record.push(JSON.stringify(val));
        } else {
          record.push(val as string | number | boolean);
        }
      }
      aoa.push(record);
    }
    const sheet = xlsx.utils.aoa_to_sheet(aoa);
    const book = xlsx.utils.book_new();
    // Excel sheet-name cap: 31 chars, forbid []:*?/\
    const safeSheetName =
      (title || 'table').replace(/[\\/?*[\]:]/g, '_').slice(0, 31) || 'table';
    xlsx.utils.book_append_sheet(book, sheet, safeSheetName);
    xlsx.writeFile(book, `${title || 'table'}.xlsx`);
  };

  const hiddenByAuto = Object.keys(autoHiddenColumns).length;

  return (
    <div className="space-y-3">
      {/* Toolbar */}
      <div className="flex items-center justify-between gap-2 flex-wrap">
        <div className="flex items-center gap-2">
          <Input
            placeholder="Filter all columns…"
            value={globalFilter}
            onChange={(e) => setGlobalFilter(e.target.value)}
            className="h-8 w-64 text-xs"
          />
          <span className="text-xs text-gray-500 font-mono">
            {table.getFilteredRowModel().rows.length} / {data.rows.length} rows
          </span>
          {hiddenByAuto > 0 && (
            <button
              type="button"
              className="text-[10px] text-gray-500 hover:text-gray-700:text-gray-200 underline decoration-dotted"
              onClick={() => {
                const anyHidden = Object.keys(autoHiddenColumns).some(
                  (col) => mergedVisibility[col] === false,
                );
                if (anyHidden) {
                  const overrides: VisibilityState = {};
                  for (const col of Object.keys(autoHiddenColumns)) {
                    overrides[col] = true;
                  }
                  setColumnVisibility(overrides);
                } else {
                  setColumnVisibility({});
                }
              }}
            >
              {Object.keys(autoHiddenColumns).some(
                (col) => mergedVisibility[col] === false,
              )
                ? `+${hiddenByAuto} empty cols hidden`
                : 'Hide empty cols'}
            </button>
          )}
        </div>
        <div className="flex items-center gap-1">
          <Button
            variant="secondary"
            size="sm"
            className="h-7 text-xs"
            onClick={() => setShowColumnPicker(!showColumnPicker)}
          >
            {showColumnPicker ? (
              <EyeOff className="h-3 w-3 mr-1" />
            ) : (
              <Eye className="h-3 w-3 mr-1" />
            )}
            Columns
          </Button>
          <Button
            variant="secondary"
            size="sm"
            className="h-7 text-xs"
            onClick={exportCsv}
            data-testid="export-csv"
          >
            <Download className="h-3 w-3 mr-1" />
            CSV
          </Button>
          <Button
            variant="secondary"
            size="sm"
            className="h-7 text-xs"
            onClick={exportXlsx}
            data-testid="export-xlsx"
          >
            <Download className="h-3 w-3 mr-1" />
            XLS
          </Button>
          <Button
            variant="secondary"
            size="sm"
            className="h-7 text-xs"
            onClick={exportJson}
            data-testid="export-json"
          >
            <Download className="h-3 w-3 mr-1" />
            JSON
          </Button>
        </div>
      </div>

      {/* Column visibility picker */}
      {showColumnPicker && (
        <div className="flex flex-wrap gap-2 p-2 rounded-md border border-gray-200 bg-gray-50">
          {table.getAllLeafColumns().map((column) => {
            const colDef = tableType ? getColumnDefinition(tableType, column.id) : undefined;
            // Precedence matches the header render above — canonical header
            // wins so the picker agrees with the column-header text.
            const canonical = canonicalById.get(column.id);
            const label = canonical?.header ?? colDef?.label ?? column.id;
            return (
              <label
                key={column.id}
                className="flex items-center gap-1.5 text-xs cursor-pointer"
              >
                <input
                  type="checkbox"
                  checked={column.getIsVisible()}
                  onChange={column.getToggleVisibilityHandler()}
                  className="rounded border-gray-300"
                />
                <span className="font-mono truncate max-w-[180px] md:max-w-[280px] lg:max-w-[380px]">
                  {label}
                </span>
              </label>
            );
          })}
        </div>
      )}

      {/* Virtualized scrolling table */}
      <VirtualizedTable table={table} onRowClick={onRowClick} />

      {/* M6: QuickPlot embedded below the table. Renders only when the
          page passes a datasetId + tableType (i.e. not the ontology-table
          path, which has no class_name). */}
      {datasetId && tableType && tableType !== 'ontology' && tableType !== 'combined' && (
        <QuickPlot datasetId={datasetId} className={tableType} table={data} />
      )}
    </div>
  );
}

/**
 * Column-header `ℹ` info icon with its explainer tooltip. Renders the
 * tooltip via `FloatingPanel` (portal to `document.body`, `position:
 * fixed`) so it isn't clipped by the table's `overflow-auto` scroll
 * wrapper. Replaces a previous CSS-only `group-hover:block` tooltip
 * that was invisible on top-row headers — Steve's 2026-04-19 report.
 *
 * Hover semantics are simple show-on-enter / hide-on-leave (matching
 * the old CSS behavior) — no delay is needed because the user
 * explicitly hovered the info icon.
 */
function ColumnInfoTip({
  label,
  description,
}: {
  label: string;
  description: string;
}) {
  const [open, setOpen] = useState(false);
  const iconRef = useRef<HTMLSpanElement>(null);
  return (
    <>
      <span
        ref={iconRef}
        className="inline-flex"
        onMouseEnter={() => setOpen(true)}
        onMouseLeave={() => setOpen(false)}
        onFocus={() => setOpen(true)}
        onBlur={() => setOpen(false)}
      >
        <Info
          className="h-3 w-3 text-gray-400 hover:text-gray-600:text-gray-300 cursor-help"
          aria-label={`Column info: ${label}`}
          tabIndex={0}
        />
      </span>
      <FloatingPanel
        open={open}
        anchorRef={iconRef}
        preferredPlacement="above"
        width={256}
        estimatedHeight={80}
        className="rounded-md border border-gray-200 bg-white p-2 shadow-lg text-xs text-gray-600"
      >
        {description}
      </FloatingPanel>
    </>
  );
}

function TableCell({
  value,
  formatter,
}: {
  value: unknown;
  /** Optional column-level formatter — e.g. CSV-join for array cells. If
   * it returns a string, that replaces the default rendering; returning
   * `undefined` falls through to the default branches below. */
  formatter?: ColumnFormatter;
}) {
  if (value === null || value === undefined) {
    // aria-hidden: the em-dash is a visual null-placeholder, not content.
    return <span className="text-gray-500" aria-hidden>—</span>;
  }
  // Let the column-level formatter override first — specifically the
  // CSV-join formatter for array cells, which matches MATLAB's
  // `join({...}, ', ')` shape per Plan B amendment §4.B6a.
  if (formatter) {
    const formatted = formatter(value);
    if (typeof formatted === 'string') {
      return (
        <span className="font-mono text-xs truncate max-w-[300px] md:max-w-[440px] lg:max-w-[600px] block">
          {formatted}
        </span>
      );
    }
  }
  if (typeof value === 'object' && !Array.isArray(value)) {
    return <EpochTimeCell value={value as Record<string, unknown>} />;
  }
  if (Array.isArray(value)) {
    return (
      <span className="font-mono text-xs">{JSON.stringify(value)}</span>
    );
  }
  const str = String(value);
  if (typeof value === 'string' && isOntologyTerm(str)) {
    const trimmed = str.trim();
    // M6: "Find everywhere" cross-link. We pre-load the QueryBuilder with
    // `contains_string` on ontology-shaped fields (covers openminds
    // preferredOntologyIdentifier / ontologyIdentifier, probe_location
    // ontology_name, treatment ontologyName, and ontologyTableRow nodes).
    const findEverywherePath = `/query?op=contains_string&field=openminds.fields.preferredOntologyIdentifier&param1=${encodeURIComponent(trimmed)}`;
    return <OntologyPopover termId={trimmed} findEverywherePath={findEverywherePath} />;
  }
  return (
    <span className="font-mono text-xs truncate max-w-[300px] md:max-w-[440px] lg:max-w-[600px] block">
      {str}
    </span>
  );
}

function EpochTimeCell({ value }: { value: Record<string, unknown> }) {
  // Recognize the {devTime, globalTime} shape produced by
  // summary_table_service._normalize_t0_t1.
  if ('devTime' in value || 'globalTime' in value) {
    const dev = value.devTime;
    const global = value.globalTime;
    return (
      <span
        className="font-mono text-xs flex flex-col leading-tight"
        title={`devTime=${String(dev)}${
          global !== null && global !== undefined ? `, globalTime=${String(global)}` : ''
        }`}
      >
        <span>{dev === null || dev === undefined ? '—' : String(dev)}</span>
        {global !== null && global !== undefined && (
          <span className="text-[10px] text-gray-500">
            {String(global)}
          </span>
        )}
      </span>
    );
  }
  return (
    <span className="font-mono text-xs">{JSON.stringify(value)}</span>
  );
}

// VirtualizedTable used to live here. Extracted to
// `@/components/tables/VirtualizedTable` (audit 2026-04-23, #63) so
// PivotView can reuse it. This file imports it at the top.
