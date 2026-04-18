/**
 * CopyButton — copies a fixed string to the clipboard and flashes a
 * transient confirmation.
 *
 * Uses ``navigator.clipboard.writeText``. When the Clipboard API is
 * unavailable (insecure context, older browsers, sandboxed tests without
 * a jsdom polyfill) the button falls back to a selection-based
 * ``document.execCommand('copy')`` via a detached textarea. Same call
 * signature either way — consumers don't branch.
 */
import { useCallback, useEffect, useRef, useState } from 'react';
import { Check, Copy } from 'lucide-react';

import { cn } from '@/lib/cn';

export interface CopyButtonProps {
  /** The string copied when the button is clicked. */
  value: string;
  /** Human-readable label announced to screen readers. */
  ariaLabel?: string;
  className?: string;
  /** Visible label alongside the icon. Defaults to "Copy"/"Copied". */
  label?: string;
  /** Optional test id override; defaults to `copy-button`. */
  testId?: string;
}

export function CopyButton({
  value,
  ariaLabel,
  className,
  label,
  testId = 'copy-button',
}: CopyButtonProps) {
  const [copied, setCopied] = useState(false);
  const timerRef = useRef<number | null>(null);

  const copy = useCallback(async () => {
    try {
      if (navigator.clipboard?.writeText) {
        await navigator.clipboard.writeText(value);
      } else {
        const ta = document.createElement('textarea');
        ta.value = value;
        ta.style.position = 'fixed';
        ta.style.opacity = '0';
        document.body.appendChild(ta);
        ta.select();
        document.execCommand('copy');
        document.body.removeChild(ta);
      }
      setCopied(true);
      if (timerRef.current !== null) window.clearTimeout(timerRef.current);
      timerRef.current = window.setTimeout(() => setCopied(false), 1500);
    } catch {
      // Swallow — clipboard failures are non-critical and the user can
      // still select-and-copy the visible text. We intentionally do NOT
      // show an error state: the cite block itself is visible text.
    }
  }, [value]);

  useEffect(
    () => () => {
      if (timerRef.current !== null) window.clearTimeout(timerRef.current);
    },
    [],
  );

  return (
    <button
      type="button"
      onClick={copy}
      aria-label={ariaLabel ?? 'Copy to clipboard'}
      aria-live="polite"
      className={cn(
        'inline-flex items-center gap-1 rounded-md border border-slate-300 bg-white px-2 py-1 text-xs',
        'text-slate-700 transition-colors hover:bg-slate-50',
        'dark:border-slate-600 dark:bg-slate-800 dark:text-slate-200 dark:hover:bg-slate-700',
        'focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-brand-500',
        className,
      )}
      data-testid={testId}
      data-copied={copied ? 'true' : 'false'}
    >
      {copied ? (
        <Check className="h-3 w-3" aria-hidden />
      ) : (
        <Copy className="h-3 w-3" aria-hidden />
      )}
      <span>{copied ? 'Copied' : label ?? 'Copy'}</span>
    </button>
  );
}
