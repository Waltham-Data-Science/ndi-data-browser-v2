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
 * Cross-cloud query page — `/query`.
 *
 * Layout:
 *   1. Depth-gradient hero band (eyebrow + H1 + educational sub-copy).
 *      Full-bleed, matches the design bar shared by DatasetsPage,
 *      MyDatasetsPage, and DatasetDetailPage. Pattern overlay uses the
 *      NDI brandmark at 5% opacity.
 *   2. Body grid (max-w 1200px): FacetPanel (left sidebar) + QueryBuilder
 *      (center) + OutputShapePreview (right sidebar), with the ResultsCard
 *      rendered directly below the builder when results are present.
 *
 * Plan B B3 surface (preserved end-to-end from the prior revision):
 *   - FacetPanel — cross-dataset distinct-values chips from
 *     ``GET /api/facets``. Clicking a chip appends a filter to the builder.
 *   - QueryBuilder — ported v1 builder with MATLAB ``contains`` default
 *     string match (amendment §4.B3); also hydrates from URL for ontology
 *     "Find everywhere" cross-links.
 *   - OutputShapePreview — static NDI-matlab tutorial column sets for the
 *     subject/probe/epoch grains.
 *
 * All functionality preserved: `useFacets`/`useRunQuery`/`useQueryOperations`
 * hooks, feature-flag gate + 503 probe (upstream of this component),
 * `seedKey` force-remount pattern so successive facet clicks always reach
 * the builder's initialization useEffect. This file is visual-layer-only.
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
    <>
      {/* ── Hero band (full bleed) ──────────────────────────────────── */}
      <section
        className="relative overflow-hidden text-white"
        style={{ background: 'var(--grad-depth)' }}
        aria-labelledby="query-hero"
      >
        {/* Pattern overlay — NDI brandmark at 5% opacity */}
        <div
          aria-hidden
          className="absolute inset-0 pointer-events-none"
          style={{
            backgroundImage: "url('/brand/ndicloud-emblem.svg')",
            backgroundSize: '120px',
            backgroundRepeat: 'repeat',
            opacity: 0.05,
          }}
        />

        <div className="relative mx-auto max-w-[1200px] px-7 py-12 md:py-14">
          <div className="eyebrow mb-4">
            <span className="eyebrow-dot" aria-hidden />
            NDI QUERY · BETA
          </div>
          <h1
            id="query-hero"
            className="text-white font-display font-extrabold tracking-tight leading-tight text-[2rem] md:text-[2.25rem] mb-2"
          >
            Query across every dataset.
          </h1>
          <p className="text-white/70 text-[14.5px] leading-relaxed max-w-[620px]">
            Filter by species, brain region, probe, subject, session, epoch.
            Every field search auto-narrows to the class, so queries stay fast
            even across public datasets. Filters default to{' '}
            <code className="font-mono text-[13px] text-white/85">contains</code>{' '}
            (case-insensitive) — matches the NDI-matlab tutorial convention.
          </p>
        </div>
      </section>

      {/* ── Body ─────────────────────────────────────────────────────── */}
      <section className="mx-auto max-w-[1200px] px-7 py-7">
        <div className="grid gap-4 lg:grid-cols-[18rem_minmax(0,1fr)_20rem]">
          <aside className="space-y-4 min-w-0">
            <FacetPanel
              onSelectOntologyFacet={handleSelectOntologyFacet}
              onSelectProbeType={handleSelectProbeType}
            />
          </aside>

          <section className="space-y-4 min-w-0">
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

          <aside className="space-y-4 min-w-0">
            <OutputShapePreview />
          </aside>
        </div>
      </section>
    </>
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
          <p className="text-sm text-fg-muted">
            No matching documents.
          </p>
        ) : (
          <div className="overflow-x-auto rounded border border-border-subtle">
            <table className="w-full text-sm">
              <thead className="bg-gray-50">
                <tr>
                  <th className="px-3 py-2 text-left font-medium text-fg-muted">
                    Name
                  </th>
                  <th className="px-3 py-2 text-left font-medium text-fg-muted">
                    Class
                  </th>
                  <th className="px-3 py-2 text-left font-medium text-fg-muted">
                    Dataset
                  </th>
                  <th className="px-3 py-2 text-left font-medium text-fg-muted">
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
                      className="border-t border-border-subtle hover:bg-gray-50"
                    >
                      <td className="px-3 py-1.5">
                        {dsId && d.id ? (
                          <Link
                            to={`/datasets/${dsId}/documents/${d.id}`}
                            className="text-brand-600 hover:underline"
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
                      <td className="px-3 py-1.5 font-mono text-xs text-fg-muted">
                        {dsId ? (
                          <Link to={`/datasets/${dsId}`} className="hover:underline">
                            {dsId.slice(0, 8)}…
                          </Link>
                        ) : (
                          '—'
                        )}
                      </td>
                      <td className="px-3 py-1.5 font-mono text-xs text-fg-muted truncate max-w-[220px] md:max-w-[340px] lg:max-w-[480px]">
                        {String(d.ndiId ?? '')}
                      </td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
            {docs.length > 200 && (
              <p className="px-3 py-2 text-xs text-fg-muted">
                Showing first 200 of {formatNumber(docs.length)} returned documents.
              </p>
            )}
          </div>
        )}
      </CardBody>
    </Card>
  );
}
