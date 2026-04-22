/**
 * Tabs — minimal ARIA-compliant tab list.
 *
 * Controlled component. Keyboard support is arrow-left / arrow-right per
 * the WAI-ARIA authoring practice for tabs. Only this file uses tabs
 * right now (UseThisDataModal); promote to a shared primitive when a
 * second consumer appears.
 *
 * Active-state color uses `ndi-teal` + underline to match the marketing
 * site's active-nav treatment and the product's link color.
 */
import { useCallback, type KeyboardEvent } from 'react';

import { cn } from '@/lib/cn';

export interface TabItem<T extends string = string> {
  id: T;
  label: string;
  /** Optional test id for the tab trigger; falls back to `tab-${id}`. */
  testId?: string;
}

export interface TabsProps<T extends string = string> {
  tabs: TabItem<T>[];
  active: T;
  onSelect: (id: T) => void;
  className?: string;
}

export function Tabs<T extends string = string>({
  tabs,
  active,
  onSelect,
  className,
}: TabsProps<T>) {
  const onKey = useCallback(
    (e: KeyboardEvent<HTMLButtonElement>) => {
      if (e.key !== 'ArrowLeft' && e.key !== 'ArrowRight') return;
      e.preventDefault();
      const i = tabs.findIndex((t) => t.id === active);
      if (i < 0) return;
      const next = e.key === 'ArrowRight' ? (i + 1) % tabs.length : (i - 1 + tabs.length) % tabs.length;
      onSelect(tabs[next]!.id);
    },
    [tabs, active, onSelect],
  );

  return (
    <div
      role="tablist"
      aria-orientation="horizontal"
      className={cn(
        'flex items-center gap-1 border-b border-border-subtle',
        className,
      )}
      data-testid="tabs"
    >
      {tabs.map((t) => {
        const isActive = t.id === active;
        return (
          <button
            key={t.id}
            role="tab"
            type="button"
            aria-selected={isActive}
            tabIndex={isActive ? 0 : -1}
            onClick={() => onSelect(t.id)}
            onKeyDown={onKey}
            className={cn(
              '-mb-px border-b-2 px-3 py-1.5 text-xs font-medium transition-colors',
              'focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-ndi-teal',
              isActive
                ? 'border-ndi-teal text-ndi-teal'
                : 'border-transparent text-fg-muted hover:text-fg-secondary',
            )}
            data-testid={t.testId ?? `tab-${t.id}`}
          >
            {t.label}
          </button>
        );
      })}
    </div>
  );
}
