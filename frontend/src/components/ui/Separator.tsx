import type { HTMLAttributes } from 'react';
import { cn } from '@/lib/cn';

interface SeparatorProps extends HTMLAttributes<HTMLHRElement> {
  orientation?: 'horizontal' | 'vertical';
}

export function Separator({
  className,
  orientation = 'horizontal',
  ...rest
}: SeparatorProps) {
  if (orientation === 'vertical') {
    return (
      <span
        role="separator"
        aria-orientation="vertical"
        className={cn('inline-block w-px self-stretch bg-slate-200 dark:bg-slate-700', className)}
        {...rest}
      />
    );
  }
  return (
    <hr
      className={cn('border-0 h-px bg-slate-200 dark:bg-slate-700', className)}
      {...rest}
    />
  );
}
