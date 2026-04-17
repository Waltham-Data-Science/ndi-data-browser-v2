import type { HTMLAttributes, PropsWithChildren } from 'react';
import { cn } from '@/lib/cn';

type Variant = 'default' | 'outline' | 'secondary';

interface BadgeProps extends HTMLAttributes<HTMLSpanElement> {
  variant?: Variant;
}

const VARIANT: Record<Variant, string> = {
  default:
    'bg-brand-500 text-white ring-1 ring-inset ring-brand-600 dark:bg-brand-400 dark:ring-brand-300',
  outline:
    'bg-transparent text-slate-700 ring-1 ring-inset ring-slate-300 dark:text-slate-200 dark:ring-slate-600',
  secondary:
    'bg-slate-100 text-slate-700 ring-1 ring-inset ring-slate-200 dark:bg-slate-800 dark:text-slate-200 dark:ring-slate-700',
};

export function Badge({
  className,
  children,
  variant = 'default',
  ...rest
}: PropsWithChildren<BadgeProps>) {
  return (
    <span
      className={cn(
        'inline-flex items-center rounded-full px-2 py-0.5 text-[10px] font-medium tracking-wide',
        VARIANT[variant],
        className,
      )}
      {...rest}
    >
      {children}
    </span>
  );
}
