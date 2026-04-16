import React from 'react';
import { cn } from '@/lib/cn';

type InputProps = React.InputHTMLAttributes<HTMLInputElement>;

export function Input({ className, ...rest }: InputProps) {
  return (
    <input
      className={cn(
        'w-full rounded-md px-3 py-1.5 text-sm',
        'bg-white ring-1 ring-slate-300 placeholder-slate-400 text-slate-900',
        'dark:bg-slate-900 dark:ring-slate-700 dark:text-slate-100 dark:placeholder-slate-500',
        'focus-visible:outline-2 focus-visible:outline-offset-0 focus-visible:outline-brand-500',
        className,
      )}
      {...rest}
    />
  );
}
