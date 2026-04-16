import { Link } from 'react-router-dom';

export function NotFoundPage() {
  return (
    <div className="mx-auto max-w-md text-center py-16">
      <h1 className="text-3xl font-bold">404</h1>
      <p className="mt-2 text-slate-600 dark:text-slate-400">
        Nothing here. <Link className="underline" to="/">Back to home</Link>.
      </p>
    </div>
  );
}
