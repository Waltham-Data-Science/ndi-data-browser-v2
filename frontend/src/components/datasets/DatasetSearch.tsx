import { Search } from 'lucide-react';
import { Input } from '@/components/ui/Input';

interface DatasetSearchProps {
  value: string;
  onChange: (value: string) => void;
  placeholder?: string;
}

/** Search input with a magnifier icon. Ported from v1. */
export function DatasetSearch({
  value,
  onChange,
  placeholder = 'Search datasets…',
}: DatasetSearchProps) {
  return (
    <div className="relative">
      <Search className="pointer-events-none absolute left-3 top-1/2 -translate-y-1/2 h-4 w-4 text-slate-400 dark:text-slate-500" />
      <label htmlFor="dataset-search" className="sr-only">
        Search datasets
      </label>
      <Input
        id="dataset-search"
        placeholder={placeholder}
        value={value}
        onChange={(e) => onChange(e.target.value)}
        className="pl-9"
      />
    </div>
  );
}
