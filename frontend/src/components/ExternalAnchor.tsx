import { ExternalLink } from 'lucide-react';

import { cn } from '@/lib/cn';
import { safeHref } from '@/lib/safe-href';

/**
 * ExternalAnchor renders a safe outbound link.
 *
 * React 19 does not block javascript:/data:/vbscript: URLs at render time —
 * it only warns in development. A malicious cloud dataset can ship a field
 * like `doi: "javascript:alert(document.cookie)"` that would otherwise
 * execute when a user clicks the link. Because our CSRF cookie is
 * non-HttpOnly by design (double-submit pattern), a successful click
 * leaks both the session marker and the CSRF token.
 *
 * Every href is routed through {@link safeHref}. If the href is missing
 * or uses a non-navigational scheme, the component renders the label as
 * plain text so the user still sees the information but clicking does
 * nothing. The public prop interface is unchanged from prior in-page use.
 */
export function ExternalAnchor({
  href,
  label,
  className,
  iconSize = 12,
}: {
  href: string;
  label: string;
  className?: string;
  iconSize?: number;
}) {
  // Truncation strategy: the label span uses `truncate max-w-full min-w-0`
  // so it shrinks to whatever width the flex/grid parent allows, then
  // shows `…` when the text exceeds that. `min-w-0` on the outer <a>/<span>
  // is necessary because `inline-flex` children default to min-content
  // min-width and would otherwise push their parent wider than intended
  // (Steve 2026-04-20: long DOIs + paper titles escaped the 340 px
  // sidebar card). Parents that host `ExternalAnchor` in a CSS Grid
  // need `minmax(0, 1fr)` on the value track — see
  // `DatasetDetailPage.tsx` + `DatasetSummaryCard.tsx`.
  const safe = safeHref(href);
  if (!safe) {
    return (
      <span
        className={cn(
          'inline-flex min-w-0 max-w-full items-center gap-0.5 overflow-hidden text-gray-700 dark:text-gray-300',
          className,
        )}
      >
        <span className="truncate min-w-0 max-w-full">{label}</span>
      </span>
    );
  }
  return (
    <a
      href={safe}
      target="_blank"
      rel="noopener noreferrer"
      className={cn(
        'inline-flex min-w-0 max-w-full items-center gap-0.5 overflow-hidden text-brand-600 dark:text-brand-400 hover:underline',
        className,
      )}
    >
      <span className="truncate min-w-0 max-w-full">{label}</span>
      <ExternalLink className="shrink-0" style={{ width: iconSize, height: iconSize }} />
    </a>
  );
}
