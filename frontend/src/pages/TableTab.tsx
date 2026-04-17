import { useMemo } from 'react';
import { useNavigate, useParams } from 'react-router-dom';
import {
  useCombinedTable,
  useOntologyTables,
  useSummaryTable,
  type OntologyTableGroup,
  type TableResponse,
  type TableType,
} from '@/api/tables';
import { useClassCounts } from '@/api/datasets';
import { SummaryTableView } from '@/components/tables/SummaryTableView';
import { TableSelector } from '@/components/tables/TableSelector';
import { Card, CardBody } from '@/components/ui/Card';
import { ErrorState } from '@/components/errors/ErrorState';
import { TableLoadingPanel } from '@/components/ui/Skeleton';
import { useState } from 'react';

type AllowedType = TableType;

function coerceTableType(raw: string | undefined): AllowedType {
  const allowed: AllowedType[] = [
    'combined', 'subject', 'element', 'element_epoch',
    'treatment', 'probe_location', 'openminds_subject', 'ontology',
  ];
  if (!raw) return 'subject';
  // Legacy slug compatibility: "subjects" -> "subject", "probes" -> "element".
  const normalized = raw.toLowerCase();
  const aliasMap: Record<string, AllowedType> = {
    subjects: 'subject',
    probes: 'element',
    epochs: 'element_epoch',
    epoch: 'element_epoch',
    elements: 'element',
    probe: 'element',
    treatments: 'treatment',
    locations: 'probe_location',
    openminds: 'openminds_subject',
  };
  const resolved = (aliasMap[normalized] ?? normalized) as AllowedType;
  return allowed.includes(resolved) ? resolved : 'subject';
}

export function TableTab() {
  const { id, className } = useParams();
  const navigate = useNavigate();
  const active = coerceTableType(className);

  const classCounts = useClassCounts(id);
  const counts = useMemo<Partial<Record<TableType, number>>>(() => {
    if (!classCounts.data) return {};
    const c = classCounts.data.classCounts;
    return {
      subject: c.subject ?? 0,
      element: c.element ?? c.probe ?? 0,
      element_epoch: c.element_epoch ?? c.epoch ?? 0,
      treatment: c.treatment ?? 0,
      probe_location: c.probe_location ?? 0,
      openminds_subject: c.openminds_subject ?? 0,
    };
  }, [classCounts.data]);

  const handleChange = (next: TableType) => {
    if (!id) return;
    navigate(`/datasets/${id}/tables/${next}`);
  };

  return (
    <div className="space-y-3">
      <TableSelector active={active} onChange={handleChange} counts={counts} />
      <Card>
        <CardBody>
          {active === 'ontology' ? (
            <OntologyTablesView datasetId={id} />
          ) : active === 'combined' ? (
            <CombinedTableView datasetId={id} />
          ) : (
            <SingleClassTableView datasetId={id} className={active} />
          )}
        </CardBody>
      </Card>
    </div>
  );
}

function SingleClassTableView({
  datasetId,
  className,
}: {
  datasetId: string | undefined;
  className: Exclude<TableType, 'combined' | 'ontology'>;
}) {
  const navigate = useNavigate();
  const { data, isLoading, isError, error, refetch } = useSummaryTable(datasetId, className);

  return (
    <TableBody
      data={data}
      isLoading={isLoading}
      isError={isError}
      error={error}
      onRetry={() => refetch()}
      tableType={className}
      title={className}
      datasetId={datasetId}
      onRowClick={
        datasetId
          ? (row) => {
              const docId = pickDocId(row);
              if (docId) navigate(`/datasets/${datasetId}/documents/${docId}`);
            }
          : undefined
      }
    />
  );
}

function CombinedTableView({ datasetId }: { datasetId: string | undefined }) {
  const navigate = useNavigate();
  const { data, isLoading, isError, error, refetch } = useCombinedTable(datasetId);
  return (
    <TableBody
      data={data}
      isLoading={isLoading}
      isError={isError}
      error={error}
      onRetry={() => refetch()}
      tableType="combined"
      title="combined"
      onRowClick={
        datasetId
          ? (row) => {
              const docId = pickDocId(row);
              if (docId) navigate(`/datasets/${datasetId}/documents/${docId}`);
            }
          : undefined
      }
    />
  );
}

