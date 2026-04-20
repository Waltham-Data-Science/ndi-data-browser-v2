import { useState } from 'react';

import { useMyDatasets, type MyScope } from '@/api/datasets';
import { useMe } from '@/api/auth';
import { DatasetCard } from '@/components/datasets/DatasetCard';
import { Badge } from '@/components/ui/Badge';
import { CardSkeleton } from '@/components/ui/Skeleton';
import { ErrorState } from '@/components/errors/ErrorState';
import { cn } from '@/lib/cn';
import { formatNumber } from '@/lib/format';

/**
 * Logged-in-user view of every dataset owned by the caller's org(s).
 *
 * Backend route: ``GET /api/datasets/my`` aggregates cloud's
 * ``/organizations/:orgId/datasets`` across every org on
 * ``session.organization_ids``. Returns the same shape as
 * ``/api/datasets/published`` (including embedded compact summaries),
 * so `DatasetCard` renders published / in-review / draft states
 * uniformly — the per-row ``publishStatus`` badge distinguishes them.
 *
 * From here the user can drill into any row's detail page
 * (``/datasets/:id``); the backend forwards the Cognito access token
 * from the session so the cloud's permission filter allows access to
 * unpublished / draft datasets the caller's org owns. Pre-2026-04-20
 * this surface only showed the narrow ``isPublished=false AND
 * isSubmitted=true`` slice — see CLAUDE.md "My Org datasets".
 */
export function MyDatasetsPage() {
  const me = useMe();
  const isAdmin = me.data?.isAdmin ?? false;
  // Scope toggle — admins only. `mine` = per-org aggregation (default);
  // `all` = legacy cross-org in-review firehose (see backend
  // `datasets.my` route for details). Non-admins never see the toggle
  // UI; and the backend silently downgrades `scope=all` if they try to
  // pass it manually.
  const [scope, setScope] = useState<MyScope>('mine');
  const activeScope: MyScope = isAdmin ? scope : 'mine';
  const q = useMyDatasets(me.isSuccess, activeScope);

  if (me.isError) return <ErrorState error={me.error} />;
  if (me.isLoading) return <CardSkeleton />;

  const total = q.data?.totalNumber ?? q.data?.datasets?.length ?? 0;
  const orgCount = me.data?.organizationIds?.length ?? 0;

  return (
    <div className="space-y-5">
      <header className="space-y-2">
        <div className="flex flex-wrap items-center gap-2">
          <h1 className="text-2xl font-bold text-gray-900 dark:text-gray-100">
            My organization&apos;s datasets
          </h1>
          {isAdmin && <Badge variant="secondary">admin</Badge>}
        </div>
        <p className="text-sm text-gray-600 dark:text-gray-300">
          {activeScope === 'all'
            ? 'Admin debug view — every in-review dataset across every org in the cloud (legacy /datasets/unpublished firehose).'
            : 'Every dataset owned by your organization — published, in-review, and drafts. Click any card for full detail (subjects, probes, epochs, raw documents) just like the public catalog.'}
        </p>
        {isAdmin && (
          <ScopeToggle value={scope} onChange={setScope} />
        )}
        {q.data && (
          <p className="text-xs text-gray-500 dark:text-gray-400 font-mono">
            {formatNumber(total)} {total === 1 ? 'dataset' : 'datasets'}
            {activeScope === 'mine' && orgCount > 1
              ? ` · ${orgCount} organizations`
              : ''}
          </p>
        )}
      </header>

      {q.isLoading && (
        <div className="grid gap-3 sm:grid-cols-2 xl:grid-cols-3">
          {Array.from({ length: 4 }).map((_, i) => (
            <CardSkeleton key={i} />
          ))}
        </div>
      )}

      {q.isError && <ErrorState error={q.error} onRetry={() => q.refetch()} />}

      {!q.isLoading && !q.isError && q.data && q.data.datasets.length === 0 && (
        <div className="rounded-lg border border-dashed border-gray-200 dark:border-gray-700 p-10 text-center">
          <p className="text-sm text-gray-600 dark:text-gray-400">
            {activeScope === 'all'
              ? 'No in-review datasets in any org — the cloud\u2019s cross-org admin view is empty.'
              : 'No datasets in your organization yet.'}
          </p>
          <p className="mt-2 text-xs text-gray-500 dark:text-gray-500">
            {activeScope === 'all'
              ? 'Switch back to "My org only" for your org-scoped view.'
              : 'Datasets you upload via NDI Cloud will appear here — published work, in-review submissions, and drafts.'}
          </p>
        </div>
      )}

      {!q.isLoading && q.data && q.data.datasets.length > 0 && (
        <div className="grid gap-3 sm:grid-cols-2 xl:grid-cols-3">
          {q.data.datasets.map((d) => (
            <DatasetCard key={d.id} dataset={d} />
          ))}
        </div>
      )}
    </div>
  );
}

function ScopeToggle({
  value,
  onChange,
}: {
  value: MyScope;
  onChange: (next: MyScope) => void;
}) {
  return (
    <div
      role="group"
      aria-label="Dataset scope"
      className="inline-flex items-center rounded-md border border-gray-200 dark:border-gray-700 overflow-hidden text-xs"
      data-testid="my-scope-toggle"
    >
      <ToggleButton
        active={value === 'mine'}
        onClick={() => onChange('mine')}
      >
        My org only
      </ToggleButton>
      <ToggleButton
        active={value === 'all'}
        onClick={() => onChange('all')}
      >
        All orgs (admin)
      </ToggleButton>
    </div>
  );
}

function ToggleButton({
  active,
  onClick,
  children,
}: {
  active: boolean;
  onClick: () => void;
  children: React.ReactNode;
}) {
  return (
    <button
      type="button"
      onClick={onClick}
      aria-pressed={active}
      className={cn(
        'px-3 py-1.5 font-medium transition-colors',
        active
          ? 'bg-gray-900 text-white dark:bg-white dark:text-gray-900'
          : 'text-gray-600 dark:text-gray-400 hover:text-gray-900 dark:hover:text-gray-100',
      )}
    >
      {children}
    </button>
  );
}
