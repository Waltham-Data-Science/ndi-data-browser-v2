import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { useSearchParams } from 'react-router-dom';
import {
  flexRender,
  getCoreRowModel,
  getFilteredRowModel,
  getSortedRowModel,
  useReactTable,
  type ColumnDef,
  type ColumnFiltersState,
  type SortingState,
  type VisibilityState,
} from '@tanstack/react-table';
import { useVirtualizer } from '@tanstack/react-virtual';
import { ArrowUpDown, Download, Eye, EyeOff, Info } from 'lucide-react';

import type { TableResponse } from '@/api/tables';
import { Button } from '@/components/ui/Button';
import { Input } from '@/components/ui/Input';
import { OntologyPopover } from '@/components/ontology/OntologyPopover';
import { isOntologyTerm } from '@/components/ontology/ontology-utils';
import { useBatchOntologyLookup } from '@/api/ontology';
import { QuickPlot } from '@/components/visualization/QuickPlot';
import { getColumnDefinition } from '@/data/table-column-definitions';
import { cn } from '@/lib/cn';

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

const ROW_HEIGHT = 32;

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

  // Collect every ontology-shaped value in the current rows and fire a
  // batch lookup so the popovers open instantly.
  const ontologyTermIds = useMemo(() => {
    const terms = new Set<string>();
    for (const row of data.rows) {
      for (const col of data.columns) {
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
  }, [data.rows, data.columns, columnOntologyPrefixes]);

  useBatchOntologyLookup(ontologyTermIds);

  // Auto-hide columns whose values are all empty (null / undefined / '' /
  // 0 is kept — rows frequently legitimately use 0 as devTime).
  const autoHiddenColumns = useMemo(() => {
    const hidden: VisibilityState = {};
    if (data.rows.length === 0) return hidden;
    for (const col of data.columns) {
      const allEmpty = data.rows.every((row) => {
        const v = row[col.key];
        return v === null || v === undefined || v === '';
      });
      if (allEmpty) hidden[col.key] = false;
    }
    return hidden;
  }, [data.columns, data.rows]);

  const [columnVisibility, setColumnVisibility] = useState<VisibilityState>(initialHidden);

  const mergedVisibility = useMemo(
    () => ({ ...autoHiddenColumns, ...columnVisibility }),
    [autoHiddenColumns, columnVisibility],
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
      data.columns.map((col) => {
        const colDef = tableType ? getColumnDefinition(tableType, col.key) : undefined;
        const label = colDef?.label ?? col.label;
        return {
          id: col.key,
          accessorFn: (row) => row[col.key],
          header: ({ column }) => (
            <div className="flex items-center gap-1">
              <button
                type="button"
                className="flex items-center gap-1 hover:text-slate-900 dark:hover:text-slate-100 transition-colors text-left"
                onClick={() => column.toggleSorting(column.getIsSorted() === 'asc')}
              >
                <span className="truncate max-w-[200px]">{label}</span>
                <ArrowUpDown className="h-3 w-3 shrink-0 opacity-50" />
              </button>
              {colDef?.description && (
                <span className="relative group">
                  <Info
                    className="h-3 w-3 text-slate-400 dark:text-slate-500 hover:text-slate-600 dark:hover:text-slate-300 cursor-help"
                    aria-label={`Column info: ${label}`}
                  />
                  <span
                    role="tooltip"
                    className="absolute z-50 bottom-full left-0 mb-1 w-64 rounded-md border border-slate-200 dark:border-slate-700 bg-white dark:bg-slate-900 p-2 shadow-lg text-xs text-slate-600 dark:text-slate-400 hidden group-hover:block"
                  >
                    {colDef.description}
                  </span>
                </span>
              )}
            </div>
          ),
          cell: ({ getValue }) => <TableCell value={getValue()} />,
          filterFn: 'includesString' as const,
        };
      }),
    [data.columns, tableType],
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
          <span className="text-xs text-slate-500 dark:text-slate-400 font-mono">
            {table.getFilteredRowModel().rows.length} / {data.rows.length} rows
          </span>
          {hiddenByAuto > 0 && (
            <button
              type="button"
              className="text-[10px] text-slate-500 dark:text-slate-400 hover:text-slate-700 dark:hover:text-slate-200 underline decoration-dotted"
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
        <div className="flex flex-wrap gap-2 p-2 rounded-md border border-slate-200 dark:border-slate-700 bg-slate-50 dark:bg-slate-900">
          {table.getAllLeafColumns().map((column) => {
            const colDef = tableType ? getColumnDefinition(tableType, column.id) : undefined;
            return (
              <label
                key={column.id}
                className="flex items-center gap-1.5 text-xs cursor-pointer"
              >
                <input
                  type="checkbox"
                  checked={column.getIsVisible()}
                  onChange={column.getToggleVisibilityHandler()}
                  className="rounded border-slate-300 dark:border-slate-600"
                />
                <span className="font-mono truncate max-w-[180px]">
                  {colDef?.label ?? column.id}
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

function TableCell({ value }: { value: unknown }) {
  if (value === null || value === undefined) {
    // aria-hidden: the em-dash is a visual null-placeholder, not content.
    return <span className="text-slate-500 dark:text-slate-400" aria-hidden>—</span>;
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
    <span className="font-mono text-xs truncate max-w-[300px] block">
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
          <span className="text-[10px] text-slate-500 dark:text-slate-400">
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

function VirtualizedTable({
  table,
  onRowClick,
}: {
  table: ReturnType<typeof useReactTable<Record<string, unknown>>>;
  onRowClick?: (row: Record<string, unknown>) => void;
}) {
  const scrollRef = useRef<HTMLDivElement>(null);
  const { rows } = table.getRowModel();
  const virtualizer = useVirtualizer({
    count: rows.length,
    getScrollElement: () => scrollRef.current,
    estimateSize: () => ROW_HEIGHT,
    overscan: 20,
  });
  const virtualItems = virtualizer.getVirtualItems();
  const totalSize = virtualizer.getTotalSize();
  const paddingTop = virtualItems.length > 0 ? virtualItems[0].start : 0;
  const paddingBottom =
    virtualItems.length > 0
      ? totalSize - (virtualItems[virtualItems.length - 1].end ?? 0)
      : 0;
  const columnCount = table.getVisibleLeafColumns().length;

  return (
    <div className="relative">
      <div
        ref={scrollRef}
        className="rounded-md border border-slate-200 dark:border-slate-700 overflow-auto max-h-[600px]"
      >
        <table className="w-full text-xs">
          <thead className="sticky top-0 bg-slate-50 dark:bg-slate-900 z-10">
            {table.getHeaderGroups().map((headerGroup) => (
              <tr
                key={headerGroup.id}
                className="border-b border-slate-200 dark:border-slate-700"
              >
                {headerGroup.headers.map((header) => (
                  <th
                    key={header.id}
                    className="px-3 py-2 text-left font-medium text-slate-600 dark:text-slate-300 whitespace-nowrap"
                  >
                    {header.isPlaceholder
                      ? null
                      : flexRender(header.column.columnDef.header, header.getContext())}
                  </th>
                ))}
              </tr>
            ))}
          </thead>
          <tbody>
            {rows.length === 0 ? (
              <tr>
                <td
                  colSpan={columnCount}
                  className="px-3 py-8 text-center text-slate-500 dark:text-slate-400"
                >
                  No data
                </td>
              </tr>
            ) : (
              <>
                {paddingTop > 0 && (
                  <tr>
                    <td
                      colSpan={columnCount}
                      style={{ height: paddingTop, padding: 0, border: 'none' }}
                    />
                  </tr>
                )}
                {virtualItems.map((vr) => {
                  const row = rows[vr.index];
                  return (
                    <tr
                      key={row.id}
                      data-index={vr.index}
                      className={cn(
                        'border-b border-slate-100 dark:border-slate-800',
                        onRowClick &&
                          'cursor-pointer hover:bg-slate-50 dark:hover:bg-slate-800',
                      )}
                      style={{ height: ROW_HEIGHT }}
                      onClick={() => onRowClick?.(row.original)}
                    >
                      {row.getVisibleCells().map((cell) => (
                        <td
                          key={cell.id}
                          className="px-3 py-1.5 whitespace-nowrap align-top"
                        >
                          {flexRender(cell.column.columnDef.cell, cell.getContext())}
                        </td>
                      ))}
                    </tr>
                  );
                })}
                {paddingBottom > 0 && (
                  <tr>
                    <td
                      colSpan={columnCount}
                      style={{ height: paddingBottom, padding: 0, border: 'none' }}
                    />
                  </tr>
                )}
              </>
            )}
          </tbody>
        </table>
      </div>
    </div>
  );
}
