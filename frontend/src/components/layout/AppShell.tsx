import { LogOut } from 'lucide-react';
import { Link, NavLink, Outlet } from 'react-router-dom';

import { useLogout, useMe } from '@/api/auth';
import { cn } from '@/lib/cn';
import { useDocumentTitle } from '@/lib/useDocumentTitle';

/**
 * App chrome — aligned with NDI Cloud design system (April 2026).
 * Glassmorphism header, Geist typography, wordmark logo.
 */
export function AppShell() {
  const me = useMe();
  const logout = useLogout();
  const signedIn = me.isSuccess;

  useDocumentTitle();

  return (
    <div className="min-h-screen flex flex-col bg-gray-50 dark:bg-gray-950">
      <header className="sticky top-0 z-50 bg-black/92 backdrop-blur-[14px] text-white border-b border-white/6">
        <div className="mx-auto flex max-w-7xl items-center gap-6 px-4 py-3">
          <Link
            to="/"
            className="flex items-center gap-2 transition-all hover:opacity-90"
            aria-label="NDI Data Browser home"
          >
            <img
              src="/brand/ndicloud-wordmark-white.svg"
              alt="NDI Cloud"
              className="h-5 w-auto"
            />
          </Link>

          <nav className="flex items-center gap-1 text-sm" aria-label="Primary">
            <NavItem to="/datasets">Datasets</NavItem>
            <NavItem to="/query">Query</NavItem>
            {signedIn && <NavItem to="/my">My Org</NavItem>}
            <NavItem to="/about">About</NavItem>
          </nav>

          <div className="ml-auto flex items-center gap-2 text-sm">
            {signedIn ? (
              <>
                <span className="hidden sm:inline text-gray-400 text-xs" data-testid="me">
                  Signed in
                </span>
                <button
                  type="button"
                  onClick={() => logout.mutate()}
                  className="flex items-center gap-1.5 rounded-full border border-white/15 px-3 py-1 text-xs font-medium text-gray-200 hover:bg-white/8 hover:border-white/25 hover:text-white transition-all focus-visible:outline-2 focus-visible:outline-brand-logo"
                >
                  <LogOut className="h-3.5 w-3.5" /> Sign out
                </button>
              </>
            ) : (
              <Link
                to="/login"
                className="rounded-full border border-white/15 px-4 py-1 text-xs font-semibold text-gray-100 hover:bg-white/8 hover:border-white/25 hover:text-white transition-all focus-visible:outline-2 focus-visible:outline-brand-logo"
              >
                Sign in
              </Link>
            )}
          </div>
        </div>
      </header>

      <main className="flex-1 mx-auto w-full max-w-7xl px-4 py-6">
        <Outlet />
      </main>

      <footer className="border-t border-gray-200 dark:border-gray-800 py-4 px-4">
        <div className="mx-auto max-w-7xl flex flex-col sm:flex-row items-center justify-between gap-3">
          <div className="flex items-center gap-3">
            <img
              src="/brand/ndicloud-wordmark-white.svg"
              alt="NDI Cloud"
              className="h-4 w-auto opacity-60 dark:opacity-80 invert dark:invert-0"
            />
            <span className="text-xs text-gray-500 dark:text-gray-400">
              NDI Data Browser · Waltham Data Science
            </span>
          </div>
          <span className="text-[11px] text-gray-400 dark:text-gray-500">
            &copy; {new Date().getFullYear()} Waltham Data Science
          </span>
        </div>
      </footer>
    </div>
  );
}

function NavItem({
  to,
  children,
}: {
  to: string;
  children: React.ReactNode;
}) {
  return (
    <NavLink
      to={to}
      className={({ isActive }) =>
        cn(
          'rounded-md px-3 py-1 text-sm font-medium text-white/80 hover:bg-white/5 hover:text-white transition-all',
          isActive && 'text-[#5DC1FF]',
        )
      }
    >
      {children}
    </NavLink>
  );
}
