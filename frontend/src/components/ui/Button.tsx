import React from 'react';
import { cn } from '@/lib/cn';

/**
 * Button — app primary action primitive.
 *
 * Variants mirror the marketing site's CTA patterns:
 *   primary:   teal action button with subtle CTA glow shadow
 *   secondary: white "ghost" with navy border
 *   ghost:     transparent hover tint
 *   danger:    red confirmation for destructive actions
 *
 * All colors via design tokens (`ndi-teal`, `brand-navy`, `border-*`,
 * `fg-*`) so button surfaces stay in lockstep with ndi-cloud.com.
 */
type Variant = 'primary' | 'secondary' | 'ghost' | 'danger';
type Size = 'sm' | 'md' | 'lg';

export interface ButtonProps extends React.ButtonHTMLAttributes<HTMLButtonElement> {
  variant?: Variant;
  size?: Size;
}

const VARIANT: Record<Variant, string> = {
  primary:
    'bg-ndi-teal text-white hover:brightness-110 shadow-[var(--shadow-cta)] disabled:opacity-60',
  secondary:
    'bg-bg-surface text-brand-navy ring-1 ring-border-strong hover:bg-bg-muted',
  ghost:
    'bg-transparent text-fg-secondary hover:bg-bg-muted hover:text-brand-navy',
  danger:
    'bg-red-600 text-white hover:bg-red-700 shadow-sm',
};

const SIZE: Record<Size, string> = {
  sm: 'px-2.5 py-1 text-xs',
  md: 'px-3.5 py-1.5 text-sm',
  lg: 'px-5 py-2 text-base',
};

export function Button({ className, variant = 'primary', size = 'md', ...rest }: ButtonProps) {
  return (
    <button
      className={cn(
        'inline-flex items-center gap-1.5 rounded-md font-medium transition-all duration-200 ease-[var(--ease-out)]',
        'focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-ndi-teal',
        'disabled:cursor-not-allowed',
        VARIANT[variant],
        SIZE[size],
        className,
      )}
      {...rest}
    />
  );
}
