import { useState } from 'react';
import { useRunQuery, type QueryNode } from '@/api/query';
import { useMe } from '@/api/auth';
import { Card, CardBody, CardHeader } from '@/components/ui/Card';
import { Button } from '@/components/ui/Button';
import { Input } from '@/components/ui/Input';
import { ErrorState } from '@/components/errors/ErrorState';

type Op = 'isa' | 'exact_string' | 'contains_string' | 'hasfield' | 'hasmember' | 'depends_on' | 'regexp';
const OPS: Op[] = ['isa', 'exact_string', 'contains_string', 'hasfield', 'hasmember', 'depends_on', 'regexp'];

interface Clause {
  id: number;
  negated: boolean;
  operation: Op;
  field: string;
  param1: string;
  param2: string;
}

type ScopeKind = 'public' | 'my' | 'all' | 'dataset';

export function QueryPage() {
  const me = useMe();
  const run = useRunQuery();
  const [scopeKind, setScopeKind] = useState<ScopeKind>('public');
  const [datasetId, setDatasetId] = useState('');
  const [clauses, setClauses] = useState<Clause[]>([
    { id: 1, negated: false, operation: 'isa', field: '', param1: 'subject', param2: '' },
  ]);

  const canUseMy = me.isSuccess;
  const scope = (() => {
    if (scopeKind === 'my') return 'private';
    if (scopeKind === 'all') return 'all';
    if (scopeKind === 'public') return 'public';
    return datasetId.trim();
  })();

  function addClause() {
    setClauses((c) => [
      ...c,
      { id: Date.now(), negated: false, operation: 'contains_string', field: '', param1: '', param2: '' },
    ]);
  }
  function removeClause(id: number) {
    setClauses((c) => c.filter((x) => x.id !== id));
  }
  function update(id: number, patch: Partial<Clause>) {
    setClauses((c) => c.map((x) => (x.id === id ? { ...x, ...patch } : x)));
  }

  function submit() {
    const searchstructure: QueryNode[] = clauses.map((c) => {
      const operation = (c.negated ? `~${c.operation}` : c.operation) as string;
      const node: QueryNode = { operation };
      if (c.field) node.field = c.field;
      if (c.param1) node.param1 = c.param1;
      if (c.param2) node.param2 = c.param2;
      return node;
    });
    run.mutate({ searchstructure, scope });
  }

  return (
    <div className="space-y-4">
      <header>
        <h1 className="text-2xl font-bold">Query builder</h1>
        <p className="text-sm text-slate-600 dark:text-slate-400">
          Build an NDI query. Every field search auto-narrows to the class, so searches are fast even cross-cloud.
        </p>
      </header>

      <Card>
        <CardHeader>
          <h2 className="text-sm font-semibold">Scope</h2>
        </CardHeader>
        <CardBody className="flex flex-wrap gap-3 text-sm">
          <label className="flex items-center gap-1.5">
            <input type="radio" checked={scopeKind === 'public'} onChange={() => setScopeKind('public')} /> All public
          </label>
          <label className={`flex items-center gap-1.5 ${canUseMy ? '' : 'text-slate-400'}`}>
            <input
              type="radio"
              checked={scopeKind === 'my'}
              onChange={() => setScopeKind('my')}
              disabled={!canUseMy}
            />{' '}
            My org
          </label>
          <label className={`flex items-center gap-1.5 ${canUseMy ? '' : 'text-slate-400'}`}>
            <input
              type="radio"
              checked={scopeKind === 'all'}
              onChange={() => setScopeKind('all')}
              disabled={!canUseMy}
            />{' '}
            Everywhere I can access
          </label>
          <label className="flex items-center gap-1.5">
            <input type="radio" checked={scopeKind === 'dataset'} onChange={() => setScopeKind('dataset')} />
            Specific dataset
            {scopeKind === 'dataset' && (
              <Input
                className="ml-2 w-64"
                value={datasetId}
                placeholder="24-char dataset ID"
                onChange={(e) => setDatasetId(e.target.value)}
              />
            )}
          </label>
        </CardBody>
      </Card>

      <Card>
        <CardHeader>
          <h2 className="text-sm font-semibold">Clauses</h2>
        </CardHeader>
        <CardBody className="space-y-3">
          {clauses.map((c) => (
            <div key={c.id} className="grid grid-cols-[auto_auto_1fr_1fr_1fr_auto] items-center gap-2">
              <label className="text-xs">
                <input
                  type="checkbox"
                  checked={c.negated}
                  onChange={(e) => update(c.id, { negated: e.target.checked })}
                  disabled={c.operation === ('or' as Op)}
                />{' '}
                not
              </label>
              <select
                value={c.operation}
                onChange={(e) => update(c.id, { operation: e.target.value as Op })}
                className="rounded border border-slate-300 px-2 py-1 text-sm dark:border-slate-700 dark:bg-slate-800"
              >
                {OPS.map((o) => <option key={o} value={o}>{o}</option>)}
              </select>
              <Input
                placeholder="field (e.g. subject.species)"
                value={c.field}
                onChange={(e) => update(c.id, { field: e.target.value })}
              />
              <Input
                placeholder="param1"
                value={c.param1}
                onChange={(e) => update(c.id, { param1: e.target.value })}
              />
              <Input
                placeholder="param2"
                value={c.param2}
                onChange={(e) => update(c.id, { param2: e.target.value })}
              />
              <Button size="sm" variant="ghost" onClick={() => removeClause(c.id)}>×</Button>
            </div>
          ))}
          <Button size="sm" variant="secondary" onClick={addClause}>+ Add clause</Button>
        </CardBody>
      </Card>

      <div className="flex gap-2">
        <Button onClick={submit} disabled={run.isPending || (scopeKind === 'dataset' && !datasetId)}>
          {run.isPending ? 'Running…' : 'Run query'}
        </Button>
      </div>

      {run.isError && <ErrorState error={run.error} onRetry={submit} />}

      {run.data && (
        <Card>
          <CardHeader>
            <h2 className="text-sm font-semibold">
              Results — {(run.data.documents?.length ?? run.data.ids?.length ?? 0)} documents
            </h2>
          </CardHeader>
          <CardBody>
            <pre className="max-h-[50vh] overflow-auto rounded bg-slate-50 p-3 text-xs dark:bg-slate-900">
              {JSON.stringify(run.data, null, 2)}
            </pre>
          </CardBody>
        </Card>
      )}
    </div>
  );
}
