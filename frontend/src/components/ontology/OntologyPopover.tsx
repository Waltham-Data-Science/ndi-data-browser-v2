import { useState } from 'react';
import { Link } from 'react-router-dom';
import { useOntologyLookup } from '@/api/ontology';
import { Skeleton } from '@/components/ui/Skeleton';
import { normalizeOntologyTerm } from './ontology-utils';

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
    <span className="relative inline-block" data-ontology-term={displayId}>
      <button
        type="button"
        className="text-brand-600 hover:text-brand-700 dark:text-brand-400 dark:hover:text-brand-300 underline decoration-dotted cursor-help font-mono text-xs"
        onMouseEnter={() => setIsOpen(true)}
        onMouseLeave={() => setIsOpen(false)}
        onFocus={() => setIsOpen(true)}
        onBlur={() => setIsOpen(false)}
        onClick={(e) => {
          // Keep the popover open on click and prevent the enclosing row's
          // onRowClick from navigating to the document detail page.
          e.stopPropagation();
          setIsOpen(true);
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
