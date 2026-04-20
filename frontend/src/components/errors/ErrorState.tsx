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
        className="mx-auto max-w-md rounded-lg bg-white p-6 shadow-sm ring-1 ring-gray-200 dark:bg-gray-900 dark:ring-gray-800 text-center"
      >
        <h2 className="text-lg font-semibold text-gray-900 dark:text-gray-100">Sign in required</h2>
        <p className="mt-2 text-sm text-gray-600 dark:text-gray-300">{message}</p>
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
        {code === 'QUERY_TOO_LARGE' && <QueryTooLargeHint />}
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
      className="rounded-md bg-gray-50 p-3 text-sm text-gray-700 ring-1 ring-gray-200 dark:bg-gray-900 dark:text-gray-300 dark:ring-gray-800"
    >
      {message}
      {code === 'QUERY_TOO_LARGE' && <QueryTooLargeHint />}
    </div>
  );
}

/** Narrowing hint surfaced alongside the generic "query returned too many
 * documents" retry message. Plan §M6 risk mitigation #4. */
function QueryTooLargeHint() {
  return (
    <div className="mt-3 rounded border border-amber-300/50 bg-white/60 dark:bg-gray-900/60 p-3 text-xs text-gray-700 dark:text-gray-300 space-y-1">
      <p className="font-semibold text-gray-900 dark:text-gray-100">
        Narrow the query to return fewer than 50,000 documents:
      </p>
      <ul className="list-disc pl-4 space-y-0.5">
        <li>
          Add an <code className="font-mono">isa</code> clause for a specific
          class (e.g. <code className="font-mono">subject</code>,{' '}
          <code className="font-mono">element_epoch</code>) instead of querying
          all <code className="font-mono">ndi_document</code>.
        </li>
        <li>Restrict the scope to a single dataset rather than `all` or `public`.</li>
        <li>
          Pair filters: combine <code className="font-mono">isa</code> with a{' '}
          <code className="font-mono">hasfield</code> or{' '}
          <code className="font-mono">depends_on</code> condition.
        </li>
      </ul>
    </div>
  );
}
