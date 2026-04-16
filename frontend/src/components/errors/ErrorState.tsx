import { useNavigate } from 'react-router-dom';
import { ApiError } from '@/api/errors';
import { Button } from '@/components/ui/Button';

export interface ErrorStateProps {
  error: unknown;
  onRetry?: () => void;
}

/**
 * Renders the correct UI for any ApiError, following docs/error-catalog.md.
 * - retry: inline message + button
 * - login: full-page lockout, auto-redirects
 * - contact_support: message + request ID copy + mailto
 * - none: inline message
 */
export function ErrorState({ error, onRetry }: ErrorStateProps) {
  const navigate = useNavigate();
  const apiError = error instanceof ApiError ? error : null;
  const code = apiError?.code ?? 'INTERNAL';
  const message = apiError?.message ?? (error instanceof Error ? error.message : 'Something went wrong.');
  const recovery = apiError?.recovery ?? 'contact_support';
  const requestId = apiError?.requestId;

  if (recovery === 'login') {
    const here = typeof window !== 'undefined' ? window.location.pathname + window.location.search : '/';
    return (
      <div
        role="alert"
        className="mx-auto max-w-md rounded-lg bg-white p-6 shadow-sm ring-1 ring-slate-200 dark:bg-slate-900 dark:ring-slate-800 text-center"
      >
        <h2 className="text-lg font-semibold text-slate-900 dark:text-slate-100">Sign in required</h2>
        <p className="mt-2 text-sm text-slate-600 dark:text-slate-300">{message}</p>
        <Button
          className="mt-4"
          onClick={() => navigate(`/login?returnTo=${encodeURIComponent(here)}`)}
        >
          Go to sign in
        </Button>
      </div>
    );
  }

  if (recovery === 'retry') {
    return (
      <div
        role="alert"
        className="rounded-lg bg-amber-50 p-4 ring-1 ring-amber-200 dark:bg-amber-950 dark:ring-amber-900"
      >
        <p className="text-sm text-amber-800 dark:text-amber-200">{message}</p>
        {onRetry && (
          <Button className="mt-3" size="sm" variant="secondary" onClick={onRetry}>
            Try again
          </Button>
        )}
        <p className="mt-2 text-[11px] text-amber-700 dark:text-amber-300">code {code}</p>
      </div>
    );
  }

  if (recovery === 'contact_support') {
    return (
      <div
        role="alert"
        className="rounded-lg bg-red-50 p-4 ring-1 ring-red-200 dark:bg-red-950 dark:ring-red-900"
      >
        <p className="text-sm font-medium text-red-900 dark:text-red-100">{message}</p>
        {requestId && (
          <p className="mt-2 text-xs text-red-700 dark:text-red-300">
            Request ID: <code className="font-mono">{requestId}</code>
          </p>
        )}
        <p className="mt-2 text-xs text-red-700 dark:text-red-300">
          Please{' '}
          <a
            href={`mailto:support@ndi-cloud.com?subject=Browser%20error%20${encodeURIComponent(code)}&body=Request%20ID:%20${encodeURIComponent(requestId ?? 'none')}`}
            className="underline"
          >
            contact support
          </a>{' '}
          with this ID.
        </p>
      </div>
    );
  }

  // NONE
  return (
    <div
      role="alert"
      className="rounded-md bg-slate-50 p-3 text-sm text-slate-700 ring-1 ring-slate-200 dark:bg-slate-900 dark:text-slate-300 dark:ring-slate-800"
    >
      {message}
    </div>
  );
}
