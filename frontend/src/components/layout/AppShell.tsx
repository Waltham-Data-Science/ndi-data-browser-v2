import { ExternalLink, LogOut, Menu as MenuIcon, X } from 'lucide-react';
import { useState } from 'react';
import { Link, NavLink, Outlet, useLocation } from 'react-router-dom';

import { useLogout, useMe } from '@/api/auth';
import { cn } from '@/lib/cn';
import { DOCS_URL, marketingHref } from '@/lib/config';
import { useDocumentTitle } from '@/lib/useDocumentTitle';

/**
 * AppShell — app chrome for the NDI Data Browser.
 *
 * Visually aligned with ndi-web-app-wds's NDIHeader/NDIFooter so the marketing
 * site (ndi-cloud.com) and the data browser (app.ndi-cloud.com) feel like one
 * product:
 *   • Dark glassmorphic sticky top nav
 *   • Geist horizontal wordmark, inverted to white
 *   • Mirror of marketing nav links (Data Commons active here; others link
 *     cross-domain to ndi-cloud.com)
 *   • App-local nav items (Query, My Workspace) surface only when relevant
 *   • Pill "Log in" + teal "Create Free Account" CTAs (cross-domain — auth
 *     lives on the marketing site for now; may SSO in a later phase)
 *   • Four-column dark footer matching NDIFooter
 */
export function AppShell() {
  const me = useMe();
  const logout = useLogout();
  const signedIn = me.isSuccess;
  const [mobileOpen, setMobileOpen] = useState(false);

  useDocumentTitle();

  const closeMobile = () => setMobileOpen(false);

  return (
    <div className="min-h-screen flex flex-col bg-bg-muted">
      {/* ── Top nav ─────────────────────────────────────────────────── */}
      <header
        className="sticky top-0 z-50 text-white backdrop-blur-xl border-b border-white/5"
        style={{ background: 'rgba(0, 0, 0, 0.92)', boxShadow: 'var(--shadow-nav)' }}
      >
        <div className="mx-auto flex max-w-[1200px] items-center gap-6 px-7 py-3.5">
          {/* Logo */}
          <Link
            to="/datasets"
            className="flex items-center transition-opacity hover:opacity-80"
            aria-label="NDI Cloud — Data Commons home"
            onClick={closeMobile}
          >
            <img
              src="/brand/ndicloud-wordmark-horizontal.svg"
              alt="NDI Cloud"
              width={121}
              height={22}
              className="h-[22px] w-auto"
              style={{ filter: 'brightness(0) invert(1)' }}
            />
          </Link>

          {/* Desktop nav */}
          <nav
            className="hidden md:flex items-center gap-1 text-[13.5px] flex-1"
            aria-label="Primary"
          >
            {/* Local — the data browser IS the Data Commons */}
            <AppNavLink to="/datasets" match={['/', '/datasets']}>
              Data Commons
            </AppNavLink>
            {signedIn && (
              <>
                <AppNavLink to="/query">Query</AppNavLink>
                <AppNavLink to="/my">My Workspace</AppNavLink>
              </>
            )}

            {/* Separator */}
            <span className="mx-2 h-4 w-px bg-white/10" aria-hidden />

            {/* Cross-domain — to marketing site.
                Note: we intentionally skip the "Data Browser" / "For Labs"
                pitch page in the app's own nav — if you're here, you're
                already in the product. The marketing nav surfaces it. */}
            <ExtNavLink href={marketingHref('/products/labchat')}>
              LabChat
            </ExtNavLink>
            <ExtNavLink href={marketingHref('/platform')}>
              Platform
            </ExtNavLink>
            <ExtNavLink href={marketingHref('/about')}>
              About
            </ExtNavLink>
            <ExtNavLink href={DOCS_URL} showIcon>
              Docs
            </ExtNavLink>
          </nav>

          {/* Right side CTAs */}
          <div className="ml-auto md:ml-0 flex items-center gap-2">
            {signedIn ? (
              <div className="hidden md:flex items-center gap-2">
                <span
                  className="text-xs text-white/55"
                  data-testid="me"
                  title={me.data?.email_hash ?? ''}
                >
                  Signed in
                </span>
                <button
                  type="button"
                  onClick={() => logout.mutate()}
                  className={btnGhostDark}
                  aria-label="Sign out"
                >
                  <LogOut className="h-3.5 w-3.5" />
                  <span>Sign out</span>
                </button>
              </div>
            ) : (
              <div className="hidden md:flex items-center gap-2">
                <Link to="/login" className={btnGhostDark}>
                  Log in
                </Link>
                <a
                  href={marketingHref('/createAccount')}
                  className={btnCreateAccount}
                  style={{ boxShadow: 'var(--shadow-cta)' }}
                >
                  Create Free Account
                </a>
              </div>
            )}

            {/* Mobile hamburger */}
            <button
              type="button"
              onClick={() => setMobileOpen((v) => !v)}
              className="md:hidden inline-flex items-center justify-center rounded-full p-2 text-white/80 hover:bg-white/8 hover:text-white"
              aria-label={mobileOpen ? 'Close menu' : 'Open menu'}
              aria-expanded={mobileOpen}
            >
              {mobileOpen ? <X className="h-5 w-5" /> : <MenuIcon className="h-5 w-5" />}
            </button>
          </div>
        </div>

        {/* Mobile panel */}
        {mobileOpen && (
          <div
            className="md:hidden border-t border-white/5 px-6 py-4"
            style={{ background: 'rgba(0, 0, 0, 0.96)' }}
          >
            <ul className="flex flex-col gap-1 text-[14px]">
              <MobileNavItem to="/datasets" onClick={closeMobile}>
                Data Commons
              </MobileNavItem>
              {signedIn && (
                <>
                  <MobileNavItem to="/query" onClick={closeMobile}>
                    Query
                  </MobileNavItem>
                  <MobileNavItem to="/my" onClick={closeMobile}>
                    My Workspace
                  </MobileNavItem>
                </>
              )}
              <li className="my-2 h-px bg-white/10" aria-hidden />
              <MobileExtItem href={marketingHref('/products/labchat')}>
                LabChat
              </MobileExtItem>
              <MobileExtItem href={marketingHref('/platform')}>
                Platform
              </MobileExtItem>
              <MobileExtItem href={marketingHref('/about')}>
                About
              </MobileExtItem>
              <MobileExtItem href={DOCS_URL} showIcon>
                Docs
              </MobileExtItem>
              <li className="my-2 h-px bg-white/10" aria-hidden />
              {signedIn ? (
                <li>
                  <button
                    type="button"
                    onClick={() => {
                      closeMobile();
                      logout.mutate();
                    }}
                    className="flex w-full items-center gap-2 rounded-md px-3 py-2 text-left text-white/80 hover:bg-white/8"
                  >
                    <LogOut className="h-4 w-4" /> Sign out
                  </button>
                </li>
              ) : (
                <>
                  <li>
                    <Link
                      to="/login"
                      onClick={closeMobile}
                      className="block rounded-md px-3 py-2 text-white/80 hover:bg-white/8 hover:text-white"
                    >
                      Log in
                    </Link>
                  </li>
                  <li>
                    <a
                      href={marketingHref('/createAccount')}
                      className="mt-1 block rounded-md bg-ndi-teal px-3 py-2 text-center text-white font-semibold"
                    >
                      Create Free Account
                    </a>
                  </li>
                </>
              )}
            </ul>
          </div>
        )}
      </header>

      {/* ── Main outlet ─────────────────────────────────────────────── */}
      <main className="flex-1 w-full">
        <Outlet />
      </main>

      {/* ── Footer ──────────────────────────────────────────────────── */}
      <SiteFooter />
    </div>
  );
}

