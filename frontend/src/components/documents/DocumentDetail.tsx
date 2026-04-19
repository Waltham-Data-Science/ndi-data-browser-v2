import type { ReactElement } from 'react';
import { Calendar, File, FileText, Link2 } from 'lucide-react';
import { Link } from 'react-router-dom';

import type { DocumentSummary } from '@/api/documents';
import { Badge } from '@/components/ui/Badge';
import { Card, CardBody, CardHeader, CardTitle } from '@/components/ui/Card';
import { Separator } from '@/components/ui/Separator';
import { formatDate } from '@/lib/format';

interface DocumentDetailViewProps {
  document: DocumentSummary;
  datasetId?: string;
}

/** Color-coded JSON tree — ported from v1 with a small cap on large
 * nested arrays to avoid rendering 10k-entry lists inline. */
function JsonTree({
  data,
  depth = 0,
  keyHint,
}: {
  data: unknown;
  depth?: number;
  keyHint?: string;
}): ReactElement {
  if (data === null || data === undefined) {
    return <span className="text-slate-500 dark:text-slate-400">null</span>;
  }
  if (typeof data === 'boolean') {
    return <span className="text-blue-500 dark:text-blue-400">{data ? 'true' : 'false'}</span>;
  }
  if (typeof data === 'number') {
    return <span className="text-emerald-600 dark:text-emerald-400">{data}</span>;
  }
  if (typeof data === 'string') {
    if (data.length > 200) {
      return (
        <span className="text-amber-700 dark:text-amber-300">
          &quot;{data.slice(0, 200)}…&quot;
        </span>
      );
    }
    return (
      <span className="text-amber-700 dark:text-amber-300">
        &quot;{data}&quot;
      </span>
    );
  }
  if (Array.isArray(data)) {
    if (data.length === 0) {
      return <span className="text-slate-500 dark:text-slate-400">[]</span>;
    }
    const maxItems = 100;
    const truncated = data.length > maxItems;
    const items = truncated ? data.slice(0, maxItems) : data;
    return (
      <div className={depth > 0 ? 'pl-3 border-l border-slate-200 dark:border-slate-700/50' : ''}>
        {items.map((item, i) => (
          <div key={i} className="py-0.5">
            <span className="text-slate-500 dark:text-slate-400 text-[10px] mr-1">[{i}]</span>
            <JsonTree data={item} depth={depth + 1} />
          </div>
        ))}
        {truncated && (
          <div className="text-[10px] text-slate-500 dark:text-slate-400 italic py-0.5">
            + {data.length - maxItems} more items ({keyHint ?? ''})
          </div>
        )}
      </div>
    );
  }
  if (typeof data === 'object') {
    const entries = Object.entries(data as Record<string, unknown>);
    if (entries.length === 0) {
      return <span className="text-slate-500 dark:text-slate-400">{'{}'}</span>;
    }
    return (
      <div
        className={
          depth > 0 ? 'pl-3 border-l border-slate-200 dark:border-slate-700/50' : ''
        }
      >
        {entries.map(([k, v]) => (
          <div key={k} className="py-0.5">
            <span className="text-purple-600 dark:text-purple-400 font-medium">{k}</span>
            <span className="text-slate-500 dark:text-slate-400">: </span>
            <JsonTree data={v} depth={depth + 1} keyHint={k} />
          </div>
        ))}
      </div>
    );
  }
  return <span>{String(data)}</span>;
}