function OntologyTablesView({ datasetId }: { datasetId: string | undefined }) {
  const { data, isLoading, isError, error, refetch } = useOntologyTables(datasetId);
  const [groupIdx, setGroupIdx] = useState(0);

  if (isLoading) return <TableLoadingPanel tableType="ontology" rows={12} />;
  if (isError) return <ErrorState error={error} onRetry={() => refetch()} />;
  if (!data || data.groups.length === 0) {
    return (
      <p className="text-sm text-slate-500">
        This dataset has no ontology table rows.
      </p>
    );
  }
  const active = data.groups[Math.min(groupIdx, data.groups.length - 1)];
  return (
    <div className="space-y-3">
      <OntologyGroupPicker
        groups={data.groups}
        active={groupIdx}
        onChange={setGroupIdx}
      />
      <SummaryTableView
        data={active.table}
        title={`ontology-${groupIdx}`}
        tableType="ontology"
        columnOntologyPrefixes={buildColumnOntology(active)}
      />
    </div>
  );
}

function OntologyGroupPicker({
  groups,
  active,
  onChange,
}: {
  groups: OntologyTableGroup[];
  active: number;
  onChange: (n: number) => void;
}) {
  if (groups.length <= 1) return null;
  return (
    <div className="flex flex-wrap gap-1 border-b border-slate-200 dark:border-slate-700 pb-px">
      {groups.map((g, i) => {
        const label = g.variableNames.slice(0, 2).join(' + ');
        return (
          <button
            key={i}
            type="button"
            onClick={() => onChange(i)}
            className={
              i === active
                ? 'px-2 py-1 text-xs font-medium rounded-t-md bg-white dark:bg-slate-900 text-slate-900 dark:text-slate-100 border border-slate-200 dark:border-slate-700 border-b-white dark:border-b-slate-900 -mb-px'
                : 'px-2 py-1 text-xs font-medium rounded-t-md text-slate-600 dark:text-slate-400 hover:text-slate-900 dark:hover:text-slate-100'
            }
          >
            <span className="font-mono truncate max-w-[200px] inline-block align-bottom">
              {label}
              {g.variableNames.length > 2 && '…'}
            </span>
            <span className="ml-1.5 text-[10px] text-slate-400">
              {g.rowCount.toLocaleString()}
            </span>
          </button>
        );
      })}
    </div>
  );
}

function buildColumnOntology(group: OntologyTableGroup): Record<string, string | null> {
  const out: Record<string, string | null> = {};
  for (let i = 0; i < group.variableNames.length; i++) {
    const key = group.variableNames[i];
    out[key] = group.ontologyNodes[i] ?? null;
  }
  return out;
}

function TableBody({
  data,
  isLoading,
  isError,
  error,
  onRetry,
  tableType,
  title,
  onRowClick,
  datasetId,
}: {
  data: TableResponse | undefined;
  isLoading: boolean;
  isError: boolean;
  error: unknown;
  onRetry: () => void;
  tableType: string;
  title: string;
  onRowClick?: (row: Record<string, unknown>) => void;
  datasetId?: string;
}) {
  if (isLoading) return <TableLoadingPanel tableType={tableType} rows={12} />;
  if (isError) return <ErrorState error={error} onRetry={onRetry} />;
  if (!data) return null;
  return (
    <SummaryTableView
      data={data}
      title={title}
      tableType={tableType}
      onRowClick={onRowClick}
      datasetId={datasetId}
    />
  );
}

function pickDocId(row: Record<string, unknown>): string | undefined {
  const candidates = [
    row.subjectDocumentIdentifier,
    row.probeDocumentIdentifier,
    row.epochDocumentIdentifier,
    row.subjectId,
    row.probeId,
    row.epochId,
    row.documentIdentifier,
    row.id,
  ];
  for (const c of candidates) {
    if (typeof c === 'string' && c) return c;
  }
  return undefined;
}
