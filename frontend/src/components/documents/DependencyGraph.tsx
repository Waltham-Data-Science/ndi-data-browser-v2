import { useState } from 'react';
import { Link } from 'react-router-dom';
import {
  ArrowDown,
  ArrowUp,
  GitBranch,
  LayoutList,
  Loader2,
  Network,
} from 'lucide-react';

import {
  useDependencyGraph,
  type DepGraphEdge,
  type DepGraphNode,
} from '@/api/documents';
import { Badge } from '@/components/ui/Badge';
import { Card, CardBody, CardHeader, CardTitle } from '@/components/ui/Card';
import { cn } from '@/lib/cn';

interface DependencyGraphProps {
  datasetId: string;
  documentId: string;
  /** Max depth in each direction. Backend hard-caps at 3. */
  maxDepth?: number;
}

type ViewMode = 'visual' | 'text';

/**
 * Visual + text dependency graph — ported from v1 (333 LOC). Differences:
 *
 * - Edges key on `ndiId` in v2 (v1 keyed on mongo id). The NodeBox
 *   Link uses `node.id` (mongo) when available, falling back to `ndiId`
 *   if mongo is unresolved for cross-dataset refs.
 * - Node dedup respects the `isTarget` flag from the backend.
 * - Renders a small "truncated — more edges exist" banner when the
 *   backend flagged the BFS as incomplete.
 */
function DepGraphEmpty({ message }: { message: string }) {
  return (
    <Card>
      <CardHeader className="py-3">
        <CardTitle className="text-xs font-medium flex items-center gap-1">
          <GitBranch className="h-3.5 w-3.5" />
          Dependency Graph
        </CardTitle>
      </CardHeader>
      <CardBody className="pt-0">
        <p className="text-xs text-fg-muted leading-relaxed">{message}</p>
      </CardBody>
    </Card>
  );
}

export function DependencyGraphView({
  datasetId,
  documentId,
  maxDepth = 3,
}: DependencyGraphProps) {
  const [viewMode, setViewMode] = useState<ViewMode>('visual');
  const { data: graph, isLoading, error } = useDependencyGraph(
    datasetId,
    documentId,
    maxDepth,
  );

  if (isLoading) {
    return (
      <Card>
        <CardHeader className="py-3">
          <CardTitle className="text-xs font-medium flex items-center gap-1">
            <GitBranch className="h-3.5 w-3.5" />
            Dependency Graph
          </CardTitle>
        </CardHeader>
        <CardBody className="pt-0">
          <div className="flex items-center gap-2 py-4 justify-center">
            <Loader2 className="h-3.5 w-3.5 animate-spin text-gray-400" />
            <span className="text-xs text-gray-500">
              Building dependency graph…
            </span>
          </div>
        </CardBody>
      </Card>
    );
  }

  // Error / missing-data cases render a compact empty-state card so
  // users know the dep graph widget exists and why it's empty. The
  // previous behavior returned null, which made users think the
  // feature was broken ("dep graph failing on all of them") when in
  // fact root documents like `subject` simply have no dependencies
  // to walk.
  if (error || !graph || graph.error) {
    return (
      <DepGraphEmpty
        message={
          error
            ? 'Could not build the dependency graph. Try again in a moment.'
            : (graph?.error as string | undefined) ??
              'No dependency data available for this document.'
        }
      />
    );
  }

  if (graph.node_count <= 1) {
    return (
      <DepGraphEmpty message="No upstream or downstream dependencies — this document is a leaf in the dependency graph (e.g. a subject or a root probe)." />
    );
  }

  const targetNdi = graph.target_ndi_id;
  if (!targetNdi) {
    return (
      <DepGraphEmpty message="This document has no ndiId, so its dependency graph can't be resolved." />
    );
  }

  const nodeMap = new Map<string, DepGraphNode>();
  for (const n of graph.nodes) nodeMap.set(n.ndiId, n);
  const targetNode = nodeMap.get(targetNdi);
  if (!targetNode) return null;

  const upstreamEdges = graph.edges.filter((e) => e.direction === 'upstream');
  const downstreamEdges = graph.edges.filter((e) => e.direction === 'downstream');

  return (
    <Card>
      <CardHeader className="py-3">
        <div className="flex items-center justify-between flex-wrap gap-2">
          <CardTitle className="text-xs font-medium flex items-center gap-1">
            <GitBranch className="h-3.5 w-3.5" />
            Dependency Graph
            <Badge variant="secondary" className="font-mono ml-1">
              {graph.node_count} nodes · {graph.edge_count} edges
            </Badge>
            {graph.truncated && (
              <Badge variant="outline" className="font-mono text-[10px] ml-1">
                truncated at depth {graph.max_depth}
              </Badge>
            )}
          </CardTitle>
          <div className="flex items-center rounded-md border border-gray-200 overflow-hidden">
            <button
              type="button"
              onClick={() => setViewMode('visual')}
              className={cn(
                'flex items-center gap-1 px-2 py-1 text-[10px] transition-colors',
                viewMode === 'visual'
                  ? 'bg-gray-900 text-white'
                  : 'text-gray-600 hover:text-gray-900:text-gray-100',
              )}
              title="Visual tree"
              aria-pressed={viewMode === 'visual'}
            >
              <Network className="h-3 w-3" />
              Visual
            </button>
            <button
              type="button"
              onClick={() => setViewMode('text')}
              className={cn(
                'flex items-center gap-1 px-2 py-1 text-[10px] transition-colors',
                viewMode === 'text'
                  ? 'bg-gray-900 text-white'
                  : 'text-gray-600 hover:text-gray-900:text-gray-100',
              )}
              title="Text list"
              aria-pressed={viewMode === 'text'}
            >
              <LayoutList className="h-3 w-3" />
              List
            </button>
          </div>
        </div>
      </CardHeader>
      <CardBody className="pt-0">
        {viewMode === 'visual' ? (
          <VisualView
            upstreamEdges={upstreamEdges}
            downstreamEdges={downstreamEdges}
            nodeMap={nodeMap}
            targetNode={targetNode}
            datasetId={datasetId}
          />
        ) : (
          <TextView
            upstreamEdges={upstreamEdges}
            downstreamEdges={downstreamEdges}
            nodeMap={nodeMap}
            datasetId={datasetId}
          />
        )}
      </CardBody>
    </Card>
  );
}

