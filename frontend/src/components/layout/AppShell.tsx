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
          {/* Logo — cross-domain to marketing home. Treated as "product
              entry point" the same way the marketing site's logo is:
              click = go back to ndi-cloud.com/. This matches the
              one-product-across-subdomains UX policy. */}
          <a
            href={marketingHref('/')}
            className="flex items-center transition-opacity hover:opacity-80"
            aria-label="NDI Cloud home"
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
          </a>

          {/* Desktop nav — mirrors the marketing site's NDIHeader link
              list exactly so the top bar is visually identical across
              ndi-cloud.com and app.ndi-cloud.com. Data Commons is the
              local route (lives in this SPA); the rest are cross-domain
              links to marketing product pages. Authenticated-only
              product routes (Query, My Workspace) sit behind a separator
              so the primary nav stays in marketing parity and the
              in-product shortcuts are visually subordinate. */}
          <nav
            className="hidden md:flex items-center gap-1 text-[13.5px] flex-1"
            aria-label="Primary"
          >
            <AppNavLink to="/datasets" match={['/', '/datasets']}>
              Data Commons
            </AppNavLink>
            <ExtNavLink href={marketingHref('/products/private-cloud')}>
              For Labs
            </ExtNavLink>
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

            {signedIn && (
              <>
                <span className="mx-2 h-4 w-px bg-white/10" aria-hidden />
                <AppNavLink to="/query">Query</AppNavLink>
                <AppNavLink to="/my">My Workspace</AppNavLink>
              </>
            )}
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
              {/* Mirror of desktop nav — exact marketing parity, then
                  authed shortcuts behind a divider. */}
              <MobileNavItem to="/datasets" onClick={closeMobile}>
                Data Commons
              </MobileNavItem>
              <MobileExtItem href={marketingHref('/products/private-cloud')}>
                For Labs
              </MobileExtItem>
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
              {signedIn && (
                <>
                  <li className="my-2 h-px bg-white/10" aria-hidden />
                  <MobileNavItem to="/query" onClick={closeMobile}>
                    Query
                  </MobileNavItem>
                  <MobileNavItem to="/my" onClick={closeMobile}>
                    My Workspace
                  </MobileNavItem>
                </>
              )}
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
  // Active state styled to match NDIHeader on the marketing site
  // (ndi-web-app/app/src/components/header/NDIHeader.module.scss
  // `.navLinkActive`): no background, just the brand-blue-3 text color
  // with full opacity. Keeps the two sides of the product
  // indistinguishable at the chrome level.
  return (
    <NavLink
      to={to}
      className={cn(
        'rounded-md px-3 py-2 font-medium text-white/85 hover:text-white hover:bg-white/5 transition-colors',
        isActive && '!text-brand-blue-3 opacity-100',
      )}
    >
      {children}
    </NavLink>
  );
}

/**
 * Cross-domain link.
 *
 * `showIcon=true` is the tell for "truly external reference" — Docs on
 * GitHub Pages, GitHub repos, LinkedIn. Those open in a new tab so
 * users keep the app open. Links WITHOUT `showIcon` (LabChat, Platform,
 * About) are our own marketing site — same product, same tab, no "this
 * will open in a new window" surprise.
 */
function ExtNavLink({
  href,
  children,
  showIcon,
}: {
  href: string;
  children: React.ReactNode;
  showIcon?: boolean;
}) {
  const external = showIcon === true;
  // Cross-domain links never look "active" here — the marketing site
  // owns those pages. Match the base opacity of the marketing nav
  // (0.85) so hover feels consistent.
  return (
    <a
      href={href}
      {...(external
        ? { target: '_blank', rel: 'noopener noreferrer' }
        : {})}
      className="inline-flex items-center gap-1 rounded-md px-3 py-2 font-medium text-white/85 hover:text-white hover:bg-white/5 transition-colors"
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
            // Mirror desktop active-style: blue text, no bg box.
            isActive && '!text-brand-blue-3',
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
  const external = showIcon === true;
  return (
    <li>
      <a
        href={href}
        {...(external
          ? { target: '_blank', rel: 'noopener noreferrer' }
          : {})}
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
          {/* Brand column — copy matches NDIFooter on the marketing
              site verbatim. Keep these in lockstep; if one side
              changes, change the other in the same commit. */}
          <div>
            <img
              src="/brand/ndicloud-wordmark-horizontal.svg"
              alt="NDI Cloud"
              className="h-[22px] w-auto mb-[14px]"
              style={{ filter: 'brightness(0) invert(1)' }}
            />
            <p className="text-[13px] leading-[1.5] text-white/50 max-w-[300px] m-0">
              Data infrastructure, publishing, and AI tools for modern
              neuroscience research.
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
            <FooterLink href={marketingHref('/about#partnerships')}>Partners</FooterLink>
            <FooterLink href={marketingHref('/security')}>
              Security &amp; Compliance
            </FooterLink>
            <FooterLink
              href="https://github.com/VH-Lab/NDI-matlab"
              external
            >
              Research on GitHub
            </FooterLink>
          </FooterCol>

          {/* Get in touch */}
          <FooterCol title="Get in touch">
            <FooterLink href="mailto:info@walthamdatascience.com?subject=NDI Cloud Inquiry">
              info@walthamdatascience.com
            </FooterLink>
            <FooterLink href={DOCS_URL} external>
              Documentation
            </FooterLink>
            <FooterLink href={marketingHref('/about#sfn')}>
              SfN 2025 &middot; San Diego
            </FooterLink>
          </FooterCol>
        </div>

        {/* Bottom bar */}
        <div className="mt-14 pt-5 border-t border-white/10 flex flex-col md:flex-row items-center justify-between gap-3 text-[12px] text-white/40">
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
  // Matches NDIFooter.module.scss `h5` spec exactly: 11px / 700 /
  // 0.12em tracking / uppercase / white / 16px bottom margin.
  return (
    <div>
      <h4 className="text-[11px] font-bold tracking-[0.12em] uppercase text-white mb-4">
        {title}
      </h4>
      <ul className="flex flex-col gap-2.5 text-[13.5px]">{children}</ul>
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
  // Color: rgba(255, 255, 255, 0.65) to match the SCSS directly.
  // Tailwind `/65` maps to that. Hover goes to pure white.
  const cls =
    'text-white/65 hover:text-white transition-colors inline-flex items-center gap-1';
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
