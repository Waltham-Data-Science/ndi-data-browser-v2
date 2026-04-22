import { useState } from 'react';
import { useNavigate, useSearchParams } from 'react-router-dom';
import { useLogin } from '@/api/auth';
import { Input } from '@/components/ui/Input';
import { Button } from '@/components/ui/Button';
import { ApiError } from '@/api/errors';
import { marketingHref } from '@/lib/config';

/**
 * Login — split-auth layout matching the marketing site.
 *
 *   Left  (marketing side): depth-gradient background with brandmark pattern
 *                           overlay, eyebrow, display heading with em accent,
 *                           subtitle, and a feature bullet list.
 *   Right (form side):      white background, centered max-w-md form.
 *
 * Auth flows (createAccount, forgotPassword) live on the marketing domain,
 * so those links route out via `marketingHref(...)`.
 */
export function LoginPage() {
  const login = useLogin();
  const navigate = useNavigate();
  const [params] = useSearchParams();
  const returnTo = sanitizeReturnTo(params.get('returnTo'));
  const [username, setUsername] = useState('');
  const [password, setPassword] = useState('');

  return (
    <div className="grid grid-cols-1 md:grid-cols-2 min-h-[calc(100vh-51px)]">
      {/* ── Marketing side (left) ─────────────────────────────────────── */}
      <aside
        className="relative overflow-hidden text-white flex flex-col justify-center px-8 py-16 md:px-14 md:py-20"
        style={{ background: 'var(--grad-depth)' }}
        aria-label="Welcome to NDI Cloud"
      >
        {/* Pattern overlay — NDI brandmark at 5% opacity */}
        <div
          aria-hidden
          className="absolute inset-0 pointer-events-none"
          style={{
            backgroundImage: "url('/brand/ndicloud-emblem.svg')",
            backgroundSize: '120px',
            backgroundRepeat: 'repeat',
            opacity: 0.05,
          }}
        />
        {/* Soft blue glow accent, bottom-right */}
        <div
          aria-hidden
          className="absolute pointer-events-none rounded-full"
          style={{
            width: 500,
            height: 500,
            background:
              'radial-gradient(circle, rgba(23,167,255,0.15) 0%, transparent 70%)',
            bottom: -200,
            right: -150,
          }}
        />

        <div className="relative z-10 max-w-[480px] mx-auto w-full">
          <div className="eyebrow mb-5">
            <span className="eyebrow-dot" aria-hidden />
            Welcome back
          </div>

          <h2 className="font-display text-white text-[2rem] md:text-[2.5rem] font-extrabold leading-[1.08] tracking-tight mb-4 text-balance">
            Your lab&apos;s data,{' '}
            <em className="not-italic text-brand-blue">
              right where you left it.
            </em>
          </h2>

          <p className="text-white/75 text-[1.05rem] leading-relaxed mb-8 max-w-[440px]">
            Sign in to browse your organization&apos;s datasets, query across
            species, region, probe, and subject, and save bookmarks with
            persistent DOIs.
          </p>

          <ul className="list-none p-0 m-0 flex flex-col gap-3">
            {FEATURES.map((feature) => (
              <li
                key={feature}
                className="flex items-start gap-3 text-[0.92rem] leading-normal text-white/85"
              >
                <svg
                  width="18"
                  height="18"
                  viewBox="0 0 18 18"
                  fill="none"
                  stroke="currentColor"
                  strokeWidth="2.2"
                  strokeLinecap="round"
                  strokeLinejoin="round"
                  className="flex-shrink-0 mt-[3px] text-ndi-teal"
                  aria-hidden
                >
                  <polyline points="4 9 8 13 14 5" />
                </svg>
                <span>{feature}</span>
              </li>
            ))}
          </ul>
        </div>
      </aside>

      {/* ── Form side (right) ─────────────────────────────────────────── */}
      <main className="flex flex-col items-center justify-center bg-white px-6 py-12 md:px-8 md:py-16">
        <div className="w-full max-w-md">
          <h1 className="font-display text-brand-navy text-[1.85rem] font-extrabold tracking-tight mb-2">
            Log in
          </h1>
          <p className="text-fg-secondary text-[0.95rem] mb-7">
            Sign in to your lab&apos;s workspace.
          </p>

          <form
            className="flex flex-col gap-4"
            onSubmit={(e) => {
              e.preventDefault();
              login.mutate(
                { username, password },
                { onSuccess: () => navigate(returnTo) },
              );
            }}
          >
            <div>
              <label
                htmlFor="login-email"
                className="block text-sm font-medium text-brand-navy mb-1.5"
              >
                Email
              </label>
              <Input
                id="login-email"
                type="email"
                value={username}
                onChange={(e) => setUsername(e.target.value)}
                required
                autoComplete="email"
                placeholder="you@lab.edu"
              />
            </div>

            <div>
              <label
                htmlFor="login-password"
                className="block text-sm font-medium text-brand-navy mb-1.5"
              >
                Password
              </label>
              <Input
                id="login-password"
                type="password"
                value={password}
                onChange={(e) => setPassword(e.target.value)}
                required
                autoComplete="current-password"
              />
            </div>

            {login.isError && login.error instanceof ApiError && (
              <p role="alert" className="text-sm text-red-600">
                {login.error.message}
              </p>
            )}

            <Button
              type="submit"
              disabled={login.isPending}
              className="w-full justify-center bg-ndi-teal text-white hover:brightness-110 py-2.5 text-[14.5px] font-semibold"
              style={{ boxShadow: 'var(--shadow-cta)' }}
            >
              {login.isPending ? 'Signing in…' : 'Log in'}
            </Button>
          </form>

          <div className="mt-6 pt-5 border-t border-border-subtle flex flex-col gap-2 text-sm text-center">
            <a
              href={marketingHref('/forgotPassword')}
              className="text-ndi-teal hover:brightness-110"
            >
              Forgot password?
            </a>
            <span className="text-fg-muted">
              No account?{' '}
              <a
                href={marketingHref('/createAccount')}
                className="text-ndi-teal hover:brightness-110 font-medium"
              >
                Create one on NDI Cloud
              </a>
            </span>
          </div>
        </div>
      </main>
    </div>
  );
}

/** Feature bullets shown on the left (marketing) side. */
const FEATURES = [
  'Browse published datasets without an account — required only for your own workspace',
  'Query across species, region, probe, and subject',
  'Bookmark and cite with persistent DOIs',
];

/**
 * Accept only same-origin paths for the `?returnTo=` param to prevent
 * open-redirect abuse. Everything else falls back to `/datasets`.
 */
function sanitizeReturnTo(v: string | null): string {
  if (!v || !v.startsWith('/') || v.startsWith('//')) return '/datasets';
  return v;
}
