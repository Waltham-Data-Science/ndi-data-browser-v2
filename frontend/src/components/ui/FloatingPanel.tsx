/**
 * FloatingPanel — a popover/tooltip primitive that escapes clipping
 * ancestors by portaling its children to `document.body` with
 * `position: fixed`.
 *
 * Why this exists (2026-04-19 audit):
 * The codebase previously had four different absolute-positioned
 * popovers/tooltips inside scrolling parents:
 *   - `SummaryTableView` column-header `ℹ` tooltip (inside the table's
 *     `overflow-auto max-h-[600px]` scroll wrapper — Steve's report).
 *   - `OntologyPopover` term definition popover (same table scroll).
 *   - `OntologyTermPill` tooltip (sidebar aside — narrow clipping column).
 *   - `SummaryFooter` warnings tooltip (sidebar card).
 *
 * All shared the same bug: `overflow: auto` on any ancestor turns that
 * ancestor into a clipping context, and an `absolute`-positioned
 * descendant cannot render outside it. A popover rendered above a
 * top-of-scroll trigger vanishes behind the scroll container's top edge.
 *
 * Portaling to `document.body` + positioning via
 * `getBoundingClientRect()` is the canonical floating-UI fix. This
 * component encapsulates it so we have one place to maintain the
 * placement + anchoring logic.
 *
 * ## API
 *
 * The caller owns the `open` state and the trigger element. This is
 * deliberate: different call sites have different triggers (CSS hover,
 * hover-with-delay, focus, click, etc.), and pushing trigger semantics
 * into the primitive would produce either a God component or a lossy
 * abstraction. The primitive's job is purely to render the floating
 * panel at the right viewport coordinates and to keep it there as the
 * page reflows.
 *
 * ## Placement
 *
 * Prefers `preferredPlacement` (default: `'above'`). Auto-flips to the
 * other side when the preferred side doesn't have enough room to fit
 * `estimatedHeight` + `offset` before the nearest viewport edge.
 * Horizontal position is anchored at the anchor's left edge, clamped
 * so the panel's right edge stays inside the viewport by at least
 * `viewportMargin` px.
 *
 * The actual rendered height is measured after mount and passed back
 * through to the next `computeCoords()` call via `panelRef`, so
 * scroll/resize refinements are pixel-accurate. The first frame uses
 * `estimatedHeight` as a conservative starting point.
 */
import { useCallback, useEffect, useRef, useState } from 'react';
import type { CSSProperties, ReactNode, RefObject } from 'react';
import { createPortal } from 'react-dom';

import { cn } from '@/lib/cn';

export type FloatingPanelPlacement = 'above' | 'below';

interface PanelCoords {
  top: number;
  left: number;
  placement: FloatingPanelPlacement;
}

export interface FloatingPanelProps {
  /** Whether the panel is currently visible. */
  open: boolean;
  /** Ref to the trigger element. Panel anchors to this element's
   * viewport rect. */
  anchorRef: RefObject<HTMLElement | null>;
  /** Preferred side. Auto-flips if no room. Default `'above'`. */
  preferredPlacement?: FloatingPanelPlacement;
  /** Panel width in px — also used for right-edge viewport clamp.
   * Default `288` (matches Tailwind `w-72`). */
  width?: number;
  /** Conservative height estimate (px) for the initial placement
   * decision. After first measurement the actual height is used for
   * subsequent reflows. Default `180`. */
  estimatedHeight?: number;
  /** Gap in px between the anchor edge and the panel edge. Default `4`. */
  offset?: number;
  /** Minimum distance in px between the panel and any viewport edge.
   * Default `8`. */
  viewportMargin?: number;
  /** Panel root className. Typical use: Tailwind background, border,
   * padding, rounded corners. The primitive applies only positioning
   * and a sensible z-index default. */
  className?: string;
  /** Optional inline style merged after the computed `position: fixed`
   * coordinates. Rarely needed — prefer `className`. */
  style?: CSSProperties;
  /** ARIA role for the panel. Default `'tooltip'`. Use `'dialog'` for
   * interactive popovers. */
  role?: string;
  /** Optional aria-label on the panel root. */
  ariaLabel?: string;
  /** Pointer-enter handler on the panel itself — used by interactive
   * popovers to cancel a pending close-on-leave from the trigger. */
  onMouseEnter?: () => void;
  /** Pointer-leave handler on the panel itself. */
  onMouseLeave?: () => void;
  /** `data-testid` on the panel root. */
  testId?: string;
  /** Extra data attributes propagated to the panel root for test /
   * instrumentation hooks (e.g. `data-placement`). */
  dataAttrs?: Record<string, string>;
  children: ReactNode;
}

