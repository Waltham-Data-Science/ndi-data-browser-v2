import { useState } from 'react';
import { Link, Navigate, Outlet, useParams } from 'react-router-dom';
import {
  BookOpen,
  Code2,
  FileText,
  Globe,
  Quote,
  Users,
} from 'lucide-react';

import { useClassCounts, useDataset, useDatasetSummary, type DatasetRecord } from '@/api/datasets';
import { CiteModal } from '@/components/datasets/CiteModal';
import { DatasetSummaryCard } from '@/components/datasets/DatasetSummaryCard';
import { UseThisDataModal } from '@/components/datasets/UseThisDataModal';
import { Badge } from '@/components/ui/Badge';
import { Button } from '@/components/ui/Button';
import {
  Card,
  CardBody,
  CardDescription,
  CardHeader,
  CardTitle,
} from '@/components/ui/Card';
import { CardSkeleton, Skeleton } from '@/components/ui/Skeleton';
import { ErrorState } from '@/components/errors/ErrorState';
import { ExternalAnchor } from '@/components/ExternalAnchor';
import { formatBytes, formatDate, formatNumber } from '@/lib/format';
import type { DatasetSummary } from '@/types/dataset-summary';

const COMMON_CLASSES = [
  'subject',
  'element',
  'element_epoch',
  'treatment',
  'openminds_subject',
  'probe_location',
];

export function DatasetDetailPage() {
  const { id } = useParams();
  const ds = useDataset(id);
  const cc = useClassCounts(id);
  const summary = useDatasetSummary(id);

  if (!id) return <Navigate to="/datasets" replace />;

  return (
    <div className="grid gap-4 lg:grid-cols-[340px_1fr]">
      <aside className="space-y-3">
        {summary.isLoading && <CardSkeleton />}
        {summary.isError && (
          <ErrorState error={summary.error} onRetry={() => summary.refetch()} />
        )}
        {summary.data && <DatasetSummaryCard summary={summary.data} />}

        {ds.isLoading && <CardSkeleton />}
        {ds.isError && <ErrorState error={ds.error} onRetry={() => ds.refetch()} />}
        {ds.data && (
          <DatasetOverviewCard
            ds={ds.data}
            datasetId={id}
            summary={summary.data}
          />
        )}

        <Card>
          <CardHeader>
            <CardTitle className="text-sm">Document classes</CardTitle>
            <CardDescription>
              Click any class to open it in the Raw Documents explorer.
            </CardDescription>
          </CardHeader>
          <CardBody>
            {cc.isLoading && <Skeleton className="h-32 w-full" />}
            {cc.isError && <ErrorState error={cc.error} onRetry={() => cc.refetch()} />}
            {cc.data && <ClassCountsList datasetId={id} data={cc.data} />}
          </CardBody>
        </Card>
      </aside>

      <section className="space-y-3">
        <Outlet />
      </section>
    </div>
  );
}

