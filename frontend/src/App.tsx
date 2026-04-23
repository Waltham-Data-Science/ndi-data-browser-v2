import { BrowserRouter, Routes, Route, Navigate } from 'react-router-dom';
import { QueryClient } from '@tanstack/react-query';
import {
  PersistQueryClientProvider,
  type PersistedClient,
  type Persister,
} from '@tanstack/react-query-persist-client';
import { createSyncStoragePersister } from '@tanstack/query-sync-storage-persister';
import { AppShell } from '@/components/layout/AppShell';
import { AboutPage } from '@/pages/AboutPage';
import { HomePage } from '@/pages/HomePage';
import { DatasetsPage } from '@/pages/DatasetsPage';
import {
  DatasetDetailPage,
  OverviewTab,
} from '@/pages/DatasetDetailPage';
import { DocumentExplorerPage } from '@/pages/DocumentExplorerPage';
import { PivotView } from '@/components/datasets/PivotView';
import { TableTab } from '@/pages/TableTab';
import { DocumentDetailPage } from '@/pages/DocumentDetailPage';
import { LoginPage } from '@/pages/LoginPage';
import { MyDatasetsPage } from '@/pages/MyDatasetsPage';
import { QueryPage } from '@/pages/QueryPage';
import { NotFoundPage } from '@/pages/NotFoundPage';
import { ErrorBoundary } from '@/components/errors/ErrorBoundary';
import { ApiError } from '@/api/errors';

/**
 * Cache strategy
 * ──────────────
 * Two-tier client-side caching keeps perceived latency near zero on
 * revisits without sacrificing correctness:
 *
 * 1. In-memory TanStack Query cache (`staleTime: 60s`, `gcTime: 30m`)
 *    — same as before. Within a session a query is fresh for 60s;
 *    navigating back to a page within that window is a no-round-trip
 *    render.
 *
 * 2. Persisted cache via localStorage (`maxAge: 1h`) — new. Survives
 *    page refresh and tab close. When the user returns, the SPA
 *    hydrates the cache before any network call, so `/datasets`,
 *    `/my`, and dataset detail pages paint with their last-known
 *    data immediately, then TanStack Query revalidates in the
 *    background. The `buster` key is a build-hash-like string we
 *    bump on breaking shape changes; any mismatch wipes the cache on
 *    next mount so a stale client can't display fields that no
 *    longer exist on the new response shape.
 *
 * Auth-gated queries (`useMe`, `useMyDatasets`) persist too — that's
 * fine because localStorage is origin-scoped to `app.ndi-cloud.com`
 * and the SPA already trusts whatever local storage holds (e.g., the
 * returnTo path in LoginPage). If a user logs out, `useLogout` clears
 * the whole TanStack cache via `qc.clear()`, which also triggers a
 * persistence write — so the localStorage snapshot no longer contains
 * the previous user's data.
 */

// Bump this string whenever a response shape changes in a way that
// would make a cached old response dangerous to render (e.g. required
// field removal). The persistence layer compares this against the
// stored value on hydration; any mismatch → cache wiped.
//
// Format: semantic-version-ish. Touching this triggers a one-time
// cache wipe across all users on next visit after deploy.
const CACHE_BUSTER = 'v2-2026-04-22';

const queryClient = new QueryClient({
  defaultOptions: {
    queries: {
      staleTime: 60_000,
      gcTime: 30 * 60 * 1000,
      retry: (failureCount, error) => {
        if (error instanceof ApiError) {
          // Never retry auth or user errors.
          if (error.status === 401 || error.status === 403 || error.status === 404 || error.status === 400) {
            return false;
          }
        }
        return failureCount < 2;
      },
      refetchOnWindowFocus: false,
    },
    mutations: { retry: false },
  },
});

// Guard: SSR / tests without a localStorage. Fall back to an
// in-memory no-op persister so the app still renders.
function makePersister(): Persister {
  if (typeof window === 'undefined' || !window.localStorage) {
    let snapshot: PersistedClient | undefined;
    return {
      persistClient: async (client) => {
        snapshot = client;
      },
      restoreClient: async () => snapshot,
      removeClient: async () => {
        snapshot = undefined;
      },
    };
  }
  return createSyncStoragePersister({
    storage: window.localStorage,
    // Namespaced so other apps / older builds on the same origin
    // don't clobber our cache key.
    key: 'ndi-query-cache',
    // Throttle writes so a burst of mutations doesn't spam
    // localStorage.setItem (which is sync and can be slow).
    throttleTime: 1_000,
  });
}

const persister = makePersister();

export function App() {
  return (
    <ErrorBoundary>
      <PersistQueryClientProvider
        client={queryClient}
        persistOptions={{
          persister,
          // One hour — long enough that "close tab, come back to the
          // same dataset in an hour" is instant, short enough that a
          // changed-dataset window of staleness stays bounded.
          maxAge: 60 * 60 * 1000,
          buster: CACHE_BUSTER,
          dehydrateOptions: {
            // Don't persist pending/errored queries — they'd re-hydrate
            // into an unhelpful "forever loading" or "forever error"
            // state. Only successful responses are worth carrying over.
            shouldDehydrateQuery: (query) => query.state.status === 'success',
          },
        }}
      >
        <BrowserRouter>
          <Routes>
            <Route element={<AppShell />}>
              <Route index element={<HomePage />} />
              <Route path="datasets" element={<DatasetsPage />} />
              {/* Dataset detail shell — hero + tab bar + outlet. Three
                  tabs share the same shell so the hero, tab bar, and
                  page chrome render once and only the tab content
                  swaps. Legacy `/datasets/:id` bookmarks land on
                  Overview (cheap redirect, preserves the URL contract
                  for shared links). */}
              <Route path="datasets/:id" element={<DatasetDetailPage />}>
                <Route index element={<Navigate to="overview" replace />} />
                <Route path="overview" element={<OverviewTab />} />
                {/* Legacy class slugs with hyphens/underscores resolved
                    in TableTab. `tables` (no class) redirects to the
                    subject default — matches the previous "open tables
                    tab" UX. */}
                <Route
                  path="tables"
                  element={<Navigate to="subject" replace />}
                />
                <Route path="tables/:className" element={<TableTab />} />
                {/* Plan B B6e: grain-selectable pivot (subject/session/element). */}
                <Route path="pivot/:grain" element={<PivotView />} />
                {/* Raw document explorer — class-filterable list of
                    every NDI document in the dataset. Used to be a
                    top-level page with its own hero; now renders inside
                    the shared shell so the dataset tab bar is
                    discoverable from here. */}
                <Route path="documents" element={<DocumentExplorerPage />} />
              </Route>
              {/* Document detail is a drill-down on a single document,
                  not a tab on the dataset — keep it outside the shell
                  so it renders its own hero and doesn't inherit the
                  dataset tab bar. */}
              <Route path="datasets/:id/documents/:docId" element={<DocumentDetailPage />} />
              <Route path="my" element={<MyDatasetsPage />} />
              <Route path="query" element={<QueryPage />} />
              <Route path="about" element={<AboutPage />} />
              <Route path="login" element={<LoginPage />} />
              <Route path="*" element={<NotFoundPage />} />
            </Route>
          </Routes>
        </BrowserRouter>
      </PersistQueryClientProvider>
    </ErrorBoundary>
  );
}