/* ─────────────────────────────────────────────────────────────────────
   Nav link primitives
   ───────────────────────────────────────────────────────────────────── */

function AppNavLink({
  to,
  match,
  children,
}: {
  to: string;
  match?: string[];
  children: React.ReactNode;
}) {
  const location = useLocation();
  const paths = match ?? [to];
  const isActive = paths.some(
    (p) => location.pathname === p || location.pathname.startsWith(p + '/'),
  );
  return (
    <NavLink
      to={to}
      className={cn(
        'rounded-md px-3 py-2 font-medium text-white/75 hover:text-white hover:bg-white/5 transition-colors',
        isActive && 'text-white bg-white/8',
      )}
    >
      {children}
    </NavLink>
  );
}

function ExtNavLink({
  href,
  children,
  showIcon,
}: {
  href: string;
  children: React.ReactNode;
  showIcon?: boolean;
}) {
  return (
    <a
      href={href}
      target="_blank"
      rel="noopener noreferrer"
      className="inline-flex items-center gap-1 rounded-md px-3 py-2 font-medium text-white/75 hover:text-white hover:bg-white/5 transition-colors"
    >
      {children}
      {showIcon && <ExternalLink className="h-3 w-3 opacity-60" />}
    </a>
  );
}

function MobileNavItem({
  to,
  children,
  onClick,
}: {
  to: string;
  children: React.ReactNode;
  onClick: () => void;
}) {
  return (
    <li>
      <NavLink
        to={to}
        onClick={onClick}
        className={({ isActive }) =>
          cn(
            'block rounded-md px-3 py-2 text-white/80 hover:bg-white/8 hover:text-white',
            isActive && 'text-white bg-white/8',
          )
        }
      >
        {children}
      </NavLink>
    </li>
  );
}

function MobileExtItem({
  href,
  children,
  showIcon,
}: {
  href: string;
  children: React.ReactNode;
  showIcon?: boolean;
}) {
  return (
    <li>
      <a
        href={href}
        target="_blank"
        rel="noopener noreferrer"
        className="flex items-center gap-1.5 rounded-md px-3 py-2 text-white/80 hover:bg-white/8 hover:text-white"
      >
        {children}
        {showIcon && <ExternalLink className="h-3 w-3 opacity-60" />}
      </a>
    </li>
  );
}

