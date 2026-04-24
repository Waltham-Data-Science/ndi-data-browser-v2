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
 *
 * Audit 2026-04-23 (#67): on dataset detail pages this hook used to
 * stamp the static "Dataset — NDI Data Browser" regardless of which
 * dataset was being viewed, which made LinkedIn / Slack / Discord link
 * previews useless and gave screen-reader users the same announcement
 * for every dataset. Now any page with a dynamic title (dataset detail,
 * document detail) can call ``setDocumentTitle(label)`` imperatively
 * once its own query resolves, via the DYNAMIC_TITLE_STACK contract
 * below. The hook itself keeps its route-based default behavior for
 * static routes.
 *
 * Since this is a Vite SPA with no SSR, crawlers still see the static
 * index.html title. That's a tracked follow-up (ship react-helmet-async
 * or @unhead/react once SEO becomes a priority).
 */
const DYNAMIC_TITLE_SYMBOL = Symbol.for('ndi-dynamic-title');
interface DynamicTitleState {
  pathname: string;
  title: string;
}
// Stashed on globalThis so the dynamic setter and the route hook can
// share state without a dedicated context provider.
function getDynamicState(): DynamicTitleState | null {
  return (globalThis as unknown as Record<symbol, unknown>)[DYNAMIC_TITLE_SYMBOL] as
    | DynamicTitleState
    | null;
}
function setDynamicState(state: DynamicTitleState | null): void {
  (globalThis as unknown as Record<symbol, unknown>)[DYNAMIC_TITLE_SYMBOL] = state;
}

/** Set a page-specific document title. Pass the page's pathname too so
 * we can auto-clear when the user navigates away. The returned cleanup
 * fn is a no-op safety net — the pathname-match check handles normal
 * navigation. */
export function setDocumentTitle(pathname: string, title: string): () => void {
  setDynamicState({ pathname, title });
  document.title = `${title} — NDI Data Browser`;
  return () => {
    const cur = getDynamicState();
    if (cur && cur.pathname === pathname) setDynamicState(null);
  };
}

export function useDocumentTitle() {
  const { pathname } = useLocation();

  useEffect(() => {
    const base = 'NDI Data Browser';

    // Dynamic title wins if set by the current route (dataset detail,
    // document detail, etc. — see setDocumentTitle).
    const dynamic = getDynamicState();
    if (dynamic && dynamic.pathname === pathname) {
      document.title = `${dynamic.title} — ${base}`;
      return;
    }
    // Clear stale dynamic state when route changes.
    if (dynamic && dynamic.pathname !== pathname) {
      setDynamicState(null);
    }

    // Check static routes first
    const staticTitle = ROUTE_TITLES[pathname];
    if (staticTitle) {
      document.title = `${staticTitle} — ${base}`;
      return;
    }

    // Dataset detail pages: generic fallback until the page calls
    // setDocumentTitle(dataset.name) once its query resolves.
    if (pathname.startsWith('/datasets/') && pathname !== '/datasets') {
      const parts = pathname.split('/');
      if (parts.length >= 3) {
        document.title = `Dataset — ${base}`;
        return;
      }
    }

    document.title = base;
  }, [pathname]);
}
