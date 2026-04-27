/**
 * FacetsResponse — distinct structured facts aggregated across every
 * published dataset.
 *
 * One-to-one mirror of
 * ``backend/services/facet_service.py::FacetsResponse``. Produced by the
 * Plan B B3 cross-dataset facet aggregator and returned verbatim from
 * ``GET /api/facets``.
 *
 * Freshness budget (amendment §4.B3): cached under ``facets:v1`` for 5
 * minutes. A dataset published at T=0 surfaces in these facets within
 * T+5m, not T+1h. Short TTL is the CURRENT strategy; the eventual
 * strategy is "invalidate on dataset publish" — see ADR-013.
 */

import type { OntologyTerm } from './dataset-summary';

export type { OntologyTerm };

export interface FacetsResponse {
  /** Distinct species terms across all published datasets. Empty list means
   *  the aggregation ran and found no species values. */
  species: OntologyTerm[];
  /** Distinct brain-region terms. */
  brainRegions: OntologyTerm[];
  /** Distinct strain terms. */
  strains: OntologyTerm[];
  /** Distinct biological-sex terms. */
  sexes: OntologyTerm[];
  /** Distinct probe-type labels — free-text bucket (amendment §3), no
   *  canonical ontology. */
  probeTypes: string[];
  /** Distinct license labels post-normalization. The backend collapses
   *  every raw on-the-wire format (``CC-BY-4.0``, ``ccBy4_0``,
   *  ``Creative Commons Attribution 4.0 International``) into one
   *  canonical short label per logical license. */
  licenses: string[];
  /** How many datasets contributed at least one non-null fact to this
   *  aggregation. Distinct from ``totalNumber`` on the published catalog:
   *  datasets with a failed synthesizer / zero subjects don't contribute. */
  datasetCount: number;
  /** ISO-8601 timestamp the aggregation computed at. */
  computedAt: string;
  schemaVersion: 'facets:v1';
}

/** Runtime marker so ``import type`` cannot erase the schema version at
 * build time. */
export const FacetsContract = { schemaVersion: 'facets:v1' } as const;
