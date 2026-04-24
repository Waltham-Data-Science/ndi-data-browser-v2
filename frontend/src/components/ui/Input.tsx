import React from 'react';
import { cn } from '@/lib/cn';

type InputProps = React.InputHTMLAttributes<HTMLInputElement>;

export function Input({ className, ...rest }: InputProps) {
  return (
    <input
      className={cn(
        'w-full rounded-md px-3 py-1.5 text-sm',
        'bg-white ring-1 ring-gray-300 placeholder-gray-400 text-gray-900',
        'focus-visible:outline-2 focus-visible:outline-offset-0 focus-visible:outline-brand-500',
        className,
      )}
      {...rest}
    />
  );
}
