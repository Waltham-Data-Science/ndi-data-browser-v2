import type { HTMLAttributes, PropsWithChildren } from 'react';
import { cn } from '@/lib/cn';

/**
 * Card — surface primitive.
 *
 * Uses design tokens: `bg-bg-surface` + `ring-border-subtle` match the
 * marketing site's card surfaces. Light-mode only — the design bar
 * across ndi-cloud.com is intentionally one-mode to keep brand
 * consistency with the marketing site.
 */
export function Card({ className, children, ...rest }: PropsWithChildren<HTMLAttributes<HTMLDivElement>>) {
  return (
    <div
      className={cn(
        'rounded-lg bg-bg-surface shadow-sm ring-1 ring-border-subtle',
        'transition-[border-color,box-shadow,transform] duration-200 ease-[var(--ease-out)]',
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
        'flex flex-col gap-1.5 px-4 py-3 border-b border-border-subtle',
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
 * explicitly.
 *
 * Uses Geist display via `font-display` + tight tracking to match the
 * marketing site's card titles. */
export function CardTitle({
  className,
  children,
  as = 'h2',
}: PropsWithChildren<{ className?: string; as?: HeadingLevel }>) {
  const Tag = as;
  return (
    <Tag
      className={cn(
        'font-display text-base font-semibold leading-tight tracking-[-0.01em] text-fg-primary',
        className,
      )}
    >
      {children}
    </Tag>
  );
}

export function CardDescription({ className, children }: PropsWithChildren<{ className?: string }>) {
  return (
    <p className={cn('text-sm text-fg-muted', className)}>{children}</p>
  );
}
