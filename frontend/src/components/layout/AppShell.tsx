import { Link, NavLink, Outlet } from 'react-router-dom';
import { Database, Search, User, LogOut } from 'lucide-react';
import { useMe, useLogout } from '@/api/auth';
import { cn } from '@/lib/cn';

export function AppShell() {
  const me = useMe();
  const logout = useLogout();
  const signedIn = me.isSuccess;

  return (
    <div className="min-h-screen flex flex-col">
      <header className="bg-brand-navy text-white">
        <div className="mx-auto flex max-w-7xl items-center gap-6 px-4 py-3">
          <Link to="/" className="flex items-center gap-2 font-semibold">
            <Database className="h-5 w-5 text-brand-logo" />
            NDI Data Browser
          </Link>
          <nav className="flex items-center gap-1 text-sm" aria-label="Primary">
            <NavItem to="/datasets" icon={<Database className="h-4 w-4" />}>Datasets</NavItem>
            <NavItem to="/query" icon={<Search className="h-4 w-4" />}>Query</NavItem>
            {signedIn && <NavItem to="/my" icon={<User className="h-4 w-4" />}>My org</NavItem>}
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

      <footer className="border-t border-slate-200 dark:border-slate-800 py-4 text-center text-xs text-slate-500">
        NDI Data Browser v2 &mdash; Waltham Data Science
      </footer>
    </div>
  );
}

function NavItem({ to, icon, children }: { to: string; icon: React.ReactNode; children: React.ReactNode }) {
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
