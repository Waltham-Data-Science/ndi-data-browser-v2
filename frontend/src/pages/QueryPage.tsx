import { useState } from 'react';
import { Link } from 'react-router-dom';

import type { QueryResponse } from '@/api/query';
import type { QueryNode } from '@/api/query';
import { FacetPanel } from '@/components/query/FacetPanel';
import { OutputShapePreview } from '@/components/query/OutputShapePreview';
import { QueryBuilder } from '@/components/query/QueryBuilder';
import { Card, CardBody, CardHeader, CardTitle } from '@/components/ui/Card';
import { formatNumber } from '@/lib/format';
import type { OntologyTerm } from '@/types/facets';

/**
 * Cross-cloud query page.
 *
 * Plan B B3 surface:
 * - FacetPanel (left sidebar) — cross-dataset distinct-values chips from
 *   ``GET /api/facets``. Clicking a chip appends a filter to the builder.
 * - QueryBuilder (center) — ported v1 builder with MATLAB ``contains``
 *   default string match (amendment §4.B3). Also hydrates from URL for
 *   ontology "Find everywhere" cross-links.
 * - OutputShapePreview (right sidebar) — static NDI-matlab tutorial
 *   column sets for the subject/probe/epoch grains.
 */
export function QueryPage() {
  const [results, setResults] = useState<QueryResponse | null>(null);
  // Facet clicks inject a fresh seed + re-key the builder so the useEffect
  // re-runs. ``seedKey`` increments monotonically per click; ``seed`` holds
  // the initial condition list the builder should start with.
  const [seed, setSeed] = useState<{ key: number; conditions: QueryNode[] } | null>(null);

  const handleSelectOntologyFacet = (
    _kind: 'species' | 'brainRegions' | 'strains' | 'sexes',
    term: OntologyTerm,
  ) => {
    // `data.ontology_name` is the canonical ontology-ID field emitted by
    // the enrichment pipeline (see B1's DatasetSummary notes + v1's
    // ontology cross-link). A click appends `contains_string` on
    // `data.ontology_name` — this matches both full IDs
    // (`NCBITaxon:10116`) and human labels in the same cell.
    //
    // `_kind` is reserved for future use: when the enrichment pipeline
    // routes species/brainRegions to distinct field paths we'll
    // dispatch on it. For now we unify on `data.ontology_name`.
    const param1 = term.ontologyId ?? term.label;
    const condition: QueryNode = {
      operation: 'contains_string',
      field: 'data.ontology_name',
      param1,
    };
    setSeed((prev) => ({
      key: (prev?.key ?? 0) + 1,
      conditions: [condition],
    }));
  };

  const handleSelectProbeType = (probeType: string) => {
    // Probe types are free-text; narrow by element.type (the canonical
    // probe-type field in NDI-matlab + v2's SUBJECT/PROBE column shape).
    const condition: QueryNode = {
      operation: 'contains_string',
      field: 'element.type',
      param1: probeType,
    };
    setSeed((prev) => ({
      key: (prev?.key ?? 0) + 1,
      conditions: [condition],
    }));
  };

  return (
    <div className="space-y-4">
      <header>
        <h1 className="text-2xl font-bold text-slate-900 dark:text-slate-100">
          Query builder
        </h1>
        <p className="text-sm text-slate-600 dark:text-slate-400">
          Build an NDI query. Every field search auto-narrows to the class, so
          searches stay fast even across public datasets. Filters default to{' '}
          <code className="font-mono text-xs">contains</code> (case-insensitive) —
          matches the NDI-matlab tutorial convention.
        </p>
      </header>

      <div className="grid gap-4 lg:grid-cols-[18rem_minmax(0,1fr)_20rem]">
        <aside className="space-y-4">
          <FacetPanel
            onSelectOntologyFacet={handleSelectOntologyFacet}
            onSelectProbeType={handleSelectProbeType}
          />
        </aside>

        <section className="space-y-4">
          {/*
            Use `key` to force a re-mount on each facet click so the builder's
            initialization useEffect picks up the fresh seed. Without this a
            second click on a different chip would not reach the state.
          */}
          <QueryBuilder
            key={seed?.key ?? 'initial'}
            onResults={setResults}
            onClear={() => setResults(null)}
            seedConditions={seed?.conditions}
          />
          {results && <ResultsCard results={results} />}
        </section>

        <aside className="space-y-4">
          <OutputShapePreview />
        </aside>
      </div>
    </div>
  );
}

function ResultsCard({ results }: { results: QueryResponse }) {
  const docs = results.documents ?? [];
  const total = results.total ?? results.totalItems ?? results.number_matches ?? docs.length;
  return (
    <Card>
      <CardHeader>
        <CardTitle className="text-sm">
          Results — {formatNumber(total)} documents
        </CardTitle>
      </CardHeader>
      <CardBody>
        {docs.length === 0 ? (
          <p className="text-sm text-slate-500 dark:text-slate-400">
            No matching documents.
          </p>
        ) : (
          <div className="overflow-x-auto rounded border border-slate-200 dark:border-slate-700">
            <table className="w-full text-sm">
              <thead className="bg-slate-50 dark:bg-slate-900">
                <tr>
                  <th className="px-3 py-2 text-left font-medium text-slate-500 dark:text-slate-300">
                    Name
                  </th>
                  <th className="px-3 py-2 text-left font-medium text-slate-500 dark:text-slate-300">
                    Class
                  </th>
                  <th className="px-3 py-2 text-left font-medium text-slate-500 dark:text-slate-300">
                    Dataset
                  </th>
                  <th className="px-3 py-2 text-left font-medium text-slate-500 dark:text-slate-300">
                    ndiId
                  </th>
                </tr>
              </thead>
              <tbody>
                {docs.slice(0, 200).map((d, i) => {
                  const id = String(d.id ?? d.ndiId ?? i);
                  const dsId = String(d.datasetId ?? '');
                  return (
                    <tr
                      key={id}
                      className="border-t border-slate-100 dark:border-slate-800 hover:bg-slate-50 dark:hover:bg-slate-800/40"
                    >
                      <td className="px-3 py-1.5">
                        {dsId && d.id ? (
                          <Link
                            to={`/datasets/${dsId}/documents/${d.id}`}
                            className="text-brand-600 dark:text-brand-400 hover:underline"
                          >
                            {String(d.name ?? d.id)}
                          </Link>
                        ) : (
                          <span>{String(d.name ?? d.id ?? '')}</span>
                        )}
                      </td>
                      <td className="px-3 py-1.5 font-mono text-xs">
                        {String(d.className ?? '—')}
                      </td>
                      <td className="px-3 py-1.5 font-mono text-xs text-slate-500 dark:text-slate-400">
                        {dsId ? (
                          <Link to={`/datasets/${dsId}`} className="hover:underline">
                            {dsId.slice(0, 8)}…
                          </Link>
                        ) : (
                          '—'
                        )}
                      </td>
                      <td className="px-3 py-1.5 font-mono text-xs text-slate-500 dark:text-slate-400 truncate max-w-[220px]">
                        {String(d.ndiId ?? '')}
                      </td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
            {docs.length > 200 && (
              <p className="px-3 py-2 text-xs text-slate-500 dark:text-slate-400">
                Showing first 200 of {formatNumber(docs.length)} returned documents.
              </p>
            )}
          </div>
        )}
      </CardBody>
    </Card>
  );
}
