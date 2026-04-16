/**
 * Typed fetch wrapper. Always sends the session cookie, echoes CSRF, and
 * surfaces typed ApiError with a stable code that the UI routes on.
 */
import { ApiError, type ApiErrorBody } from './errors';

function getCookie(name: string): string | null {
  const match = document.cookie.match(new RegExp(`(?:^|; )${name}=([^;]*)`));
  return match ? decodeURIComponent(match[1]) : null;
}

async function ensureCsrfToken(): Promise<string> {
  const existing = getCookie('XSRF-TOKEN');
  if (existing) return existing;
  const r = await fetch('/api/auth/csrf', { credentials: 'include' });
  if (!r.ok) throw new Error(`Failed to fetch CSRF token: ${r.status}`);
  const body = (await r.json()) as { csrfToken: string };
  return body.csrfToken;
}

export interface ApiOptions {
  method?: 'GET' | 'POST' | 'DELETE';
  body?: unknown;
  signal?: AbortSignal;
  idempotencyKey?: string;
}

export async function apiFetch<T>(path: string, options: ApiOptions = {}): Promise<T> {
  const method = options.method ?? 'GET';
  const headers: Record<string, string> = {
    Accept: 'application/json',
  };
  let body: BodyInit | undefined;
  if (options.body !== undefined) {
    headers['Content-Type'] = 'application/json';
    body = JSON.stringify(options.body);
  }

  // CSRF — only required for mutations.
  if (method !== 'GET' && path !== '/api/auth/csrf' && path !== '/api/auth/login') {
    const token = await ensureCsrfToken();
    headers['X-XSRF-TOKEN'] = token;
  }

  // Login path needs a CSRF pair too (double-submit, even on first auth).
  if (path === '/api/auth/login') {
    const token = await ensureCsrfToken();
    headers['X-XSRF-TOKEN'] = token;
  }

  if (options.idempotencyKey) {
    headers['X-Idempotency-Key'] = options.idempotencyKey;
  }

  const res = await fetch(path, {
    method,
    headers,
    body,
    credentials: 'include',
    signal: options.signal,
  });

  if (res.status === 204) return undefined as T;

  // Error path.
  if (!res.ok) {
    let errBody: ApiErrorBody | null = null;
    try {
      errBody = (await res.json()) as ApiErrorBody;
    } catch {
      // fall through
    }
    if (errBody?.error) {
      throw new ApiError(errBody.error, res.status);
    }
    throw new ApiError(
      {
        code: 'INTERNAL',
        message: `Request failed (${res.status})`,
        recovery: 'contact_support',
        requestId: null,
      },
      res.status,
    );
  }

  const ct = res.headers.get('content-type') ?? '';
  if (ct.includes('application/json')) {
    return (await res.json()) as T;
  }
  return (await res.text()) as unknown as T;
}
