import type { HTMLAttributes, PropsWithChildren } from 'react';
import { cn } from '@/lib/cn';

export function Card({ className, children, ...rest }: PropsWithChildren<HTMLAttributes<HTMLDivElement>>) {
  return (
    <div
      className={cn(
        'rounded-lg bg-white shadow-sm ring-1 ring-slate-200',
        'dark:bg-slate-900 dark:ring-slate-800',
        className,
      )}
      {...rest}
    >
      {children}
    </div>
  );
}

export function CardHeader({ className, children }: PropsWithChildren<{ className?: string }>) {
  return (
    <div
      className={cn(
        'flex flex-col gap-1.5 px-4 py-3 border-b border-slate-200 dark:border-slate-800',
        className,
      )}
    >
      {children}
    </div>
  );
}

export function CardBody({ className, children }: PropsWithChildren<{ className?: string }>) {
  return <div className={cn('p-4', className)}>{children}</div>;
}

export function CardTitle({ className, children }: PropsWithChildren<{ className?: string }>) {
  return (
    <h3
      className={cn(
        'text-base font-semibold leading-tight text-slate-900 dark:text-slate-100',
        className,
      )}
    >
      {children}
    </h3>
  );
}

export function CardDescription({ className, children }: PropsWithChildren<{ className?: string }>) {
  return (
    <p className={cn('text-sm text-slate-500 dark:text-slate-400', className)}>{children}</p>
  );
}

export function CardFooter({ className, children }: PropsWithChildren<{ className?: string }>) {
  return (
    <div
      className={cn(
        'flex items-center gap-2 px-4 py-3 border-t border-slate-200 dark:border-slate-800',
        className,
      )}
    >
      {children}
    </div>
  );
}
