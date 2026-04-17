import { useState } from 'react';
import { Link } from 'react-router-dom';

import type { QueryResponse } from '@/api/query';
import { QueryBuilder } from '@/components/query/QueryBuilder';
import { Card, CardBody, CardHeader, CardTitle } from '@/components/ui/Card';
import { formatNumber } from '@/lib/format';

/**
 * Cross-cloud query page — reuses the ported v1 QueryBuilder with URL
 * pre-load (ontology cross-link drops users here with op+field+param1
 * hydrated from the OntologyPopover "Find everywhere" link).
 */
export function QueryPage() {
  const [results, setResults] = useState<QueryResponse | null>(null);

  return (
    <div className="space-y-4">
      <header>
        <h1 className="text-2xl font-bold text-slate-900 dark:text-slate-100">
          Query builder
        </h1>
        <p className="text-sm text-slate-600 dark:text-slate-400">
          Build an NDI query. Every field search auto-narrows to the class, so
          searches stay fast even across public datasets.
        </p>
      </header>

      <QueryBuilder onResults={setResults} onClear={() => setResults(null)} />

      {results && <ResultsCard results={results} />}
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
