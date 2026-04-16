import { useState } from 'react';
import { useNavigate, useSearchParams } from 'react-router-dom';
import { useLogin } from '@/api/auth';
import { Card, CardBody, CardHeader } from '@/components/ui/Card';
import { Input } from '@/components/ui/Input';
import { Button } from '@/components/ui/Button';
import { ApiError } from '@/api/errors';

export function LoginPage() {
  const login = useLogin();
  const navigate = useNavigate();
  const [params] = useSearchParams();
  const returnTo = sanitizeReturnTo(params.get('returnTo'));
  const [username, setUsername] = useState('');
  const [password, setPassword] = useState('');

  return (
    <div className="mx-auto max-w-md">
      <Card>
        <CardHeader>
          <h1 className="text-xl font-bold">Sign in</h1>
          <p className="text-sm text-slate-500">Use your NDI Cloud credentials.</p>
        </CardHeader>
        <CardBody>
          <form
            className="space-y-3"
            onSubmit={(e) => {
              e.preventDefault();
              login.mutate(
                { username, password },
                { onSuccess: () => navigate(returnTo) },
              );
            }}
          >
            <div>
              <label htmlFor="u" className="block text-sm font-medium mb-1">Email</label>
              <Input
                id="u"
                type="email"
                value={username}
                onChange={(e) => setUsername(e.target.value)}
                required
                autoComplete="email"
              />
            </div>
            <div>
              <label htmlFor="p" className="block text-sm font-medium mb-1">Password</label>
              <Input
                id="p"
                type="password"
                value={password}
                onChange={(e) => setPassword(e.target.value)}
                required
                autoComplete="current-password"
              />
            </div>
            {login.isError && login.error instanceof ApiError && (
              <p role="alert" className="text-sm text-red-600">
                {login.error.message}
              </p>
            )}
            <Button type="submit" disabled={login.isPending} className="w-full">
              {login.isPending ? 'Signing in…' : 'Sign in'}
            </Button>
          </form>
        </CardBody>
      </Card>
    </div>
  );
}

function sanitizeReturnTo(v: string | null): string {
  if (!v || !v.startsWith('/') || v.startsWith('//')) return '/datasets';
  return v;
}
