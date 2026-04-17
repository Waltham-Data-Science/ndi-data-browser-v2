import { useMutation } from '@tanstack/react-query';
import { apiFetch } from './client';

export interface DistributionGroup {
  name: string;
  count: number;
  min: number;
  max: number;
  mean: number;
  std: number;
  median: number;
  q1: number;
  q3: number;
  values: number[];
}

export interface DistributionGroupedResponse {
  field: string;
  groupBy: string;
  n: number;
  groups: DistributionGroup[];
}

export interface DistributionUngroupedResponse {
  n: number;
  min?: number;
  max?: number;
  mean?: number;
  std?: number;
  quartiles?: { q1: number; median: number; q3: number } | null;
  kde?: { x: number[]; density: number[] } | null;
  raw?: number[];
}

export type DistributionResponse =
  | DistributionGroupedResponse
  | DistributionUngroupedResponse;

export interface DistributionRequest {
  datasetId: string;
  className: string;
  field: string;
  groupBy?: string;
}

export function useDistribution() {
  return useMutation({
    mutationFn: (req: DistributionRequest) =>
      apiFetch<DistributionResponse>('/api/visualize/distribution', {
        method: 'POST',
        body: req,
      }),
  });
}