function DatasetOverviewCard({
  ds,
  datasetId,
  summary,
}: {
  ds: DatasetRecord;
  datasetId: string;
  summary?: DatasetSummary;
}) {
  const abstract = ds.description ?? ds.abstract;
  const [citeOpen, setCiteOpen] = useState(false);
  const [useDataOpen, setUseDataOpen] = useState(false);
  return (
    <Card>
      <CardHeader>
        <h1 className="text-lg font-bold text-slate-900 dark:text-slate-100 leading-tight">
          {ds.name}
        </h1>
        <div className="flex flex-wrap gap-1.5 pt-1">
          {ds.license && <Badge variant="outline">{ds.license}</Badge>}
          {ds.branchName && ds.branchName !== 'main' && (
            <Badge variant="secondary">{ds.branchName}</Badge>
          )}
          {ds.isPublished === false && <Badge variant="secondary">draft</Badge>}
        </div>
        {ds.affiliation && (
          <p className="text-[11px] text-slate-500 dark:text-slate-400 leading-tight pt-1">
            {ds.affiliation}
          </p>
        )}
      </CardHeader>

      <CardBody className="space-y-4 text-sm">
        {abstract && (
          <p className="text-slate-700 dark:text-slate-300 text-[13px] leading-relaxed">
            {abstract}
          </p>
        )}

        <dl className="grid grid-cols-[auto_1fr] gap-x-3 gap-y-1.5 text-xs">
          <DatasetStat label="Species" value={ds.species} />
          <DatasetStat label="Brain regions" value={ds.brainRegions} />
          <DatasetStat
            label="Subjects"
            value={ds.numberOfSubjects != null ? formatNumber(ds.numberOfSubjects) : undefined}
          />
          <DatasetStat
            label="Documents"
            value={ds.documentCount != null ? formatNumber(ds.documentCount) : undefined}
          />
          <DatasetStat
            label="Size"
            value={ds.totalSize != null ? formatBytes(ds.totalSize) : undefined}
          />
          <DatasetStat
            label="Neurons"
            value={ds.neurons != null && ds.neurons > 0 ? formatNumber(ds.neurons) : undefined}
          />
        </dl>

        {(ds.contributors?.length ?? 0) > 0 && (
          <div className="space-y-1.5">
            <h2 className="text-xs font-semibold text-slate-600 dark:text-slate-300 flex items-center gap-1">
              <Users className="h-3 w-3" /> Contributors
            </h2>
            <ul className="space-y-0.5 text-xs">
              {ds.contributors!.map((c, i) => (
                <ContributorRow key={`${c.firstName}-${c.lastName}-${i}`} c={c} />
              ))}
            </ul>
          </div>
        )}

        {(ds.correspondingAuthors?.length ?? 0) > 0 && (
          <div className="space-y-1.5">
            <h2 className="text-xs font-semibold text-slate-600 dark:text-slate-300">
              Corresponding authors
            </h2>
            <ul className="space-y-0.5 text-xs">
              {ds.correspondingAuthors!.map((c, i) => (
                <ContributorRow key={`${c.firstName}-${c.lastName}-${i}`} c={c} />
              ))}
            </ul>
          </div>
        )}

        {(ds.funding?.length ?? 0) > 0 && (
          <div className="space-y-1">
            <h2 className="text-xs font-semibold text-slate-600 dark:text-slate-300">
              Funding
            </h2>
            <p className="text-xs text-slate-600 dark:text-slate-400">
              {ds
                .funding!.map((f) => f.source)
                .filter(Boolean)
                .join('; ')}
            </p>
          </div>
        )}

        {(ds.associatedPublications?.length ?? 0) > 0 && (
          <div className="space-y-1.5">
            <h2 className="text-xs font-semibold text-slate-600 dark:text-slate-300 flex items-center gap-1">
              <BookOpen className="h-3 w-3" /> Associated publications
            </h2>
            <ul className="space-y-1 text-xs">
              {ds.associatedPublications!.map((p, i) => (
                <PublicationRow key={p.DOI ?? p.PMID ?? i} p={p} />
              ))}
            </ul>
          </div>
        )}

        <dl className="grid grid-cols-[auto_1fr] gap-x-3 gap-y-1 text-[11px] text-slate-500 dark:text-slate-400 font-mono border-t border-slate-200 dark:border-slate-700 pt-3">
          {ds.doi && (
            <>
              <dt>DOI</dt>
              <dd>
                <ExternalAnchor href={ds.doi} label={ds.doi} />
              </dd>
            </>
          )}
          {ds.pubMedId && (
            <>
              <dt>PubMed</dt>
              <dd>
                <ExternalAnchor
                  href={`https://pubmed.ncbi.nlm.nih.gov/${ds.pubMedId}/`}
                  label={ds.pubMedId}
                />
              </dd>
            </>
          )}
          {ds.organizationId && (
            <>
              <dt>Org</dt>
              <dd>{ds.organizationId}</dd>
            </>
          )}
          <dt>Created</dt>
          <dd>{formatDate(ds.createdAt)}</dd>
          <dt>Updated</dt>
          <dd>{formatDate(ds.updatedAt)}</dd>
          {ds.uploadedAt && (
            <>
              <dt>Uploaded</dt>
              <dd>{formatDate(ds.uploadedAt)}</dd>
            </>
          )}
        </dl>

        <div
          className="flex flex-wrap gap-2 border-t border-slate-200 pt-3 dark:border-slate-700"
          data-testid="dataset-actions"
        >
          <Button
            variant="secondary"
            size="sm"
            onClick={() => setCiteOpen(true)}
            disabled={!summary}
            data-testid="open-cite-modal"
            aria-label="Open citation formats"
          >
            <Quote className="h-3 w-3" aria-hidden />
            Cite
          </Button>
          <Button
            variant="secondary"
            size="sm"
            onClick={() => setUseDataOpen(true)}
            data-testid="open-use-data-modal"
            aria-label="Open code snippets for local analysis"
          >
            <Code2 className="h-3 w-3" aria-hidden />
            Use this data
          </Button>
        </div>
      </CardBody>
      {summary && (
        <CiteModal
          open={citeOpen}
          onClose={() => setCiteOpen(false)}
          citation={summary.citation}
        />
      )}
      <UseThisDataModal
        open={useDataOpen}
        onClose={() => setUseDataOpen(false)}
        datasetId={datasetId}
      />
    </Card>
  );
}