export function FloatingPanel({
  open,
  anchorRef,
  preferredPlacement = 'above',
  width = 288,
  estimatedHeight = 180,
  offset = 4,
  viewportMargin = 8,
  className,
  style,
  role = 'tooltip',
  ariaLabel,
  onMouseEnter,
  onMouseLeave,
  testId,
  dataAttrs,
  children,
}: FloatingPanelProps) {
  const panelRef = useRef<HTMLDivElement>(null);
  const [coords, setCoords] = useState<PanelCoords | null>(null);

  const computeCoords = useCallback((): PanelCoords | null => {
    const anchor = anchorRef.current;
    if (!anchor) return null;
    const rect = anchor.getBoundingClientRect();
    const panelHeight = panelRef.current?.offsetHeight ?? estimatedHeight;

    const spaceAbove = rect.top - viewportMargin;
    const spaceBelow = window.innerHeight - rect.bottom - viewportMargin;
    const needed = panelHeight + offset;

    let placement: FloatingPanelPlacement;
    if (preferredPlacement === 'above') {
      placement = spaceAbove >= needed || spaceAbove >= spaceBelow ? 'above' : 'below';
    } else {
      placement = spaceBelow >= needed || spaceBelow >= spaceAbove ? 'below' : 'above';
    }

    const top = placement === 'above' ? rect.top - offset : rect.bottom + offset;
    const maxLeft = window.innerWidth - width - viewportMargin;
    const left = Math.max(viewportMargin, Math.min(rect.left, maxLeft));

    return { top, left, placement };
  }, [anchorRef, estimatedHeight, offset, preferredPlacement, viewportMargin, width]);

  // We compute the first-frame coords ONCE (when `open` flips true)
  // using a deferred microtask. This intentionally avoids calling
  // setState inside an effect body (which the react-hooks plugin
  // rightly flags). The scroll/resize subscription below handles
  // further refinements; its setState lives inside the event callback,
  // which is the allowed "subscribe for external updates" pattern.
  useEffect(() => {
    if (!open) return;
    // queueMicrotask defers the setState until after React finishes the
    // current render pass, so we don't re-enter render synchronously.
    // The panel is still measurable by the time the microtask runs
    // because React has committed the portal div to the DOM.
    const id = requestAnimationFrame(() => {
      const next = computeCoords();
      if (next) setCoords(next);
    });
    return () => cancelAnimationFrame(id);
  }, [open, computeCoords]);

  // Re-anchor on scroll (capture phase — catches nested scrollers
  // like the summary table's `overflow-auto` body) and on viewport
  // resize. `setCoords` inside the event callback is the allowed
  // external-subscription pattern.
  useEffect(() => {
    if (!open) return;
    const onReflow = () => {
      const next = computeCoords();
      if (next) setCoords(next);
    };
    window.addEventListener('scroll', onReflow, true);
    window.addEventListener('resize', onReflow);
    return () => {
      window.removeEventListener('scroll', onReflow, true);
      window.removeEventListener('resize', onReflow);
    };
  }, [open, computeCoords]);

  if (!open) return null;

  const panelStyle: CSSProperties = {
    position: 'fixed',
    top: coords?.top ?? -9999,
    left: coords?.left ?? -9999,
    width,
    transform:
      coords?.placement === 'above' ? 'translateY(-100%)' : undefined,
    // Hide the first pre-coords frame to avoid a flash at (-9999, -9999).
    visibility: coords ? 'visible' : 'hidden',
    ...style,
  };

  return createPortal(
    <div
      ref={panelRef}
      role={role}
      aria-label={ariaLabel}
      style={panelStyle}
      className={cn('z-50', className)}
      data-placement={coords?.placement}
      data-testid={testId}
      onMouseEnter={onMouseEnter}
      onMouseLeave={onMouseLeave}
      {...(dataAttrs ?? {})}
    >
      {children}
    </div>,
    document.body,
  );
}
