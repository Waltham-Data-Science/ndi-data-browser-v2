import { Badge } from '@/components/ui/Badge';
import { cn } from '@/lib/cn';

export interface DocTypeCount {
  className: string;
  count: number;
}

interface DocumentTypeSelectorProps {
  types: DocTypeCount[];
  selected: string | null;
  onSelect: (type: string | null) => void;
  total: number;
}

/** Left-rail class selector — ported from v1. Virtualization unnecessary
 * since NDI datasets rarely exceed ~30 distinct classes. */
export function DocumentTypeSelector({
  types,
  selected,
  onSelect,
  total,
}: DocumentTypeSelectorProps) {
  return (
    <div className="space-y-0.5 max-h-[500px] overflow-y-auto" role="listbox" aria-label="Document classes">
      <TypeRow
        label="All documents"
        count={total}
        selected={selected === null}
        onClick={() => onSelect(null)}
      />
      {types.map((t) => (
        <TypeRow
          key={t.className}
          label={t.className}
          count={t.count}
          selected={selected === t.className}
          onClick={() => onSelect(t.className)}
        />
      ))}
    </div>
  );
}

function TypeRow({
  label,
  count,
  selected,
  onClick,
}: {
  label: string;
  count: number;
  selected: boolean;
  onClick: () => void;
}) {
  return (
    <button
      type="button"
      role="option"
      aria-selected={selected}
      onClick={onClick}
      className={cn(
        'w-full flex items-center justify-between gap-2 px-2 py-1.5 rounded text-xs transition-colors',
        selected
          ? 'bg-slate-900 text-white dark:bg-white dark:text-slate-900'
          : 'text-slate-600 dark:text-slate-400 hover:bg-slate-100 dark:hover:bg-slate-800',
      )}
    >
      <span className="font-mono truncate flex-1 text-left">{label}</span>
      <Badge
        variant={selected ? 'outline' : 'secondary'}
        className="font-mono shrink-0"
      >
        {count.toLocaleString()}
      </Badge>
    </button>
  );
}
