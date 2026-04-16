import { useMemo, useState } from 'react';
import { useParams, Link } from 'react-router-dom';
import { useSummaryTable } from '@/api/tables';
import { TableSkeleton } from '@/components/ui/Skeleton';
import { ErrorState } from '@/components/errors/ErrorState';
import { Input } from '@/components/ui/Input';
import { Card, CardBody } from '@/components/ui/Card';
import { formatNumber } from '@/lib/format';
import { useNavigate } from 'react-router-dom';

export function TableTab() {
  const { id, className } = useParams();
  const [filter, setFilter] = useState('');
  const [sortKey, setSortKey] = useState<string | null>(null);
  const [sortDir, setSortDir] = useState<'asc' | 'desc'>('asc');

  const { data, isLoading, isError, error, refetch } = useSummaryTable(id, className ?? 'subject');

  const rows = useMemo(() => {
    if (!data) return [];
    let r = data.rows;
    if (filter.trim()) {
      const needle = filter.toLowerCase();
      r = r.filter((row) => Object.values(row).some((v) => String(v ?? '').toLowerCase().includes(needle)));
    }
    if (sortKey) {
      const s = sortKey;
      r = [...r].sort((a, b) => {
        const av = a[s];
        const bv = b[s];
        if (av == null && bv == null) return 0;
        if (av == null) return 1;
        if (bv == null) return -1;
        const cmp = String(av).localeCompare(String(bv), undefined, { numeric: true });
        return sortDir === 'asc' ? cmp : -cmp;
      });
    }
    return r;
  }, [data, filter, sortKey, sortDir]);

  return (
    <Card>
      <CardBody className="space-y-3">
        <div className="flex flex-wrap items-center justify-between gap-2">
          <h2 className="text-lg font-semibold capitalize">{className}</h2>
          <Input
            placeholder="Filter rows…"
            value={filter}
            onChange={(e) => setFilter(e.target.value)}
            className="max-w-xs"
          />
        </div>
        {isLoading && <TableSkeleton rows={12} />}
        {isError && <ErrorState error={error} onRetry={() => refetch()} />}
        {data && (
          <>
            <p className="text-xs text-slate-500">
              {formatNumber(rows.length)} of {formatNumber(data.rows.length)} rows
            </p>
            <div className="overflow-auto max-h-[65vh] rounded border border-slate-200 dark:border-slate-800">
              <table className="w-full text-sm">
                <thead className="sticky top-0 bg-slate-50 dark:bg-slate-800">
                  <tr>
                    {data.columns.map((c) => (
                      <th
                        key={c.key}
                        className="px-3 py-2 text-left font-semibold text-slate-600 dark:text-slate-200 cursor-pointer select-none"
                        onClick={() => {
                          if (sortKey === c.key) {
                            setSortDir((d) => (d === 'asc' ? 'desc' : 'asc'));
                          } else {
                            setSortKey(c.key);
                            setSortDir('asc');
                          }
                        }}
                      >
                        {c.label}
                        {sortKey === c.key && <span className="ml-1">{sortDir === 'asc' ? '↑' : '↓'}</span>}
                      </th>
                    ))}
                  </tr>
                </thead>
                <tbody>
                  {rows.map((row, i) => (
                    <Row key={i} row={row} columns={data.columns} datasetId={id!} />
                  ))}
                </tbody>
              </table>
            </div>
          </>
        )}
      </CardBody>
    </Card>
  );
}

function Row({
  row,
  columns,
  datasetId,
}: {
  row: Record<string, unknown>;
  columns: { key: string; label: string }[];
  datasetId: string;
}) {
  const navigate = useNavigate();
  const docId = row.subjectId ?? row.probeId ?? row.epochId ?? row.id ?? row._id;
  const clickable = typeof docId === 'string';
  return (
    <tr
      className={`border-t border-slate-100 dark:border-slate-800 ${clickable ? 'hover:bg-slate-50 dark:hover:bg-slate-800 cursor-pointer' : ''}`}
      onClick={() => {
        if (clickable) navigate(`/datasets/${datasetId}/documents/${docId}`);
      }}
    >
      {columns.map((c) => (
        <td key={c.key} className="px-3 py-1.5 align-top text-slate-800 dark:text-slate-200">
          {formatCell(row[c.key])}
        </td>
      ))}
    </tr>
  );
}

function formatCell(v: unknown): React.ReactNode {
  if (v == null) return <span className="text-slate-400">—</span>;
  if (typeof v === 'object') return <span className="font-mono text-xs">{JSON.stringify(v)}</span>;
  const s = String(v);
  // Looks like an ontology term? Render as link.
  const m = s.match(/^([A-Za-z]+):([A-Za-z0-9_.-]+)$/);
  if (m && ['CL', 'NCBITaxon', 'CHEBI', 'PATO', 'EFO', 'RRID', 'WBStrain', 'PubChem'].includes(m[1])) {
    return <Link to={`/query?term=${encodeURIComponent(s)}`} className="underline decoration-dotted hover:text-brand-600">{s}</Link>;
  }
  return s;
}
