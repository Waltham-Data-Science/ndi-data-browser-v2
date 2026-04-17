import { useCallback, useEffect, useState } from 'react';
import { ChevronDown, ChevronUp, Loader2, Play, Plus, Search, Trash2, X } from 'lucide-react';
import { useSearchParams } from 'react-router-dom';

import { useQueryOperations, useRunQuery, type QueryNode, type QueryResponse } from '@/api/query';
import { Badge } from '@/components/ui/Badge';
import { Button } from '@/components/ui/Button';
import { Card, CardBody } from '@/components/ui/Card';
import { ErrorState } from '@/components/errors/ErrorState';
import { Input } from '@/components/ui/Input';

const QUICK_TYPES = ['subject', 'probe', 'element', 'element_epoch', 'imageStack', 'treatment'];

const FALLBACK_OPERATIONS = [
  { name: 'isa', label: 'is a (type)', negatable: true },
  { name: 'depends_on', label: 'depends on', negatable: true },
  { name: 'hasfield', label: 'field exists', negatable: true },
  { name: 'exact_string', label: 'equals (string)', negatable: true },
  { name: 'exact_string_anycase', label: 'equals (case-insensitive)', negatable: true },
  { name: 'contains_string', label: 'contains', negatable: true },
  { name: 'regexp', label: 'matches regex', negatable: true },
  { name: 'exact_number', label: '= (number)', negatable: true },
  { name: 'lessthan', label: '< (number)', negatable: true },
  { name: 'lessthaneq', label: '<= (number)', negatable: true },
  { name: 'greaterthan', label: '> (number)', negatable: true },
  { name: 'greaterthaneq', label: '>= (number)', negatable: true },
  { name: 'hasmember', label: 'has member', negatable: true },
];

export type Scope = 'public' | 'private' | 'all' | string; // string: CSV of dataset IDs

interface QueryBuilderProps {
  onResults: (result: QueryResponse) => void;
  onClear?: () => void;
  /** Optional — narrows scope to a single dataset when the page provides it. */
  defaultDatasetId?: string;
}

function newCondition(): QueryNode {
  return { operation: 'isa', field: '', param1: '', param2: '' };
}

function buildStructure(conds: QueryNode[]): QueryNode[] {
  return conds
    .filter((c) => c.operation && (c.operation === 'hasfield' || c.param1))
    .map((c) => ({
      operation: c.operation,
      field: c.field || undefined,
      param1: c.param1 ?? undefined,
      param2: c.param2 ?? undefined,
    }));
}

/**
 * QueryBuilder — ported from v1 with v2 adaptations:
 *
 * - `?op=…&field=…&param1=…&param2=…` URL pre-load for the ontology cross-
 *   link (OntologyPopover's "Find everywhere" drops you here with a pre-
 *   filled `contains_string` on `data.ontology_name`).
 * - Error surface goes through v2's ErrorState (which knows about
 *   QUERY_TOO_LARGE and renders the narrowing hint).
 * - Scope dropdown: public / private / all / "this dataset" when the
 *   enclosing page supplies `defaultDatasetId`.
 */