/* ─── Button class strings (kept inline for Tailwind JIT scanning) ─── */

const btnGhostDark =
  'inline-flex items-center gap-1.5 rounded-full border border-white/18 px-4 py-[6px] text-[13px] font-semibold text-white/90 hover:bg-white/8 hover:border-white/30 hover:text-white transition-all focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-brand-blue-3';

const btnCreateAccount =
  'inline-flex items-center rounded-full bg-ndi-teal px-4 py-[7px] text-[13px] font-semibold text-white transition-all hover:brightness-110 focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-brand-blue-3';
// note: box-shadow for CTA glow is applied via inline style since shadow-cta
// utility resolves to the shared token but JIT needs to see the class literal.

/* ─────────────────────────────────────────────────────────────────────
   SiteFooter — 4-column dark footer mirroring NDIFooter from marketing site
   ───────────────────────────────────────────────────────────────────── */

function SiteFooter() {
  return (
    <footer className="bg-black text-white mt-16">
      <div className="mx-auto max-w-[1200px] px-7 py-14">
        <div className="grid gap-12 md:grid-cols-[1.2fr_1fr_1fr_1fr]">
          {/* Brand column */}
          <div>
            <img
              src="/brand/ndicloud-wordmark-horizontal.svg"
              alt="NDI Cloud"
              className="h-6 w-auto mb-4"
              style={{ filter: 'brightness(0) invert(1)' }}
            />
            <p className="text-[13px] leading-relaxed text-white/60 max-w-xs">
              Quiet infrastructure for neuroscience data. Published datasets,
              private lab workspaces, and AI that knows your lab.
            </p>
          </div>

          {/* Products */}
          <FooterCol title="Products">
            <FooterLink href={marketingHref('/')}>NDI Cloud overview</FooterLink>
            <FooterLink href={marketingHref('/products/private-cloud')}>
              For Labs
            </FooterLink>
            <FooterLink href="/datasets" internal>
              Data Commons
            </FooterLink>
            <FooterLink href={marketingHref('/products/labchat')}>LabChat</FooterLink>
            <FooterLink href={marketingHref('/platform')}>How NDI works</FooterLink>
          </FooterCol>

          {/* Company */}
          <FooterCol title="Company">
            <FooterLink href={marketingHref('/about')}>About</FooterLink>
            <FooterLink href={marketingHref('/about#partners')}>Partners</FooterLink>
            <FooterLink href={marketingHref('/security')}>
              Security &amp; Compliance
            </FooterLink>
            <FooterLink
              href="https://github.com/VH-Lab"
              external
            >
              Research on GitHub
            </FooterLink>
          </FooterCol>

          {/* Get in touch */}
          <FooterCol title="Get in touch">
            <FooterLink href="mailto:info@walthamdatascience.com">
              info@walthamdatascience.com
            </FooterLink>
            <FooterLink href={DOCS_URL} external>
              Documentation
            </FooterLink>
          </FooterCol>
        </div>

        {/* Bottom bar */}
        <div className="mt-10 pt-6 border-t border-white/10 flex flex-col md:flex-row items-center justify-between gap-3 text-[12px] text-white/50">
          <span>
            &copy; {new Date().getFullYear()} Waltham Data Science &middot; NDI Cloud
          </span>
          <span className="flex items-center gap-4">
            <a href={marketingHref('/privacy')} className="hover:text-white/80">
              Privacy
            </a>
            <a href={marketingHref('/terms')} className="hover:text-white/80">
              Terms
            </a>
            <a href={marketingHref('/security')} className="hover:text-white/80">
              Security
            </a>
          </span>
        </div>
      </div>
    </footer>
  );
}

function FooterCol({
  title,
  children,
}: {
  title: string;
  children: React.ReactNode;
}) {
  return (
    <div>
      <h4 className="text-[11px] font-bold tracking-[0.14em] uppercase text-white/50 mb-4">
        {title}
      </h4>
      <ul className="flex flex-col gap-2.5 text-[13px]">{children}</ul>
    </div>
  );
}

function FooterLink({
  href,
  children,
  internal,
  external,
}: {
  href: string;
  children: React.ReactNode;
  internal?: boolean;
  external?: boolean;
}) {
  const cls =
    'text-white/70 hover:text-white transition-colors inline-flex items-center gap-1';
  if (internal) {
    return (
      <li>
        <Link to={href} className={cls}>
          {children}
        </Link>
      </li>
    );
  }
  return (
    <li>
      <a
        href={href}
        className={cls}
        {...(external
          ? { target: '_blank', rel: 'noopener noreferrer' }
          : {})}
      >
        {children}
        {external && <ExternalLink className="h-3 w-3 opacity-50" />}
      </a>
    </li>
  );
}
