import { useEffect, useRef, useState } from 'react';
import { Link } from 'react-router-dom';
import { useOntologyLookup } from '@/api/ontology';
import { Skeleton } from '@/components/ui/Skeleton';
import { normalizeOntologyTerm } from './ontology-utils';

/**
 * Grace period (ms) between `mouseleave` on the trigger/popover envelope
 * and the popover closing. Users need to traverse the small gap between
 * the underlined term and the absolutely-positioned popover div; a short
 * delay forgives that transit without feeling sluggish.
 *
 * Post-Steve feedback 2026-04-18: "when I move my mouse to click on those
 * options it disappears as soon as my mouse leaves the table cell".
 */
const POPOVER_CLOSE_DELAY_MS = 150;

interface OntologyPopoverProps {
  termId: string;
  /** Optional — when set, "Find everywhere" link in the popover points
   * at the query page preloaded with this term as a `contains_string`
   * clause. Wired up in M6. */
  findEverywherePath?: string;
}

/**
 * Interactive ontology term chip. Click/hover opens a popover with the
 * term's human-readable label + definition, fetched via
 * `useOntologyLookup` (which hits the Redis-cached ontology lookup).
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
  const displayId = termId.trim();
  const isEmptyTerm = displayId.startsWith('EMPTY:');

  const lookupTerm = isOpen && !isEmptyTerm ? displayId : '';
  const { data, isLoading } = useOntologyLookup(lookupTerm);
  const normalized = normalizeOntologyTerm(displayId) ?? displayId;

  // Close-delay machinery. `closeTimeoutRef` holds the pending setTimeout
  // id; `onEnter` cancels it, `onLeave` schedules it. Listeners are on
  // the OUTER <span>: the button + popover are both DOM descendants, so
  // moving the cursor between them does not fire `mouseleave` on the
  // span unless the cursor actually leaves the button+popover envelope.
  // The small visual gap between button and popover (mb-1) is covered by
  // the close delay — the user has 150ms of grace to traverse it.
  const closeTimeoutRef = useRef<number | null>(null);

  const openNow = () => {
    if (closeTimeoutRef.current !== null) {
      window.clearTimeout(closeTimeoutRef.current);
      closeTimeoutRef.current = null;
    }
    setIsOpen(true);
  };
  const closeSoon = () => {
    if (closeTimeoutRef.current !== null) {
      window.clearTimeout(closeTimeoutRef.current);
    }
    closeTimeoutRef.current = window.setTimeout(() => {
      closeTimeoutRef.current = null;
      setIsOpen(false);
    }, POPOVER_CLOSE_DELAY_MS);
  };

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
        className="font-mono text-xs text-slate-500 dark:text-slate-400"
        title="NDI internal identifier (no ontology mapping)"
        data-ontology-term={displayId}
      >
        {id}
      </span>
    );
  }

  const hasDefinition = !!data && !!data.label;

  return (
    <span
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
      {isOpen && (
        <div
          role="tooltip"
          className="absolute z-50 bottom-full left-0 mb-1 w-72 rounded-md border border-slate-200 bg-white p-3 shadow-lg text-xs dark:border-slate-700 dark:bg-slate-900"
        >
          {isLoading ? (
            <div className="space-y-1.5">
              <Skeleton className="h-4 w-32" />
              <Skeleton className="h-3 w-full" />
            </div>
          ) : hasDefinition ? (
            <div className="space-y-1.5">
              <div className="font-medium text-slate-900 dark:text-slate-100">{data!.label}</div>
              <div className="font-mono text-[10px] text-slate-500 dark:text-slate-400">
                {`${data!.provider}:${data!.termId}`}
              </div>
              {data!.definition && (
                <p className="text-slate-600 dark:text-slate-400 leading-relaxed">
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
            <div className="text-slate-500 dark:text-slate-400">
              No definition found for{' '}
              <span className="font-mono">{normalized}</span>
            </div>
          )}
        </div>
      )}
    </span>
  );
}
