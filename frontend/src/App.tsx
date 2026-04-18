import { BrowserRouter, Routes, Route, Navigate } from 'react-router-dom';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { AppShell } from '@/components/layout/AppShell';
import { AboutPage } from '@/pages/AboutPage';
import { HomePage } from '@/pages/HomePage';
import { DatasetsPage } from '@/pages/DatasetsPage';
import { DatasetDetailPage } from '@/pages/DatasetDetailPage';
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

export function App() {
  return (
    <ErrorBoundary>
      <QueryClientProvider client={queryClient}>
        <BrowserRouter>
          <Routes>
            <Route element={<AppShell />}>
              <Route index element={<HomePage />} />
              <Route path="datasets" element={<DatasetsPage />} />
              <Route path="datasets/:id" element={<DatasetDetailPage />}>
                <Route index element={<Navigate to="tables/subject" replace />} />
                {/* Legacy class slugs with hyphens/underscores resolved in TableTab */}
                <Route path="tables/:className" element={<TableTab />} />
                {/* M4c: Summary Tables / Raw Documents toggle. */}
                <Route path="documents" element={<DocumentExplorerPage />} />
                {/* Plan B B6e: grain-selectable pivot (subject/session/element). */}
                <Route path="pivot/:grain" element={<PivotView />} />
              </Route>
              <Route path="datasets/:id/documents/:docId" element={<DocumentDetailPage />} />
              <Route path="my" element={<MyDatasetsPage />} />
              <Route path="query" element={<QueryPage />} />
              <Route path="about" element={<AboutPage />} />
              <Route path="login" element={<LoginPage />} />
              <Route path="*" element={<NotFoundPage />} />
            </Route>
          </Routes>
        </BrowserRouter>
      </QueryClientProvider>
    </ErrorBoundary>
  );
}
