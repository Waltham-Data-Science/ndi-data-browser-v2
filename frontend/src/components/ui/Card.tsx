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

type HeadingLevel = 'h1' | 'h2' | 'h3' | 'h4' | 'h5' | 'h6';

/** CardTitle defaults to h2 — axe's heading-order rule requires that
 * headings increase by one. Most of our pages have a page-level h1
 * (Published datasets / dataset name / Query) and then cards, so the
 * natural next level is h2. A card inside a card can pass `as="h3"`
 * explicitly. */
export function CardTitle({
  className,
  children,
  as = 'h2',
}: PropsWithChildren<{ className?: string; as?: HeadingLevel }>) {
  const Tag = as;
  return (
    <Tag
      className={cn(
        'text-base font-semibold leading-tight text-slate-900 dark:text-slate-100',
        className,
      )}
    >
      {children}
    </Tag>
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
