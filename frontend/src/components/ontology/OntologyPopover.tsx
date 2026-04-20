import { useCallback, useEffect, useRef, useState } from 'react';
import { createPortal } from 'react-dom';
import { Link } from 'react-router-dom';
import { useOntologyLookup } from '@/api/ontology';
import { Skeleton } from '@/components/ui/Skeleton';
import { normalizeOntologyTerm } from './ontology-utils';

/**
 * Grace period (ms) between `mouseleave` on the trigger/popover and the
 * popover actually closing. Forgives the cursor transiting the 4px gap
 * between the underlined term and the floating panel without feeling
 * sluggish. Post-Steve feedback 2026-04-18.
 */
const POPOVER_CLOSE_DELAY_MS = 150;

/**
 * Popover width in px. Must match the Tailwind `w-72` on the floating
 * panel below; the JS side uses it for horizontal viewport clamping so
 * a popover near the right edge of the screen doesn't poke off-screen.
 */
const POPOVER_WIDTH_PX = 288;

/** px gap between trigger edge and popover edge. Purely visual. */
const POPOVER_OFFSET_PX = 4;

/** Min margin from viewport edges. */
const VIEWPORT_MARGIN_PX = 8;

interface OntologyPopoverProps {
  termId: string;
  /** Optional — when set, "Find everywhere" link in the popover points
   * at the query page preloaded with this term as a `contains_string`
   * clause. Wired up in M6. */
  findEverywherePath?: string;
}

type Placement = 'above' | 'below';

interface PopoverCoords {
  /** Viewport-relative top edge (for `position: fixed; top`). */
  top: number;
  /** Viewport-relative left edge (for `position: fixed; left`). */
  left: number;
  /** Whether the popover sits above or below its trigger. Affects the
   * transform (no transform for `below`; `translateY(-100%)` for
   * `above` so the popover's bottom sits at `top`). */
  placement: Placement;
}

/**
 * Interactive ontology term chip. Hover/focus opens a floating popover
 * with the term's human-readable label + definition, fetched via
 * `useOntologyLookup` (which hits the Redis-cached ontology lookup).
 *
 * ## Why the popover is portaled out to document.body
 *
 * Historically the popover was rendered as an absolutely-positioned
 * child of the trigger `<span>`. That broke when the trigger sits in a
 * scrolling ancestor — the table's `overflow-auto max-h-[600px]`
 * wrapper (see `SummaryTableView.tsx`) clips any absolute descendant
 * that spills above the scroll container's top edge. For any row near
 * the top of the table, the popover (which opens above) disappeared
 * behind the scroll container.
 *
 * Fix (post-Steve feedback 2026-04-19): render the popover into
 * `document.body` via `createPortal` with `position: fixed`. Placement
 * is computed from the trigger's `getBoundingClientRect()`, and the
 * popover auto-flips to below if there isn't enough room above.
 *
 * ## Hover semantics
 *
 * Because the popover is no longer a DOM descendant of the trigger,
 * moving the cursor from trigger to popover fires `mouseleave` on the
 * trigger span. We compensate with a shared close-delay timer (150ms)
 * and hover listeners on BOTH the trigger and the portaled popover —
 * `mouseenter` on either cancels any pending close.
 *
 * Ported from v1 with three adjustments:
 * - Backend term shape is `{provider, termId, label, definition, url}` —
 *   no `synonyms` field, no `found` boolean (label==null means not found).
 * - `isEmptyTerm` rendering path preserved: `EMPTY:` prefix means NDI's
 *   internal placeholder; render as monospace text, don't hit lookup.
 * - Unprefixed bare NCBI ids (Van Hooser) are normalized before lookup
 *   so the popover still resolves.
 */
