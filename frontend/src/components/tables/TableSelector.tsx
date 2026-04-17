import { BookOpen, Brain, Clock, Layers, Pill, Radio, TableProperties, Users } from 'lucide-react';
import type { TableType } from '@/api/tables';
import { cn } from '@/lib/cn';

interface TableSelectorProps {
  active: TableType;
  onChange: (type: TableType) => void;
  /** Counts keyed by class name — drives the per-tab row-count badge.
   * Missing entries render without a count. */
  counts?: Partial<Record<TableType, number>>;
  /** When set, disables tabs whose counts are zero. Defaults to true. */
  hideEmpty?: boolean;
}

interface TabSpec {
  type: TableType;
  label: string;
  icon: React.ComponentType<{ className?: string }>;
}

const TABS: TabSpec[] = [
  { type: 'combined', label: 'Combined', icon: TableProperties },
  { type: 'subject', label: 'Subjects', icon: Users },
  { type: 'element', label: 'Probes', icon: Radio },
  { type: 'element_epoch', label: 'Epochs', icon: Clock },
  { type: 'treatment', label: 'Treatments', icon: Pill },
  { type: 'probe_location', label: 'Locations', icon: Brain },
  { type: 'openminds_subject', label: 'OpenMINDS', icon: Layers },
  { type: 'ontology', label: 'Ontology', icon: BookOpen },
];

export function TableSelector({
  active,
  onChange,
  counts,
  hideEmpty = true,
}: TableSelectorProps) {
  const visibleTabs = counts && hideEmpty
    ? TABS.filter((t) => {
        // Combined and ontology always visible.
        if (t.type === 'combined' || t.type === 'ontology') return true;
        return (counts[t.type] ?? 0) > 0;
      })
    : TABS;

  return (
    <div
      role="tablist"
      aria-label="Summary table type"
      className="flex items-center gap-1 border-b border-slate-200 dark:border-slate-700 pb-px overflow-x-auto"
    >
      {visibleTabs.map(({ type, label, icon: Icon }) => {
        const isActive = active === type;
        const count = counts?.[type];
        return (
          <button
            key={type}
            type="button"
            role="tab"
            aria-selected={isActive}
            onClick={() => onChange(type)}
            className={cn(
              'flex items-center gap-1.5 px-3 py-1.5 text-xs font-medium rounded-t-md transition-colors whitespace-nowrap shrink-0',
              isActive
                ? 'bg-white dark:bg-slate-900 text-slate-900 dark:text-slate-100 border border-slate-200 dark:border-slate-700 border-b-white dark:border-b-slate-900 -mb-px'
                : 'text-slate-600 dark:text-slate-400 hover:text-slate-900 dark:hover:text-slate-100',
            )}
          >
            <Icon className="h-3.5 w-3.5" />
            {label}
            {count !== undefined && (
              <span className="text-[10px] font-mono text-slate-400 dark:text-slate-500">
                {count.toLocaleString()}
              </span>
            )}
          </button>
        );
      })}
    </div>
  );
}
