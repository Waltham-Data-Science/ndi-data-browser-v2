import { Database, Info, LogOut, Search, User } from 'lucide-react';
import { Link, NavLink, Outlet } from 'react-router-dom';

import { useLogout, useMe } from '@/api/auth';
import { cn } from '@/lib/cn';

/**
 * App chrome — brand refresh landed in M7. Header carries the NDI Cloud
 * emblem; footer reads "Powered by NDICloud" per plan §M7 step 2.
 */
export function AppShell() {
  const me = useMe();
  const logout = useLogout();
  const signedIn = me.isSuccess;

  return (
    <div className="min-h-screen flex flex-col bg-slate-50 dark:bg-slate-950">
      <header className="bg-brand-navy text-white shadow-sm">
        <div className="mx-auto flex max-w-7xl items-center gap-6 px-4 py-3">
          <Link to="/" className="flex items-center gap-2 font-semibold" aria-label="NDI Data Browser home">
            <img
              src="/brand/ndicloud-emblem.svg"
              alt=""
              aria-hidden="true"
              className="h-6 w-auto"
            />
            <span className="hidden sm:inline">NDICloud Data Browser</span>
          </Link>
          <nav className="flex items-center gap-1 text-sm" aria-label="Primary">
            <NavItem to="/datasets" icon={<Database className="h-4 w-4" />}>
              Datasets
            </NavItem>
            <NavItem to="/query" icon={<Search className="h-4 w-4" />}>
              Query
            </NavItem>
            {signedIn && (
              <NavItem to="/my" icon={<User className="h-4 w-4" />}>
                My org
              </NavItem>
            )}
            <NavItem to="/about" icon={<Info className="h-4 w-4" />}>
              About
            </NavItem>
          </nav>
          <div className="ml-auto flex items-center gap-2 text-sm">
            {signedIn ? (
              <>
                <span className="hidden sm:inline text-slate-300" data-testid="me">
                  Signed in
                </span>
                <button
                  type="button"
                  onClick={() => logout.mutate()}
                  className="flex items-center gap-1 rounded px-2 py-1 text-slate-200 hover:bg-white/10 focus-visible:outline-2 focus-visible:outline-brand-logo"
                >
                  <LogOut className="h-4 w-4" /> Sign out
                </button>
              </>
            ) : (
              <Link
                to="/login"
                className="rounded bg-white/10 px-3 py-1 text-slate-100 hover:bg-white/20 focus-visible:outline-2 focus-visible:outline-brand-logo"
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

      <footer className="border-t border-slate-200 dark:border-slate-800 py-4 px-4 text-center text-xs text-slate-500 dark:text-slate-400">
        <div className="mx-auto max-w-7xl flex flex-col sm:flex-row items-center justify-center sm:justify-between gap-2">
          <span className="flex items-center gap-2">
            <img
              src="/brand/ndicloud-logo.svg"
              alt="NDICloud"
              className="h-4 w-auto opacity-80"
            />
            <span>Powered by NDICloud</span>
          </span>
          <span className="text-[11px]">
            NDI Data Browser v2 · Waltham Data Science
          </span>
        </div>
      </footer>
    </div>
  );
}

function NavItem({
  to,
  icon,
  children,
}: {
  to: string;
  icon: React.ReactNode;
  children: React.ReactNode;
}) {
  return (
    <NavLink
      to={to}
      className={({ isActive }) =>
        cn(
          'flex items-center gap-1.5 rounded px-2 py-1 text-slate-200 hover:bg-white/10',
          isActive && 'bg-white/15 text-white',
        )
      }
    >
      {icon}
      {children}
    </NavLink>
  );
}
