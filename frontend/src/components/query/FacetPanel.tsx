import { useFacets } from '@/api/datasets';
import { Badge } from '@/components/ui/Badge';
import { Card, CardBody, CardHeader, CardTitle } from '@/components/ui/Card';
import type { OntologyTerm } from '@/types/facets';

/**
 * Facet selection — one chip per distinct fact aggregated across all
 * published datasets. Clicking a chip pushes a ``contains_string`` filter
 * onto the caller's active filter list.
 *
 * Frozen-contract note: the clicked ``onSelectOntologyFacet`` /
 * ``onSelectProbeType`` callbacks are intentionally raw (not QueryNode
 * shapes) so this component stays UI-agnostic. The QueryPage owner wires
 * the chip click into whatever query-condition model it uses.
 *
 * Data source: ``GET /api/facets``. Freshness budget 5 minutes on the
 * backend cache + 30-second client staleTime (amendment §4.B3).
 */
export interface FacetPanelProps {
  onSelectOntologyFacet: (
    kind: 'species' | 'brainRegions' | 'strains' | 'sexes',
    term: OntologyTerm,
  ) => void;
  onSelectProbeType: (probeType: string) => void;
}

export function FacetPanel({
  onSelectOntologyFacet,
  onSelectProbeType,
}: FacetPanelProps) {
  const { data: facets, isLoading, error } = useFacets();

  return (
    <Card>
      <CardHeader>
        <CardTitle className="text-sm">
          Research vocabulary
          {facets && (
            <span className="ml-2 text-xs font-normal text-fg-muted">
              ({facets.datasetCount} datasets)
            </span>
          )}
        </CardTitle>
      </CardHeader>
      <CardBody className="space-y-4">
        {isLoading && (
          <p className="text-xs text-fg-muted">
            Loading facets…
          </p>
        )}
        {/* Show the error banner whenever there's an error — even when
            cached data remains visible. Hiding errors during background
            refetch hides staleness from the user. */}
        {error && (
          <p className="text-xs text-rose-600">
            {facets
              ? 'Facet counts may be stale — a background refresh failed.'
              : 'Couldn\u2019t load research facets.'}
          </p>
        )}
        {facets && (
          <>
            <FacetList
              title="Species"
              terms={facets.species}
              onClick={(t) => onSelectOntologyFacet('species', t)}
            />
            <FacetList
              title="Brain regions"
              terms={facets.brainRegions}
              onClick={(t) => onSelectOntologyFacet('brainRegions', t)}
            />
            <FacetList
              title="Strains"
              terms={facets.strains}
              onClick={(t) => onSelectOntologyFacet('strains', t)}
            />
            <FacetList
              title="Sex"
              terms={facets.sexes}
              onClick={(t) => onSelectOntologyFacet('sexes', t)}
            />
            <ProbeTypeList
              probeTypes={facets.probeTypes}
              onClick={onSelectProbeType}
            />
          </>
        )}
      </CardBody>
    </Card>
  );
}

function FacetList({
  title,
  terms,
  onClick,
}: {
  title: string;
  terms: OntologyTerm[];
  onClick: (term: OntologyTerm) => void;
}) {
  if (terms.length === 0) return null;
  return (
    <div>
      <h3 className="text-xs font-medium text-fg-secondary mb-1.5">
        {title}
      </h3>
      <div className="flex flex-wrap gap-1.5">
        {terms.map((term) => {
          const key = term.ontologyId ?? `label::${term.label}`;
          return (
            <button
              key={key}
              type="button"
              onClick={() => onClick(term)}
              className="hover:opacity-80 transition-opacity"
              title={term.ontologyId ?? term.label}
              aria-label={`Filter by ${title.toLowerCase()}: ${term.label}`}
            >
              <Badge variant="secondary" className="cursor-pointer">
                {term.label}
              </Badge>
            </button>
          );
        })}
      </div>
    </div>
  );
}

function ProbeTypeList({
  probeTypes,
  onClick,
}: {
  probeTypes: string[];
  onClick: (probeType: string) => void;
}) {
  if (probeTypes.length === 0) return null;
  return (
    <div>
      <h3 className="text-xs font-medium text-fg-secondary mb-1.5">
        Probe types
      </h3>
      <div className="flex flex-wrap gap-1.5">
        {probeTypes.map((probeType) => (
          <button
            key={probeType}
            type="button"
            onClick={() => onClick(probeType)}
            className="hover:opacity-80 transition-opacity"
            aria-label={`Filter by probe type: ${probeType}`}
          >
            <Badge variant="outline" className="cursor-pointer font-mono">
              {probeType}
            </Badge>
          </button>
        ))}
      </div>
    </div>
  );
}
