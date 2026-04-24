/**
 * DatasetProvenanceCard — Plan B B5 sidebar card.
 *
 * Three-section layout:
 *   - Branched from: parent dataset link (if any)
 *   - Branches: child dataset chips (if any)
 *   - Dependencies: summary "N docs reference M other datasets" +
 *     expandable list of edges grouped by targetDatasetId
 *
 * Vocabulary lock (amendment §4.B5): "provenance" / "derivation" — NEVER
 * "lineage". The cloud's ``classLineage`` is class-ISA lineage (a
 * spikesorting doc's superclass chain), a completely different concept.
 *
 * Empty states preserve the "[] vs null" distinction used in B1:
 *   - ``branchOf === null`` → "Not a branch"
 *   - ``branches === []``   → "No branches"
 *   - ``documentDependencies === []`` → "No cross-dataset dependencies"
 */
import { useMemo, useState } from 'react';
import { Link } from 'react-router-dom';
import { ChevronDown, ChevronRight, GitBranch, Network } from 'lucide-react';

import { Badge } from '@/components/ui/Badge';
import {
  Card,
  CardBody,
  CardDescription,
  CardHeader,
  CardTitle,
} from '@/components/ui/Card';
import { cn } from '@/lib/cn';
import type {
  DatasetDependencyEdge,
  DatasetProvenance,
} from '@/types/dataset-provenance';

export interface DatasetProvenanceCardProps {
  provenance: DatasetProvenance;
  className?: string;
}

export function DatasetProvenanceCard({
  provenance,
  className,
}: DatasetProvenanceCardProps) {
  return (
    <Card className={className} data-testid="dataset-provenance-card">
      <CardHeader>
        <CardTitle className="text-base">Dataset provenance</CardTitle>
        <CardDescription className="text-xs">
          Branches, parents, and cross-dataset document references for{' '}
          <span className="font-mono">{provenance.datasetId}</span>.
        </CardDescription>
      </CardHeader>
      <CardBody className="space-y-5 text-sm">
        <BranchOfSection branchOf={provenance.branchOf} />
        <BranchesSection branches={provenance.branches} />
        <DependenciesSection edges={provenance.documentDependencies} />
      </CardBody>
    </Card>
  );
}

// ---------------------------------------------------------------------------
// Sections
// ---------------------------------------------------------------------------

function SectionHeading({ children }: { children: React.ReactNode }) {
  return (
    <h2 className="text-xs font-semibold uppercase tracking-wide text-gray-500">
      {children}
    </h2>
  );
}

function BranchOfSection({ branchOf }: { branchOf: string | null }) {
  return (
    <section
      aria-label="Branched from"
      className="space-y-2"
      data-testid="provenance-branch-of"
    >
      <SectionHeading>Branched from</SectionHeading>
      {branchOf === null ? (
        <p
          className="text-[11px] italic text-gray-500"
          data-testid="provenance-not-a-branch"
        >
          Not a branch
        </p>
      ) : (
        <Link
          to={`/datasets/${branchOf}`}
          className="inline-flex items-center gap-1.5 font-mono text-xs text-brand-600 hover:underline"
          data-testid="provenance-branch-of-link"
        >
          <GitBranch className="h-3 w-3 shrink-0" aria-hidden />
          {branchOf}
        </Link>
      )}
    </section>
  );
}

function BranchesSection({ branches }: { branches: string[] }) {
  return (
    <section
      aria-label="Branches"
      className="space-y-2"
      data-testid="provenance-branches"
    >
      <SectionHeading>Branches</SectionHeading>
      {branches.length === 0 ? (
        <p
          className="text-[11px] italic text-gray-500"
          data-testid="provenance-no-branches"
        >
          No branches
        </p>
      ) : (
        <ul className="flex flex-wrap gap-1.5">
          {branches.map((childId) => (
            <li key={childId}>
              <Link
                to={`/datasets/${childId}`}
                className={cn(
                  'inline-flex items-center gap-1 rounded-full px-2 py-0.5 font-mono text-[11px] ring-1 ring-inset',
                  'bg-brand-50 text-brand-800 ring-brand-200 hover:bg-brand-100',
                )}
                data-testid="provenance-branch-chip"
              >
                <GitBranch className="h-3 w-3 shrink-0" aria-hidden />
                {childId}
              </Link>
            </li>
          ))}
        </ul>
      )}
    </section>
  );
}