function DatasetStat({ label, value }: { label: string; value?: string | null }) {
  if (!value) return null;
  return (
    <>
      <dt className="font-medium text-slate-500 dark:text-slate-400">{label}</dt>
      <dd className="text-slate-700 dark:text-slate-300">{value}</dd>
    </>
  );
}

function ContributorRow({ c }: { c: import('@/api/datasets').Contributor }) {
  const name = [c.firstName, c.lastName].filter(Boolean).join(' ').trim();
  if (!name && !c.contact) return null;
  return (
    <li className="flex items-center gap-1.5">
      <span className="text-slate-700 dark:text-slate-300">{name || c.contact}</span>
      {c.orcid && (
        <ExternalAnchor
          href={c.orcid}
          label="ORCID"
          className="text-[10px]"
          iconSize={10}
        />
      )}
    </li>
  );
}

function PublicationRow({ p }: { p: import('@/api/datasets').AssociatedPublication }) {
  const title = p.title || p.DOI || p.PMID || 'Publication';
  return (
    <li className="space-y-0.5">
      {p.DOI ? (
        <ExternalAnchor href={p.DOI} label={title} className="text-xs leading-snug" />
      ) : (
        <span className="text-slate-700 dark:text-slate-300">{title}</span>
      )}
      <div className="flex flex-wrap gap-2 text-[10px] text-slate-500 dark:text-slate-400 font-mono">
        {p.DOI && <span>DOI</span>}
        {p.PMID && (
          <ExternalAnchor
            href={`https://pubmed.ncbi.nlm.nih.gov/${p.PMID}/`}
            label={`PMID ${p.PMID}`}
            iconSize={10}
            className="text-[10px]"
          />
        )}
        {p.PMCID && (
          <ExternalAnchor
            href={`https://www.ncbi.nlm.nih.gov/pmc/articles/${p.PMCID}/`}
            label={p.PMCID}
            iconSize={10}
            className="text-[10px]"
          />
        )}
      </div>
    </li>
  );
}

function ClassCountsList({
  datasetId,
  data,
}: {
  datasetId: string;
  data: { totalDocuments: number; classCounts: Record<string, number> };
}) {
  const sorted = Object.entries(data.classCounts).sort((a, b) => b[1] - a[1]);
  const total = Math.max(1, data.totalDocuments);
  return (
    <>
      <p className="mb-2 text-[11px] text-slate-500 dark:text-slate-400 font-mono">
        {formatNumber(data.totalDocuments)} documents total
      </p>
      <ul className="space-y-1">
        {sorted.slice(0, 25).map(([cls, n]) => {
          const pct = (n / total) * 100;
          const isSummary = COMMON_CLASSES.includes(cls);
          // Route subject/element/epoch to the rich table view; the rest
          // go through the Raw Documents list (M4c renames to a toggle).
          const href = isSummary
            ? `/datasets/${datasetId}/tables/${cls}`
            : `/datasets/${datasetId}/documents?class=${encodeURIComponent(cls)}`;
          return (
            <li key={cls} className="text-xs">
              <Link
                to={href}
                className="flex items-center gap-2 hover:text-brand-600 dark:hover:text-brand-400"
              >
                <span className="font-mono truncate flex-1">{cls}</span>
                <span className="text-slate-500 dark:text-slate-400">
                  {formatNumber(n)}
                </span>
                {isSummary && <FileText className="h-3 w-3 text-slate-400" aria-hidden />}
                {!isSummary && <Globe className="h-3 w-3 text-slate-400" aria-hidden />}
              </Link>
              <div
                className="mt-0.5 h-1 rounded bg-slate-100 dark:bg-slate-800 overflow-hidden"
                role="progressbar"
                aria-label={`${cls} ${formatNumber(n)} of ${formatNumber(data.totalDocuments)}`}
              >
                <div
                  className="h-1 rounded bg-brand-500"
                  style={{ width: `${Math.max(2, pct)}%` }}
                />
              </div>
            </li>
          );
        })}
      </ul>
    </>
  );
}
