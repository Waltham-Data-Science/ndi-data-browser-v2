import React from 'react';
import { Button } from '@/components/ui/Button';

interface State {
  error: unknown;
}

export class ErrorBoundary extends React.Component<React.PropsWithChildren, State> {
  state: State = { error: null };

  static getDerivedStateFromError(error: unknown): State {
    return { error };
  }

  componentDidCatch(error: unknown, info: React.ErrorInfo): void {
    console.error('React render error', error, info);
  }

  render() {
    if (this.state.error) {
      return (
        <div className="mx-auto my-16 max-w-md rounded-lg bg-white p-6 shadow-sm ring-1 ring-gray-200 text-center">
          <h1 className="text-lg font-semibold">Something went wrong</h1>
          <p className="mt-2 text-sm text-gray-600">
            Please refresh the page. If the problem persists, contact support.
          </p>
          <Button className="mt-4" onClick={() => window.location.reload()}>
            Refresh
          </Button>
        </div>
      );
    }
    return this.props.children;
  }
}
