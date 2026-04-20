import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { apiFetch } from './client';

export interface MeResponse {
  userId: string;
  email_hash: string;
  issuedAt: number;
  lastActive: number;
  expiresAt: number;
  /**
   * NDI-Cloud organization IDs the caller belongs to, cached on the
   * session at login. Drives `/my`'s per-org fan-out on the backend
   * and lets us surface admin affordances on the frontend without a
   * second cloud round-trip. Added 2026-04-20.
   */
  organizationIds: string[];
  /** Whether the caller is a cloud-level admin. When `organizationIds`
   * is empty but `isAdmin` is true, treat the user as orphaned rather
   * than denied — `/my` will surface a helpful empty state. */
  isAdmin: boolean;
}

export function useMe() {
  return useQuery({
    queryKey: ['me'],
    queryFn: () => apiFetch<MeResponse>('/api/auth/me'),
    retry: false,
    staleTime: 60_000,
  });
}

export function useLogin() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (body: { username: string; password: string }) =>
      apiFetch<{ ok: boolean; user: { id: string }; expiresAt: number }>('/api/auth/login', {
        method: 'POST',
        body,
      }),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ['me'] });
      qc.invalidateQueries({ queryKey: ['datasets', 'my'] });
    },
  });
}

export function useLogout() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: () => apiFetch<{ ok: boolean }>('/api/auth/logout', { method: 'POST' }),
    onSuccess: () => {
      qc.clear();
    },
  });
}