function NodeBox({
  node,
  datasetId,
  isTarget,
}: {
  node: DepGraphNode;
  datasetId: string;
  isTarget?: boolean;
}) {
  const label = node.name || node.ndiId.slice(0, 20) + '…';
  const content = (
    <div
      className={cn(
        'inline-flex items-center gap-1.5 px-2.5 py-1.5 rounded-md border text-xs font-mono transition-colors',
        isTarget
          ? 'border-brand-400 bg-brand-50 ring-2 ring-brand-200/60'
          : 'border-gray-200 bg-white hover:border-brand-300',
      )}
    >
      <Badge
        variant={isTarget ? 'default' : 'outline'}
        className="font-mono shrink-0"
      >
        {node.className || 'document'}
      </Badge>
      <span
        className={cn(
          'truncate max-w-[200px]',
          isTarget
            ? 'text-brand-800 font-medium'
            : 'text-gray-600',
        )}
      >
        {label}
      </span>
    </div>
  );
  if (isTarget || !node.id) return content;
  return (
    <Link
      to={`/datasets/${datasetId}/documents/${node.id}`}
      className="hover:opacity-80 transition-opacity"
    >
      {content}
    </Link>
  );
}

function Connector({ label, direction }: { label?: string; direction: 'up' | 'down' }) {
  return (
    <div className="flex flex-col items-center gap-0">
      {direction === 'down' && <div className="w-px h-4 bg-gray-200" />}
      {label && (
        <span className="text-[9px] text-gray-500 font-mono px-1.5 py-0.5 bg-gray-100 rounded">
          {label}
        </span>
      )}
      <div className="w-px h-4 bg-gray-200" />
      {direction === 'up' ? (
        <ArrowUp className="h-3 w-3 text-brand-500" />
      ) : (
        <ArrowDown className="h-3 w-3 text-emerald-500" />
      )}
      <div className="w-px h-2 bg-gray-200" />
    </div>
  );
}

