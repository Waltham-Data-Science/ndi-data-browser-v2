import { BookOpen, FileText, Users } from 'lucide-react';
import { Link } from 'react-router-dom';

import type { DatasetRecord } from '@/api/datasets';
import { Badge } from '@/components/ui/Badge';
import {
  Card,
  CardBody,
  CardDescription,
  CardHeader,
  CardTitle,
} from '@/components/ui/Card';
import { formatBytes, formatDate, truncate } from '@/lib/format';

interface DatasetCardProps {
  dataset: DatasetRecord;
}

/** Rich catalog card — ported from v1 with v2's camelCase field names
 * and a small adaptation: v2 doesn't carry `species`/`brain_regions` on
 * the list response; those are inferred from openminds companions at the
 * detail level. We show license + organization as lightweight badges
 * here instead.
 */
export function DatasetCard({ dataset }: DatasetCardProps) {
  const abstract = dataset.abstract ?? dataset.description;
  const contributors = (dataset.contributors ?? [])
    .map((c) => [c.firstName, c.lastName].filter(Boolean).join(' '))
    .filter(Boolean);

  return (
    <Link
      to={`/datasets/${dataset.id}`}
      className="block group focus:outline-none focus-visible:ring-2 focus-visible:ring-brand-500 rounded-lg"
      aria-label={`Open dataset ${dataset.name}`}
    >
      <Card className="h-full transition-shadow group-hover:shadow-md group-hover:ring-brand-400">
        <CardHeader className="pb-2">
          <CardTitle className="line-clamp-2">{dataset.name}</CardTitle>
          {abstract && (
            <CardDescription className="line-clamp-2 text-xs">
              {truncate(abstract, 220)}
            </CardDescription>
          )}
        </CardHeader>
        <CardBody className="pt-0 space-y-2">
          <div className="flex flex-wrap gap-1.5">
            {dataset.license && (
              <Badge variant="outline">{dataset.license}</Badge>
            )}
            {dataset.organizationId && (
              <Badge variant="secondary" className="font-mono normal-case">
                {dataset.organizationId.length > 14
                  ? `${dataset.organizationId.slice(0, 14)}…`
                  : dataset.organizationId}
              </Badge>
            )}
            {dataset.publishStatus && dataset.publishStatus !== 'published' && (
              <Badge variant="secondary">{dataset.publishStatus}</Badge>
            )}
          </div>

          {contributors.length > 0 && (
            <p className="text-xs text-slate-500 dark:text-slate-400 line-clamp-1">
              {contributors.slice(0, 3).join(', ')}
              {contributors.length > 3 && ` +${contributors.length - 3}`}
            </p>
          )}

          <div className="flex flex-wrap items-center gap-3 text-[11px] text-slate-500 dark:text-slate-400 font-mono">
            {dataset.documentCount != null && (
              <span className="inline-flex items-center gap-1">
                <FileText className="h-3 w-3" />
                {dataset.documentCount.toLocaleString()} docs
              </span>
            )}
            {dataset.contributors && dataset.contributors.length > 0 && (
              <span className="inline-flex items-center gap-1">
                <Users className="h-3 w-3" />
                {dataset.contributors.length} contributors
              </span>
            )}
            {dataset.totalSize != null && dataset.totalSize > 0 && (
              <span>{formatBytes(dataset.totalSize)}</span>
            )}
            {dataset.doi && (
              <span className="inline-flex items-center gap-1">
                <BookOpen className="h-3 w-3" />
                <span className="truncate max-w-[160px]">{dataset.doi}</span>
              </span>
            )}
          </div>

          {/* Date metadata uses slate-500 on white (ratio 4.78:1) rather
              than slate-400 (2.63:1) to satisfy WCAG AA; darker dark-mode
              pairing kept as slate-400 since the contrast ratio against
              slate-900 is already fine. */}
          <div className="flex items-center gap-2 text-[10px] text-slate-500 dark:text-slate-400">
            {dataset.createdAt && <span>Created {formatDate(dataset.createdAt)}</span>}
            {dataset.updatedAt && dataset.updatedAt !== dataset.createdAt && (
              <span>Updated {formatDate(dataset.updatedAt)}</span>
            )}
          </div>
        </CardBody>
      </Card>
    </Link>
  );
}
