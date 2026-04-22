import { useEffect, useState } from 'react';

import { cn } from '@/lib/cn';

export function Skeleton({ className }: { className?: string }) {
  return <div className={cn('skeleton', className)} aria-hidden />;
}

export function TableSkeleton({ rows = 8 }: { rows?: number }) {
  return (
    <div className="space-y-2" aria-label="Loading table">
      <Skeleton className="h-8 w-full" />
      {Array.from({ length: rows }).map((_, i) => (
        <Skeleton key={i} className="h-6 w-full" />
      ))}
    </div>
  );
}

/**
 * Skeleton with a live elapsed-time hint below it. Serves as the non-streaming
 * "progress probe" from plan §M7-4: we can't tell the user N-of-M documents
 * fetched (no SSE endpoint was shipped; see plan §M4a-4), but we can at least
 * confirm the tab is progressing rather than hung. The "Still working…" copy
 * flips in at 8s when a cold Haley combined-table build is most at risk of
 * looking like a frozen page.
 */
export function TableLoadingPanel({
  tableType,
  rows = 8,
}: {
  tableType: string;
  rows?: number;
}) {
  const [elapsed, setElapsed] = useState(0);
  useEffect(() => {
    const start = Date.now();
    const t = setInterval(() => {
      setElapsed(Math.floor((Date.now() - start) / 1000));
    }, 500);
    return () => clearInterval(t);
  }, []);
  const label =
    elapsed < 1
      ? `Loading ${tableType} table…`
      : elapsed < 8
        ? `Loading ${tableType} table… ${elapsed}s elapsed`
        : `Still working on ${tableType} table… ${elapsed}s elapsed (cold cache may take up to a minute)`;
  return (
    <div className="space-y-2" aria-label={`Loading ${tableType} table`}>
      <div
        className="text-xs text-gray-500 dark:text-gray-400 font-mono"
        role="status"
        aria-live="polite"
      >
        {label}
      </div>
      <TableSkeleton rows={rows} />
    </div>
  );
}

export function CardSkeleton() {
  return (
    <div className="space-y-2 p-4 rounded-lg bg-bg-surface ring-1 ring-border-subtle">
      <Skeleton className="h-5 w-2/3" />
      <Skeleton className="h-4 w-full" />
      <Skeleton className="h-4 w-5/6" />
    </div>
  );
}