function VisualView({
  upstreamEdges,
  downstreamEdges,
  nodeMap,
  targetNode,
  datasetId,
}: {
  upstreamEdges: DepGraphEdge[];
  downstreamEdges: DepGraphEdge[];
  nodeMap: Map<string, DepGraphNode>;
  targetNode: DepGraphNode;
  datasetId: string;
}) {
  return (
    <div className="flex flex-col items-center gap-0 py-2">
      {upstreamEdges.length > 0 && (
        <>
          <div className="text-[10px] text-gray-500 font-medium mb-2 flex items-center gap-1">
            <ArrowUp className="h-3 w-3 text-brand-500" />
            Depends on
          </div>
          <div className="flex flex-wrap items-end justify-center gap-3 mb-1">
            {upstreamEdges.map((edge, i) => {
              const node = nodeMap.get(edge.target);
              if (!node) return null;
              return (
                <div key={i} className="flex flex-col items-center">
                  <NodeBox node={node} datasetId={datasetId} />
                  <Connector label={edge.label} direction="down" />
                </div>
              );
            })}
          </div>
          {upstreamEdges.length > 1 && (
            <>
              <div className="w-3/4 max-w-xs h-px bg-gray-200 mb-0" />
              <div className="w-px h-3 bg-gray-200" />
            </>
          )}
        </>
      )}

      <NodeBox node={targetNode} datasetId={datasetId} isTarget />

      {downstreamEdges.length > 0 && (
        <>
          {downstreamEdges.length > 1 && (
            <>
              <div className="w-px h-3 bg-gray-200" />
              <div className="w-3/4 max-w-xs h-px bg-gray-200 mt-0" />
            </>
          )}
          <div className="flex flex-wrap items-start justify-center gap-3 mt-1">
            {downstreamEdges.map((edge, i) => {
              const node = nodeMap.get(edge.source);
              if (!node) return null;
              return (
                <div key={i} className="flex flex-col items-center">
                  <Connector label={edge.label} direction="down" />
                  <NodeBox node={node} datasetId={datasetId} />
                </div>
              );
            })}
          </div>
          <div className="text-[10px] text-gray-500 font-medium mt-2 flex items-center gap-1">
            <ArrowDown className="h-3 w-3 text-emerald-500" />
            Depended on by
          </div>
        </>
      )}
    </div>
  );
}

function TextView({
  upstreamEdges,
  downstreamEdges,
  nodeMap,
  datasetId,
}: {
  upstreamEdges: DepGraphEdge[];
  downstreamEdges: DepGraphEdge[];
  nodeMap: Map<string, DepGraphNode>;
  datasetId: string;
}) {
  return (
    <div className="space-y-3">
      {upstreamEdges.length > 0 && (
        <div>
          <div className="flex items-center gap-1 text-[10px] text-gray-500 font-medium mb-1.5">
            <ArrowUp className="h-3 w-3" />
            Depends on ({upstreamEdges.length})
          </div>
          <div className="space-y-1 pl-4 border-l-2 border-brand-200">
            {upstreamEdges.map((edge, i) => {
              const node = nodeMap.get(edge.target);
              if (!node) return null;
              return <EdgeRow key={i} edge={edge} node={node} datasetId={datasetId} />;
            })}
          </div>
        </div>
      )}
      {downstreamEdges.length > 0 && (
        <div>
          <div className="flex items-center gap-1 text-[10px] text-gray-500 font-medium mb-1.5">
            <ArrowDown className="h-3 w-3" />
            Depended on by ({downstreamEdges.length})
          </div>
          <div className="space-y-1 pl-4 border-l-2 border-emerald-200">
            {downstreamEdges.map((edge, i) => {
              const node = nodeMap.get(edge.source);
              if (!node) return null;
              return <EdgeRow key={i} edge={edge} node={node} datasetId={datasetId} />;
            })}
          </div>
        </div>
      )}
    </div>
  );
}

function EdgeRow({
  edge,
  node,
  datasetId,
}: {
  edge: DepGraphEdge;
  node: DepGraphNode;
  datasetId: string;
}) {
  const linkTarget = node.id;
  return (
    <div className="flex items-center gap-2 text-xs">
      <span className="text-gray-500 font-mono text-[10px] w-20 shrink-0 truncate">
        {edge.label}
      </span>
      <Badge variant="outline" className="font-mono shrink-0">
        {node.className || 'document'}
      </Badge>
      {linkTarget ? (
        <Link
          to={`/datasets/${datasetId}/documents/${linkTarget}`}
          className="font-mono text-[10px] text-brand-600 hover:underline truncate"
        >
          {node.name || node.ndiId}
        </Link>
      ) : (
        <span className="font-mono text-[10px] text-gray-500 truncate">
          {node.name || node.ndiId}
        </span>
      )}
    </div>
  );
}