export function DocumentDetailView({ document: doc, datasetId }: DocumentDetailViewProps) {
  const data = (doc.data ?? {}) as Record<string, unknown>;
  const base = (data.base ?? {}) as Record<string, unknown>;
  const documentClass = (data.document_class ?? {}) as Record<string, unknown>;
  const files = (data.files ?? {}) as Record<string, unknown>;
  const deps = _normalizeDepends(data.depends_on);

  const fileInfo = _normalizeFileInfo(files.file_info);
  const hasFiles = fileInfo.length > 0;

  const displayData = { ...data };
  delete displayData.document_class;
  delete displayData.depends_on;
  delete displayData.files;
  delete displayData.base;

  const className =
    String((documentClass.class_name as string) ?? doc.className ?? '') || 'document';
  const datestamp = (base.datestamp as string) ?? '';
  const ndiId = (base.id as string) ?? doc.ndiId ?? '';
  const definition = (documentClass.definition as string) ?? '';

  return (
    <div className="space-y-4">
      {/* Header */}
      <div>
        <div className="flex flex-wrap items-center gap-2 mb-1.5">
          <Badge variant="secondary" className="font-mono text-[10px]">
            {className}
          </Badge>
          {hasFiles && (
            <Badge variant="outline" className="font-mono text-[10px]">
              <File className="h-3 w-3 mr-1" />
              Has files
            </Badge>
          )}
        </div>
        {doc.name && (
          <h2 className="text-sm font-medium text-slate-900 dark:text-slate-100 leading-tight">
            {doc.name}
          </h2>
        )}
        <div className="mt-1 space-y-0.5 text-[10px] font-mono text-slate-500 dark:text-slate-400 leading-tight">
          <p>ID: {ndiId || doc.id}</p>
          {definition && <p>{definition}</p>}
          {datestamp && (
            <p className="flex items-center gap-1">
              <Calendar className="h-2.5 w-2.5" />
              {formatDate(datestamp)}
            </p>
          )}
        </div>
      </div>

      {/* Dependencies (top-level list — the visual graph lives in a sibling card) */}
      {deps.length > 0 && (
        <Card>
          <CardHeader className="py-3">
            <CardTitle className="text-xs font-medium flex items-center gap-1">
              <Link2 className="h-3.5 w-3.5" />
              Dependencies ({deps.length})
            </CardTitle>
          </CardHeader>
          <CardBody className="pt-0">
            <div className="space-y-1">
              {deps.map((dep, i) => (
                <div key={i} className="flex items-center gap-2 text-xs">
                  <span className="text-slate-500 dark:text-slate-400 font-mono">
                    {dep.name}:
                  </span>
                  {dep.value ? (
                    datasetId ? (
                      <Link
                        to={`/datasets/${datasetId}/documents/${dep.value}`}
                        className="font-mono text-brand-600 dark:text-brand-400 hover:underline truncate"
                      >
                        {dep.value}
                      </Link>
                    ) : (
                      <span className="font-mono text-brand-600 dark:text-brand-400 truncate">
                        {dep.value}
                      </span>
                    )
                  ) : (
                    <span className="text-slate-500 dark:text-slate-400 italic">empty</span>
                  )}
                </div>
              ))}
            </div>
          </CardBody>
        </Card>
      )}

      {/* Files */}
      {hasFiles && (
        <Card>
          <CardHeader className="py-3">
            <CardTitle className="text-xs font-medium flex items-center gap-1">
              <FileText className="h-3.5 w-3.5" />
              Files ({fileInfo.length})
            </CardTitle>
          </CardHeader>
          <CardBody className="pt-0">
            <div className="space-y-1">
              {fileInfo.map((f, i) => (
                <div
                  key={i}
                  className="flex items-center gap-2 text-xs font-mono text-slate-600 dark:text-slate-400"
                >
                  <File className="h-3 w-3 shrink-0" />
                  <span className="truncate">{f.name}</span>
                  {f.uid && (
                    <span className="text-[10px] text-slate-500 dark:text-slate-400 truncate">{f.uid}</span>
                  )}
                </div>
              ))}
            </div>
          </CardBody>
        </Card>
      )}

      <Separator />

      {/* JSON tree */}
      <Card>
        <CardHeader className="py-3">
          <CardTitle className="text-xs font-medium">Document Properties</CardTitle>
        </CardHeader>
        <CardBody className="pt-0">
          <div className="font-mono text-xs leading-relaxed overflow-auto max-h-[calc(100vh-220px)] min-h-[240px]">
            <JsonTree data={displayData} />
          </div>
        </CardBody>
      </Card>
    </div>
  );
}

function _normalizeDepends(raw: unknown): Array<{ name: string; value: string }> {
  if (!raw) return [];
  const arr = Array.isArray(raw) ? raw : [raw];
  const out: Array<{ name: string; value: string }> = [];
  for (const d of arr) {
    if (!d || typeof d !== 'object') continue;
    const name = String((d as Record<string, unknown>).name ?? 'depends_on');
    const value = (d as Record<string, unknown>).value;
    if (typeof value === 'string') {
      out.push({ name, value });
    } else if (Array.isArray(value) && value.length === 0) {
      out.push({ name, value: '' });
    }
  }
  return out;
}

function _normalizeFileInfo(raw: unknown): Array<{ name: string; uid: string }> {
  if (!raw) return [];
  const arr = Array.isArray(raw) ? raw : [raw];
  const out: Array<{ name: string; uid: string }> = [];
  for (const f of arr) {
    if (!f || typeof f !== 'object') continue;
    const name = String((f as Record<string, unknown>).name ?? '');
    const locations = (f as Record<string, unknown>).locations;
    let uid = '';
    if (locations && typeof locations === 'object' && !Array.isArray(locations)) {
      uid = String((locations as Record<string, unknown>).uid ?? '');
    }
    out.push({ name: name || 'file', uid });
  }
  return out;
}