export function QueryBuilder({ onResults, onClear, defaultDatasetId }: QueryBuilderProps) {
  const [searchParams, setSearchParams] = useSearchParams();
  const [searchTerm, setSearchTerm] = useState('');
  const [scope, setScope] = useState<Scope>(defaultDatasetId ? defaultDatasetId : 'public');
  const [showAdvanced, setShowAdvanced] = useState(false);
  const [conditions, setConditions] = useState<QueryNode[]>([newCondition()]);

  const executeQuery = useRunQuery();
  const { data: opsData } = useQueryOperations();
  const operations = opsData?.operations
    ? opsData.operations.map((op) => ({ name: op.name, label: op.label, negatable: op.negatable }))
    : FALLBACK_OPERATIONS;

  // Hydrate from URL on first load (ontology cross-link / deep-links).
  useEffect(() => {
    const op = searchParams.get('op');
    const field = searchParams.get('field') ?? '';
    const param1 = searchParams.get('param1') ?? '';
    const param2 = searchParams.get('param2') ?? '';
    const urlScope = searchParams.get('scope');
    if (op) {
      setShowAdvanced(true);
      setConditions([{ operation: op, field, param1, param2 }]);
    }
    if (urlScope) setScope(urlScope);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  const persistToUrl = (cond: QueryNode | null, nextScope: Scope) => {
    const next = new URLSearchParams(searchParams);
    if (cond) {
      next.set('op', cond.operation);
      if (cond.field) next.set('field', String(cond.field));
      else next.delete('field');
      if (cond.param1 !== '' && cond.param1 != null) next.set('param1', String(cond.param1));
      else next.delete('param1');
      if (cond.param2 !== '' && cond.param2 != null) next.set('param2', String(cond.param2));
      else next.delete('param2');
    } else {
      ['op', 'field', 'param1', 'param2'].forEach((k) => next.delete(k));
    }
    if (nextScope && nextScope !== 'public') next.set('scope', nextScope);
    else next.delete('scope');
    setSearchParams(next, { replace: true });
  };

  const runSimple = useCallback(
    (term: string) => {
      if (!term.trim()) return;
      setSearchTerm(term);
      const cond: QueryNode = { operation: 'isa', param1: term.trim() };
      executeQuery.mutate(
        { searchstructure: [cond], scope },
        { onSuccess: (r) => onResults(r) },
      );
      persistToUrl(cond, scope);
    },
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [scope, executeQuery, onResults],
  );

  const runAdvanced = () => {
    const structure = buildStructure(conditions);
    if (structure.length === 0) return;
    executeQuery.mutate(
      { searchstructure: structure, scope },
      { onSuccess: (r) => onResults(r) },
    );
    persistToUrl(conditions[0] ?? null, scope);
  };

  const updateCondition = (i: number, patch: Partial<QueryNode>) =>
    setConditions((prev) => prev.map((c, idx) => (idx === i ? { ...c, ...patch } : c)));
  const addCondition = () => setConditions((prev) => [...prev, newCondition()]);
  const removeCondition = (i: number) =>
    setConditions((prev) => prev.filter((_, idx) => idx !== i));

  const handleClear = () => {
    setSearchTerm('');
    setConditions([newCondition()]);
    persistToUrl(null, 'public');
    onClear?.();
  };

  const needsField = (op: string) => op !== 'isa' && op !== '~isa';
  const needsParam1 = (op: string) => op !== 'hasfield' && op !== '~hasfield';
  const needsParam2 = (op: string) => op === 'depends_on' || op === '~depends_on';

  const err = executeQuery.error;

  return (
    <div className="space-y-3">
      {!showAdvanced ? (
        <Card>
          <CardBody className="pt-5 pb-4 space-y-3">
            <form
              onSubmit={(e) => {
                e.preventDefault();
                runSimple(searchTerm);
              }}
              className="flex items-center gap-2"
            >
              <div className="relative flex-1">
                <Search className="absolute left-3 top-1/2 -translate-y-1/2 h-4 w-4 text-slate-400" />
                <Input
                  placeholder="Search by class (e.g. subject, element, treatment)"
                  value={searchTerm}
                  onChange={(e) => setSearchTerm(e.target.value)}
                  className="pl-9 pr-8 h-10"
                />
                {searchTerm && (
                  <button
                    type="button"
                    onClick={handleClear}
                    className="absolute right-2.5 top-1/2 -translate-y-1/2 text-slate-500 hover:text-slate-700"
                    aria-label="Clear search"
                  >
                    <X className="h-3.5 w-3.5" />
                  </button>
                )}
              </div>
              <ScopeSelect
                scope={scope}
                onChange={(next) => {
                  setScope(next);
                  persistToUrl(null, next);
                }}
                defaultDatasetId={defaultDatasetId}
                className="h-10"
              />
              <Button
                type="submit"
                className="h-10 px-4"
                disabled={executeQuery.isPending || !searchTerm.trim()}
              >
                {executeQuery.isPending ? (
                  <Loader2 className="h-4 w-4 animate-spin" />
                ) : (
                  'Search'
                )}
              </Button>
            </form>

            <div className="flex flex-wrap gap-1.5">
              {QUICK_TYPES.map((type) => (
                <button
                  key={type}
                  type="button"
                  onClick={() => runSimple(type)}
                  className="px-2.5 py-1 text-xs rounded-full border border-slate-200 dark:border-slate-700 hover:bg-slate-100 dark:hover:bg-slate-800 transition-colors font-mono"
                >
                  {type}
                </button>
              ))}
            </div>
          </CardBody>
        </Card>
      ) : (
        <Card>
          <CardBody className="pt-5 pb-4 space-y-2">
            <div className="flex items-center justify-between mb-1">
              <span className="text-sm font-medium">Advanced Filters</span>
              <ScopeSelect
                scope={scope}
                onChange={(next) => {
                  setScope(next);
                  persistToUrl(null, next);
                }}
                defaultDatasetId={defaultDatasetId}
                className="h-7"
              />
            </div>
            {conditions.map((cond, i) => (
              <div key={i} className="flex items-center gap-2 flex-wrap">
                {i > 0 && (
                  <Badge variant="secondary" className="shrink-0">
                    AND
                  </Badge>
                )}
                {needsField(cond.operation) && (
                  <Input
                    placeholder="field (e.g. element.name)"
                    value={String(cond.field ?? '')}
                    onChange={(e) => updateCondition(i, { field: e.target.value })}
                    className="h-7 text-xs font-mono flex-1 min-w-[160px]"
                  />
                )}
                <select
                  value={cond.operation}
                  onChange={(e) => updateCondition(i, { operation: e.target.value })}
                  className="h-7 text-xs rounded border border-slate-300 dark:border-slate-700 bg-white dark:bg-slate-800 px-2 shrink-0"
                >
                  <optgroup label="Positive">
                    {operations.map((op) => (
                      <option key={op.name} value={op.name}>
                        {op.label}
                      </option>
                    ))}
                  </optgroup>
                  <optgroup label="Negated">
                    {operations
                      .filter((op) => op.negatable !== false && op.name !== 'or')
                      .map((op) => (
                        <option key={`~${op.name}`} value={`~${op.name}`}>
                          NOT {op.label}
                        </option>
                      ))}
                  </optgroup>
                </select>
                {needsParam1(cond.operation) && (
                  <Input
                    placeholder={
                      cond.operation.endsWith('isa')
                        ? 'class name (e.g. subject)'
                        : cond.operation.endsWith('depends_on')
                          ? 'edge name or *'
                          : 'value'
                    }
                    value={String(cond.param1 ?? '')}
                    onChange={(e) => updateCondition(i, { param1: e.target.value })}
                    className="h-7 text-xs font-mono flex-1 min-w-[140px]"
                  />
                )}
                {needsParam2(cond.operation) && (
                  <Input
                    placeholder="dep value (ndiId)"
                    value={String(cond.param2 ?? '')}
                    onChange={(e) => updateCondition(i, { param2: e.target.value })}
                    className="h-7 text-xs font-mono flex-1 min-w-[140px]"
                  />
                )}
                {conditions.length > 1 && (
                  <Button
                    variant="ghost"
                    size="sm"
                    className="h-7 w-7 p-0 shrink-0"
                    onClick={() => removeCondition(i)}
                    aria-label={`Remove filter ${i + 1}`}
                  >
                    <Trash2 className="h-3 w-3" />
                  </Button>
                )}
              </div>
            ))}
            <div className="flex items-center gap-2 pt-2">
              <Button variant="secondary" size="sm" className="h-7 text-xs" onClick={addCondition}>
                <Plus className="h-3 w-3 mr-1" />
                Add filter
              </Button>
              <Button
                size="sm"
                className="h-7 text-xs"
                onClick={runAdvanced}
                disabled={executeQuery.isPending}
              >
                {executeQuery.isPending ? (
                  <Loader2 className="h-3 w-3 mr-1 animate-spin" />
                ) : (
                  <Play className="h-3 w-3 mr-1" />
                )}
                Run query
              </Button>
            </div>
          </CardBody>
        </Card>
      )}

      {err && <ErrorState error={err} onRetry={() => executeQuery.reset()} />}

      <button
        type="button"
        onClick={() => setShowAdvanced(!showAdvanced)}
        className="flex items-center gap-1 text-xs text-slate-500 hover:text-slate-800 dark:hover:text-slate-200 transition-colors"
      >
        {showAdvanced ? (
          <>
            <ChevronUp className="h-3 w-3" />
            Simple search
          </>
        ) : (
          <>
            <ChevronDown className="h-3 w-3" />
            Advanced filters
          </>
        )}
      </button>
    </div>
  );
}

function ScopeSelect({
  scope,
  onChange,
  defaultDatasetId,
  className,
}: {
  scope: Scope;
  onChange: (next: Scope) => void;
  defaultDatasetId?: string;
  className?: string;
}) {
  return (
    <select
      value={scope}
      onChange={(e) => onChange(e.target.value as Scope)}
      className={`text-xs rounded border border-slate-300 dark:border-slate-700 bg-white dark:bg-slate-800 px-2 ${className ?? ''}`}
      aria-label="Query scope"
    >
      <option value="public">Public datasets</option>
      <option value="private">My datasets</option>
      <option value="all">All</option>
      {defaultDatasetId && <option value={defaultDatasetId}>This dataset</option>}
    </select>
  );
}
