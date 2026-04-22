import type { HTMLAttributes, PropsWithChildren } from 'react';
import { cn } from '@/lib/cn';

/**
 * Badge — small inline tag / label.
 *
 * Variants map to the marketing site's tag patterns in search.html:
 *   default:   navy "primary" (dataset versions, status)
 *   outline:   neutral with border (class names, raw IDs)
 *   secondary: gray pill (citation counts, metadata)
 *   teal:      teal-tinted (NDI-specific affordances like FAIR / Published)
 *
 * Tracking/size match the mockup's tag treatment (0.1em tracking, 10px,
 * bold, uppercase-ish).
 */
type Variant = 'default' | 'outline' | 'secondary' | 'teal' | 'pub';

interface BadgeProps extends HTMLAttributes<HTMLSpanElement> {
  variant?: Variant;
}

const VARIANT: Record<Variant, string> = {
  default:
    'bg-brand-navy text-white ring-1 ring-inset ring-brand-navy',
  outline:
    'bg-transparent text-fg-secondary ring-1 ring-inset ring-border-strong',
  secondary:
    'bg-bg-muted text-fg-secondary ring-1 ring-inset ring-border-subtle',
  teal:
    'bg-ndi-teal-light text-ndi-teal ring-1 ring-inset ring-ndi-teal-border',
  pub:
    // "Published" pill from search.html — soft blue, mirrors the
    // .tag.pub style on marketing cards.
    'bg-[#EAF4FF] text-[#0B5FA8] ring-1 ring-inset ring-[#C8DCF0]',
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
        'inline-flex items-center rounded-full px-2 py-0.5 text-[10px] font-bold tracking-[0.1em] uppercase',
        VARIANT[variant],
        className,
      )}
      {...rest}
    >
      {children}
    </span>
  );
}