function DependenciesSection({ edges }: { edges: DatasetDependencyEdge[] }) {
  const [expanded, setExpanded] = useState(false);

  // Group edges by targetDatasetId so the UI shows "DSY: via element (2), via
  // element_epoch (1)". Stable sort on the backend already places
  // same-target edges together; preserve that ordering here.
  const byTarget = useMemo(() => {
    const map = new Map<string, DatasetDependencyEdge[]>();
    for (const e of edges) {
      const bucket = map.get(e.targetDatasetId);
      if (bucket) {
        bucket.push(e);
      } else {
        map.set(e.targetDatasetId, [e]);
      }
    }
    return map;
  }, [edges]);

  const totalEdges = edges.length;
  // Sum of per-(target, class) distinct-ndiId counts across all edges.
  // NOT a per-document count — see `edgeCount` JSDoc: two source docs
  // sharing a target ndiId contribute 1, not 2.
  const totalRefs = edges.reduce((acc, e) => acc + e.edgeCount, 0);
  const targetCount = byTarget.size;

  return (
    <section
      aria-label="Cross-dataset dependencies"
      className="space-y-2"
      data-testid="provenance-dependencies"
    >
      <SectionHeading>Dependencies</SectionHeading>
      {totalEdges === 0 ? (
        <p
          className="text-[11px] italic text-gray-500"
          data-testid="provenance-no-dependencies"
        >
          No cross-dataset dependencies
        </p>
      ) : (
        <>
          <button
            type="button"
            className="inline-flex w-full items-center justify-between rounded-md border border-gray-200 bg-gray-50 px-2 py-1.5 text-left text-xs hover:bg-gray-100 focus:outline-none focus-visible:ring-2 focus-visible:ring-brand-500"
            onClick={() => setExpanded((v) => !v)}
            aria-expanded={expanded}
            data-testid="provenance-dependencies-toggle"
          >
            <span className="flex items-center gap-1.5 text-gray-700">
              <Network className="h-3 w-3 shrink-0" aria-hidden />
              <span>
                <span
                  className="font-mono"
                  data-testid="provenance-refs-count"
                >
                  {totalRefs}
                </span>{' '}
                cross-dataset reference{totalRefs === 1 ? '' : 's'} to{' '}
                <span
                  className="font-mono"
                  data-testid="provenance-targets-count"
                >
                  {targetCount}
                </span>{' '}
                other dataset{targetCount === 1 ? '' : 's'}
              </span>
            </span>
            {expanded ? (
              <ChevronDown className="h-3 w-3 shrink-0" aria-hidden />
            ) : (
              <ChevronRight className="h-3 w-3 shrink-0" aria-hidden />
            )}
          </button>
          {expanded && (
            <ul
              className="space-y-2 pl-1"
              data-testid="provenance-dependencies-list"
            >
              {Array.from(byTarget.entries()).map(([targetId, targetEdges]) => (
                <li
                  key={targetId}
                  className="space-y-1"
                  data-testid="provenance-target-group"
                  data-target-dataset-id={targetId}
                >
                  <Link
                    to={`/datasets/${targetId}`}
                    className="inline-flex items-center gap-1.5 font-mono text-[11px] text-brand-600 hover:underline"
                    data-testid="provenance-target-link"
                  >
                    <Network className="h-3 w-3 shrink-0" aria-hidden />
                    {targetId}
                  </Link>
                  <ul className="space-y-0.5 pl-4">
                    {targetEdges.map((e) => (
                      <li
                        key={`${e.targetDatasetId}:${e.viaDocumentClass}`}
                        className="flex items-center gap-2 text-[11px]"
                        data-testid="provenance-edge-row"
                      >
                        <Badge
                          variant="secondary"
                          className="font-mono text-[10px]"
                        >
                          {e.viaDocumentClass}
                        </Badge>
                        <span className="text-gray-600">
                          {e.edgeCount} ref{e.edgeCount === 1 ? '' : 's'}
                        </span>
                      </li>
                    ))}
                  </ul>
                </li>
              ))}
            </ul>
          )}
        </>
      )}
    </section>
  );
}