export function OntologyPopover({ termId, findEverywherePath }: OntologyPopoverProps) {
  const [isOpen, setIsOpen] = useState(false);
  const [coords, setCoords] = useState<PopoverCoords | null>(null);
  const triggerRef = useRef<HTMLSpanElement>(null);
  const popoverRef = useRef<HTMLDivElement>(null);
  const closeTimeoutRef = useRef<number | null>(null);

  const displayId = termId.trim();
  const isEmptyTerm = displayId.startsWith('EMPTY:');

  const lookupTerm = isOpen && !isEmptyTerm ? displayId : '';
  const { data, isLoading } = useOntologyLookup(lookupTerm);
  const normalized = normalizeOntologyTerm(displayId) ?? displayId;

  /**
   * Compute popover position from the trigger's viewport rect.
   *
   * Called from mouse/focus event handlers (to set the initial position
   * when opening) and from the scroll/resize subscription while the
   * popover is visible. Never called from an effect body — that would
   * be a `setState`-in-effect anti-pattern.
   *
   * The popover's actual height is measured if already mounted (re-flow
   * case); otherwise we use a 180px estimate that exceeds the common
   * definition-text height, so the placement decision doesn't
   * mistakenly place an oversized popover above a top-of-viewport
   * trigger. Placement is self-aligning: `placement: 'above'` uses
   * `transform: translateY(-100%)` so the popover's bottom sits at
   * `top`, regardless of its actual height.
   */
  const computeCoords = useCallback((): PopoverCoords | null => {
    const trigger = triggerRef.current;
    if (!trigger) return null;
    const rect = trigger.getBoundingClientRect();
    const popoverEl = popoverRef.current;
    const popoverHeight = popoverEl?.offsetHeight ?? 180;

    const spaceAbove = rect.top - VIEWPORT_MARGIN_PX;
    const spaceBelow = window.innerHeight - rect.bottom - VIEWPORT_MARGIN_PX;
    const placement: Placement =
      spaceAbove >= popoverHeight + POPOVER_OFFSET_PX || spaceAbove >= spaceBelow
        ? 'above'
        : 'below';

    const top =
      placement === 'above'
        ? rect.top - POPOVER_OFFSET_PX
        : rect.bottom + POPOVER_OFFSET_PX;

    const maxLeft = window.innerWidth - POPOVER_WIDTH_PX - VIEWPORT_MARGIN_PX;
    const left = Math.max(VIEWPORT_MARGIN_PX, Math.min(rect.left, maxLeft));

    return { top, left, placement };
  }, []);

  const openNow = useCallback(() => {
    if (closeTimeoutRef.current !== null) {
      window.clearTimeout(closeTimeoutRef.current);
      closeTimeoutRef.current = null;
    }
    // Compute coords synchronously in the same event tick we open in.
    // This avoids the `react-hooks/set-state-in-effect` anti-pattern
    // (computing coords reactively in an effect that runs after isOpen
    // flips) and also eliminates a render where the popover is open
    // but has no coords yet — first frame is placed correctly.
    const next = computeCoords();
    if (next) setCoords(next);
    setIsOpen(true);
  }, [computeCoords]);

  const closeSoon = useCallback(() => {
    if (closeTimeoutRef.current !== null) {
      window.clearTimeout(closeTimeoutRef.current);
    }
    closeTimeoutRef.current = window.setTimeout(() => {
      closeTimeoutRef.current = null;
      setIsOpen(false);
    }, POPOVER_CLOSE_DELAY_MS);
  }, []);

  // Keep the popover anchored to the trigger as the user scrolls (e.g.
  // scrolling the table's virtualized body) or resizes the window. The
  // `setCoords` call lives inside the scroll/resize event callback —
  // this is the "subscribe for updates from an external system" pattern
  // that `react-hooks/set-state-in-effect` explicitly allows. Capture
  // phase catches nested scrollers too, essential for the table case
  // that motivated the portal fix.
  useEffect(() => {
    if (!isOpen) return;
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
  }, [isOpen, computeCoords]);

  // Cleanup on unmount so a pending timer doesn't call setState on a
  // gone component.
  useEffect(() => {
    return () => {
      if (closeTimeoutRef.current !== null) {
        window.clearTimeout(closeTimeoutRef.current);
        closeTimeoutRef.current = null;
      }
    };
  }, []);

  // EMPTY: placeholders — NDI's internal vocabulary, no external lookup.
  if (isEmptyTerm) {
    const id = displayId.replace('EMPTY:', '');
    return (
      <span
        className="font-mono text-xs text-gray-500 dark:text-gray-400"
        title="NDI internal identifier (no ontology mapping)"
        data-ontology-term={displayId}
      >
        {id}
      </span>
    );
  }

  const hasDefinition = !!data && !!data.label;

  return (
    <>
      <span
        ref={triggerRef}
        className="relative inline-block"
        data-ontology-term={displayId}
        onMouseEnter={openNow}
        onMouseLeave={closeSoon}
      >
        <button
          type="button"
          className="text-brand-600 hover:text-brand-700 dark:text-brand-400 dark:hover:text-brand-300 underline decoration-dotted cursor-help font-mono text-xs"
          onFocus={openNow}
          onBlur={closeSoon}
          onClick={(e) => {
            // Keep the popover open on click and prevent the enclosing row's
            // onRowClick from navigating to the document detail page.
            e.stopPropagation();
            openNow();
          }}
          aria-expanded={isOpen}
          aria-label={`Ontology term ${displayId}. Click for definition.`}
        >
          {displayId}
        </button>
      </span>
      {isOpen &&
        coords &&
        createPortal(
          <div
            ref={popoverRef}
            role="tooltip"
            data-ontology-popover={displayId}
            data-placement={coords.placement}
            style={{
              position: 'fixed',
              top: coords.top,
              left: coords.left,
              width: POPOVER_WIDTH_PX,
              transform:
                coords.placement === 'above' ? 'translateY(-100%)' : undefined,
            }}
            className="z-50 rounded-md border border-gray-200 bg-white p-3 shadow-lg text-xs dark:border-gray-700 dark:bg-gray-900"
            onMouseEnter={openNow}
            onMouseLeave={closeSoon}
          >
            {isLoading ? (
              <div className="space-y-1.5">
                <Skeleton className="h-4 w-32" />
                <Skeleton className="h-3 w-full" />
              </div>
            ) : hasDefinition ? (
              <div className="space-y-1.5">
                <div className="font-medium text-gray-900 dark:text-gray-100">
                  {data!.label}
                </div>
                <div className="font-mono text-[10px] text-gray-500 dark:text-gray-400">
                  {`${data!.provider}:${data!.termId}`}
                </div>
                {data!.definition && (
                  <p className="text-gray-600 dark:text-gray-400 leading-relaxed">
                    {data!.definition}
                  </p>
                )}
                {data!.url && (
                  <a
                    href={data!.url}
                    target="_blank"
                    rel="noopener noreferrer"
                    className="text-brand-600 dark:text-brand-400 underline decoration-dotted"
                  >
                    View on provider →
                  </a>
                )}
                {findEverywherePath && (
                  <Link
                    to={findEverywherePath}
                    className="block text-brand-600 dark:text-brand-400 underline decoration-dotted pt-1"
                  >
                    Find everywhere →
                  </Link>
                )}
              </div>
            ) : (
              <div className="text-gray-500 dark:text-gray-400">
                No definition found for{' '}
                <span className="font-mono">{normalized}</span>
              </div>
            )}
          </div>,
          document.body,
        )}
    </>
  );
}
