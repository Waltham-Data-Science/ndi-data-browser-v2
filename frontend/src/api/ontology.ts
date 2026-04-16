import { useQuery } from '@tanstack/react-query';
import { apiFetch } from './client';

export interface OntologyTerm {
  provider: string;
  termId: string;
  label: string | null;
  definition: string | null;
  url: string | null;
}

export function useOntologyLookup(term: string | undefined) {
  return useQuery({
    queryKey: ['ontology', term],
    queryFn: () => apiFetch<OntologyTerm>(`/api/ontology/lookup?term=${encodeURIComponent(term!)}`),
    enabled: !!term,
    staleTime: 30 * 60 * 1000,
    retry: 1,
  });
}
