import { useEffect } from 'react';
import { useLocation } from 'react-router-dom';

const ROUTE_TITLES: Record<string, string> = {
  '/': 'Datasets',
  '/datasets': 'Datasets',
  '/query': 'Query',
  '/about': 'About',
  '/login': 'Sign In',
  '/my': 'My Org',
};

/**
 * Sets document.title based on the current route.
 * For dataset detail pages, uses "Dataset — NDI Data Browser".
 * Falls back to "NDI Data Browser" for unknown routes.
 */
export function useDocumentTitle() {
  const { pathname } = useLocation();

  useEffect(() => {
    const base = 'NDI Data Browser';

    // Check static routes first
    const staticTitle = ROUTE_TITLES[pathname];
    if (staticTitle) {
      document.title = `${staticTitle} — ${base}`;
      return;
    }

    // Dataset detail pages
    if (pathname.startsWith('/datasets/') && pathname !== '/datasets') {
      const parts = pathname.split('/');
      if (parts.length >= 3) {
        // /datasets/:id or /datasets/:id/tables/:class etc.
        document.title = `Dataset — ${base}`;
        return;
      }
    }

    document.title = base;
  }, [pathname]);
}
