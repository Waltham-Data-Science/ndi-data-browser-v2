/**
 * Modal — accessible overlay dialog primitive.
 *
 * Ships with a backdrop that closes on click + Escape. Content renders
 * in-place (no portal) which is acceptable for our use: every caller
 * mounts the Modal at the top of its own subtree under the App shell.
 *
 * Intentional non-goals for this first iteration:
 *   - No focus trap (single-screen modals; `autoFocus` on the close
 *     button is the minimum accessibility bar we commit to here).
 *   - No animation (Tailwind transitions noise for a test-visible open
 *     state).
 *   - No portal. React 19 + our flat component tree means the Modal
 *     always mounts above the content stacking context already.
 */
import { useEffect, useRef, type PropsWithChildren } from 'react';
import { X } from 'lucide-react';

import { cn } from '@/lib/cn';

export interface ModalProps {
  open: boolean;
  onClose: () => void;
  title: string;
  description?: string;
  /** Tailwind max-width class for the dialog panel. Defaults to a
   *  comfortably readable size for cite-block content. */
  size?: 'sm' | 'md' | 'lg' | 'xl';
  className?: string;
}

const SIZE: Record<NonNullable<ModalProps['size']>, string> = {
  sm: 'max-w-md',
  md: 'max-w-xl',
  lg: 'max-w-2xl',
  xl: 'max-w-3xl',
};

export function Modal({
  open,
  onClose,
  title,
  description,
  size = 'lg',
  className,
  children,
}: PropsWithChildren<ModalProps>) {
  const closeRef = useRef<HTMLButtonElement | null>(null);

  useEffect(() => {
    if (!open) return;
    function onKey(e: KeyboardEvent) {
      if (e.key === 'Escape') onClose();
    }
    window.addEventListener('keydown', onKey);
    // Auto-focus the close button so keyboard users land somewhere
    // inside the modal on open. Close is the one control we can
    // guarantee every Modal has.
    closeRef.current?.focus();
    return () => window.removeEventListener('keydown', onKey);
  }, [open, onClose]);

  if (!open) return null;

  return (
    <div
      role="dialog"
      aria-modal="true"
      aria-label={title}
      className="fixed inset-0 z-50 flex items-start justify-center overflow-y-auto bg-slate-900/60 p-4 sm:p-6"
      onClick={(e) => {
        // Backdrop click closes, but only when the click landed on the
        // backdrop itself (not on a bubbled event from inside the panel).
        if (e.target === e.currentTarget) onClose();
      }}
      data-testid="modal-backdrop"
    >
      <div
        className={cn(
          'mt-10 w-full rounded-lg bg-white shadow-xl ring-1 ring-slate-200',
          'dark:bg-slate-900 dark:ring-slate-700',
          SIZE[size],
          className,
        )}
        onClick={(e) => e.stopPropagation()}
        data-testid="modal-panel"
      >
        <div className="flex items-start justify-between gap-4 border-b border-slate-200 px-5 py-3 dark:border-slate-700">
          <div className="min-w-0 flex-1">
            <h2
              className="text-base font-semibold leading-tight text-slate-900 dark:text-slate-100"
              data-testid="modal-title"
            >
              {title}
            </h2>
            {description && (
              <p
                className="mt-1 text-xs text-slate-500 dark:text-slate-400"
                data-testid="modal-description"
              >
                {description}
              </p>
            )}
          </div>
          <button
            ref={closeRef}
            type="button"
            className={cn(
              'inline-flex shrink-0 items-center justify-center rounded-md p-1 text-slate-500 transition-colors',
              'hover:bg-slate-100 hover:text-slate-700 dark:text-slate-400',
              'dark:hover:bg-slate-800 dark:hover:text-slate-200',
              'focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-brand-500',
            )}
            onClick={onClose}
            aria-label="Close"
            data-testid="modal-close"
          >
            <X className="h-4 w-4" aria-hidden />
          </button>
        </div>
        <div className="max-h-[75vh] overflow-y-auto px-5 py-4">{children}</div>
      </div>
    </div>
  );
}
