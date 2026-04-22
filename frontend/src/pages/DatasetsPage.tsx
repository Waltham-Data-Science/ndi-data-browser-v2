import { Search, X as XIcon } from 'lucide-react';
import { useMemo, useState, type FormEvent } from 'react';
import { useSearchParams } from 'react-router-dom';

import { usePublishedDatasets, useFacets, type DatasetRecord } from '@/api/datasets';
import { DatasetCard } from '@/components/datasets/DatasetCard';
import { ErrorState } from '@/components/errors/ErrorState';
import { Button } from '@/components/ui/Button';
import { CardSkeleton } from '@/components/ui/Skeleton';
import { cn } from '@/lib/cn';
import { formatNumber } from '@/lib/format';

const PAGE_SIZE = 20;

/** Popular search seeds shown below the hero search. Click = run search. */
const POPULAR_SEARCHES = [
  'Mus musculus',
  'V1 recordings',
  'Orientation tuning',
  'Chronic probe',
  'Van Hooser Lab',
] as const;

type SortMode = 'relevance' | 'newest' | 'oldest' | 'name';

/**
 * Public Data Commons — catalog grid + faceted filter sidebar.
 *
 * Layout (mirrors search.html mockup):
 *   1. Depth-gradient hero band — eyebrow, H1, glassmorphic search,
 *      popular chips, at-a-glance stats.
 *   2. 260px facet sidebar + results grid (collapses to single column
 *      on < md, sidebar becomes a collapsible drawer).
 *   3. Applied-filter chip row + results-info bar + sort dropdown.
 *   4. Responsive dataset card grid + pagination.
 *
 * Preserves the previous implementation's behavior:
 *   - `usePublishedDatasets(page, PAGE_SIZE)` catalog fetch
 *   - URL-persisted `?q=` and `?page=` (adds `?species=…&regions=…`)
 *   - Client-side text filter (`visible` memo) — cloud-side facet
 *     filtering is a larger change to the `/datasets/published`
 *     contract; for now we apply facet checkboxes client-side against
 *     the current page's `DatasetRecord` fields (species, brainRegions,
 *     license, organizationId).
 *
 * Facets come from `GET /api/facets` (Plan B B3) — species + brain
 * regions across every published dataset, aggregated via
 * `FacetService`. Counts in the mockup are illustrative; real counts
 * would require a second server pass per checked facet and we don't
 * yet ship that, so chips show checkbox state only (no numbers).
 */
export function DatasetsPage() {
  const [searchParams, setSearchParams] = useSearchParams();
  const page = Math.max(1, parseInt(searchParams.get('page') ?? '1', 10) || 1);
  const q = searchParams.get('q') ?? '';
  const sort = (searchParams.get('sort') as SortMode) || 'relevance';
  const speciesFilters = parseCsv(searchParams.get('species'));
  const regionFilters = parseCsv(searchParams.get('regions'));
  const licenseFilters = parseCsv(searchParams.get('license'));

  // Local search input — commits on submit.
  const [draftQ, setDraftQ] = useState(q);

  const setParam = (key: string, value: string | null) => {
    const next = new URLSearchParams(searchParams);
    if (value === null || value === '') next.delete(key);
    else next.set(key, value);
    if (key !== 'page') next.delete('page');
    setSearchParams(next, { replace: true });
  };

  const toggleFilter = (key: 'species' | 'regions' | 'license', value: string) => {
    const current = parseCsv(searchParams.get(key));
    const next = current.includes(value)
      ? current.filter((v) => v !== value)
      : [...current, value];
    setParam(key, next.length ? next.join(',') : null);
  };

  const clearAllFilters = () => {
    const next = new URLSearchParams(searchParams);
    ['species', 'regions', 'license', 'q'].forEach((k) => next.delete(k));
    next.delete('page');
    setSearchParams(next, { replace: true });
    setDraftQ('');
  };

  const handleSubmit = (e: FormEvent) => {
    e.preventDefault();
    setParam('q', draftQ.trim() || null);
  };

  const handlePopular = (term: string) => {
    setDraftQ(term);
    setParam('q', term);
  };

  const { data, isLoading, isError, error, refetch } = usePublishedDatasets(
    page,
    PAGE_SIZE,
  );
  const facets = useFacets();

  // Available license tags — derived from the current page's datasets
  // so the list is never stale against what's visible. We keep the
  // canonical neuro-friendly pair at the top when both are present.
  const licenseOptions = useMemo(() => {
    const seen = new Set<string>();
    for (const d of data?.datasets ?? []) {
      if (d.license) seen.add(d.license);
    }
    const all = Array.from(seen);
    const preferred = ['CC-BY-4.0', 'CC0-1.0', 'CC0'].filter((l) =>
      all.includes(l),
    );
    const rest = all.filter((l) => !preferred.includes(l)).sort();
    return [...preferred, ...rest];
  }, [data]);

  const visible = useMemo(() => {
    const all = data?.datasets ?? [];
    const matched = all.filter((d) => matchesFilters(d, {
      q,
      species: speciesFilters,
      regions: regionFilters,
      license: licenseFilters,
    }));
    const sorted = [...matched];
    sorted.sort(compareBy(sort));
    return sorted;
  }, [data, q, speciesFilters, regionFilters, licenseFilters, sort]);

  const total = data?.totalNumber ?? 0;
  const pageCount = total > 0 ? Math.ceil(total / PAGE_SIZE) : 1;

  const anyFilterActive =
    !!q ||
    speciesFilters.length > 0 ||
    regionFilters.length > 0 ||
    licenseFilters.length > 0;

  return (
    <>
      {/* ── Hero band (full bleed) ──────────────────────────────────── */}
      <section
        className="relative overflow-hidden text-white"
        style={{ background: 'var(--grad-depth)' }}
        aria-labelledby="datasets-hero"
      >
        {/* Pattern overlay — NDI brandmark at 5% opacity */}
        <div
          aria-hidden
          className="absolute inset-0 pointer-events-none"
          style={{
            backgroundImage: "url('/brand/ndicloud-emblem.svg')",
            backgroundSize: '120px',
            backgroundRepeat: 'repeat',
            opacity: 0.05,
          }}
        />

        <div className="relative mx-auto max-w-[1200px] px-7 py-14 md:py-16">
          <div className="eyebrow mb-4">
            <span className="eyebrow-dot" aria-hidden />
            NDI Data Commons · Open access
          </div>

          <h1
            id="datasets-hero"
            className="text-white font-display font-extrabold tracking-tight leading-[1.1] text-[2.25rem] md:text-[2.75rem] mb-3 max-w-3xl"
          >
            Discover published neuroscience datasets.
          </h1>

          <p className="text-white/70 text-[15px] leading-relaxed max-w-[640px] mb-6">
            Faceted search across every dataset on NDI Cloud. Filter by species,
            region, probe, year &mdash; every entry carries a Crossref DOI.
          </p>

          <form
            onSubmit={handleSubmit}
            className="flex gap-1.5 p-1.5 rounded-xl border border-white/18 backdrop-blur-md max-w-[720px]"
            style={{ background: 'rgba(255,255,255,0.08)' }}
            role="search"
          >
            <label htmlFor="hero-search" className="sr-only">
              Search datasets
            </label>
            <div className="flex-1 flex items-center gap-2 px-3">
              <Search className="h-4 w-4 text-white/55" aria-hidden />
              <input
                id="hero-search"
                type="search"
                value={draftQ}
                onChange={(e) => setDraftQ(e.target.value)}
                placeholder="Search species, region, probe, contributor, DOI…"
                className="flex-1 bg-transparent border-none outline-none text-white text-[15px] placeholder:text-white/50 py-2.5"
                autoComplete="off"
              />
            </div>
            <button
              type="submit"
              className="rounded-lg bg-ndi-teal px-5 py-2.5 text-[14px] font-semibold text-white hover:brightness-110 focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-brand-blue-3 transition-all"
              style={{ boxShadow: 'var(--shadow-cta)' }}
            >
              Search
            </button>
          </form>

          <div className="mt-4 flex flex-wrap gap-2 items-center text-[12.5px]">
            <span className="text-white/55 font-semibold tracking-wide">
              Popular:
            </span>
            {POPULAR_SEARCHES.map((term) => (
              <button
                key={term}
                type="button"
                onClick={() => handlePopular(term)}
                className="px-3 py-1 rounded-full text-white/85 hover:text-white hover:bg-white/15 transition-colors"
                style={{ background: 'rgba(255,255,255,0.08)' }}
              >
                {term}
              </button>
            ))}
          </div>

          <div className="mt-6 pt-5 border-t border-white/10 flex flex-wrap gap-x-10 gap-y-3 text-[11.5px] text-white/55">
            <Stat label="Published datasets" value={formatNumber(total)} />
            <Stat label="DOI coverage" value="Crossref" />
            <Stat label="Metadata standard" value="OpenMINDS" />
            <Stat label="Access" value="No login required" />
          </div>
        </div>
      </section>

      {/* ── Body: facet sidebar + results ─────────────────────────────── */}
      <section className="mx-auto max-w-[1200px] px-7 py-8">
        <div className="grid grid-cols-1 md:grid-cols-[260px_1fr] gap-6">
          {/* Filter sidebar */}
          <FacetSidebar
            species={(facets.data?.species ?? []).map((t) => t.label)}
            regions={(facets.data?.brainRegions ?? []).map((t) => t.label)}
            licenses={licenseOptions}
            activeSpecies={speciesFilters}
            activeRegions={regionFilters}
            activeLicenses={licenseFilters}
            onToggleSpecies={(v) => toggleFilter('species', v)}
            onToggleRegion={(v) => toggleFilter('regions', v)}
            onToggleLicense={(v) => toggleFilter('license', v)}
            loading={facets.isLoading}
          />

          {/* Main results column */}
          <div className="min-w-0">
            {/* Results info bar + sort */}
            <div
              className="flex flex-wrap items-center justify-between gap-3 rounded-md bg-white border border-border-subtle px-4 py-2.5 mb-3"
              style={{ boxShadow: 'var(--shadow-xs)' }}
            >
              <span className="text-[13.5px] text-fg-secondary">
                {isLoading ? (
                  'Loading…'
                ) : anyFilterActive ? (
                  <>
                    <strong className="text-brand-navy font-semibold">
                      {visible.length}
                    </strong>{' '}
                    of {formatNumber(total)} dataset{total === 1 ? '' : 's'}
                    {q && (
                      <>
                        {' '}matching{' '}
                        <em className="text-brand-navy not-italic font-medium">
                          &ldquo;{q}&rdquo;
                        </em>
                      </>
                    )}
                  </>
                ) : (
                  <>
                    <strong className="text-brand-navy font-semibold">
                      {formatNumber(total)}
                    </strong>{' '}
                    datasets &middot; page {page} of {pageCount}
                  </>
                )}
              </span>
              <label className="flex items-center gap-2 text-[12.5px] text-fg-muted">
                <span className="uppercase tracking-wide text-[10.5px] font-semibold">
                  Sort
                </span>
                <select
                  value={sort}
                  onChange={(e) => setParam('sort', e.target.value)}
                  className="bg-white border border-border-subtle rounded-md px-2 py-1 text-[12.5px] text-fg-primary hover:border-border-strong focus:outline-none focus:ring-2 focus:ring-ndi-teal/40"
                >
                  <option value="relevance">Most relevant</option>
                  <option value="newest">Newest</option>
                  <option value="oldest">Oldest</option>
                  <option value="name">Title (A–Z)</option>
                </select>
              </label>
            </div>

            {/* Applied filter chips */}
            {anyFilterActive && (
              <div className="flex flex-wrap gap-1.5 mb-4">
                {q && (
                  <FilterChip
                    label={`“${q}”`}
                    onRemove={() => {
                      setDraftQ('');
                      setParam('q', null);
                    }}
                  />
                )}
                {speciesFilters.map((v) => (
                  <FilterChip
                    key={`s-${v}`}
                    label={v}
                    onRemove={() => toggleFilter('species', v)}
                  />
                ))}
                {regionFilters.map((v) => (
                  <FilterChip
                    key={`r-${v}`}
                    label={v}
                    onRemove={() => toggleFilter('regions', v)}
                  />
                ))}
                {licenseFilters.map((v) => (
                  <FilterChip
                    key={`l-${v}`}
                    label={v}
                    onRemove={() => toggleFilter('license', v)}
                  />
                ))}
                <button
                  type="button"
                  className="text-[12px] text-fg-muted hover:text-fg-secondary underline underline-offset-2 ml-1"
                  onClick={clearAllFilters}
                >
                  Clear all
                </button>
              </div>
            )}

            {/* Loading */}
            {isLoading && (
              <div className="grid gap-3 sm:grid-cols-1 lg:grid-cols-2">
                {Array.from({ length: 6 }).map((_, i) => (
                  <CardSkeleton key={i} />
                ))}
              </div>
            )}

            {isError && <ErrorState error={error} onRetry={() => refetch()} />}

            {!isLoading && !isError && visible.length === 0 && (
              <div className="rounded-lg border border-dashed border-border-subtle bg-white p-10 text-center">
                <p className="text-[14px] text-fg-secondary">
                  {anyFilterActive
                    ? 'No datasets match the current filters.'
                    : 'No published datasets yet.'}
                </p>
                {anyFilterActive && (
                  <Button
                    variant="ghost"
                    size="sm"
                    className="mt-3"
                    onClick={clearAllFilters}
                  >
                    Clear filters
                  </Button>
                )}
              </div>
            )}

            {!isLoading && visible.length > 0 && (
              <div className="grid gap-3 sm:grid-cols-1 lg:grid-cols-2">
                {visible.map((d) => (
                  <DatasetCard key={d.id} dataset={d} />
                ))}
              </div>
            )}

            {!isLoading && pageCount > 1 && (
              <nav
                className="flex items-center justify-center gap-3 pt-8"
                aria-label="Pagination"
              >
                <Button
                  variant="secondary"
                  size="sm"
                  disabled={page === 1}
                  onClick={() => setParam('page', String(page - 1))}
                >
                  Previous
                </Button>
                <span className="text-[13px] text-fg-muted font-mono">
                  Page {page} of {pageCount}
                </span>
                <Button
                  variant="secondary"
                  size="sm"
                  disabled={!data || page >= pageCount}
                  onClick={() => setParam('page', String(page + 1))}
                >
                  Next
                </Button>
              </nav>
            )}
          </div>
        </div>
      </section>
    </>
  );
}

function Stat({ label, value }: { label: string; value: string | number }) {
  return (
    <div className="flex flex-col">
      <strong className="text-white font-display font-bold text-[17px] tracking-tight leading-none mb-1">
        {value}
      </strong>
      <span className="uppercase tracking-wider">{label}</span>
    </div>
  );
}

function FilterChip({ label, onRemove }: { label: string; onRemove: () => void }) {
  return (
    <span className="inline-flex items-center gap-1.5 text-[12px] font-medium pl-2.5 pr-1.5 py-1 rounded-md bg-ndi-teal-light text-ndi-teal ring-1 ring-inset ring-ndi-teal-border">
      <span>{label}</span>
      <button
        type="button"
        aria-label={`Remove filter ${label}`}
        className="inline-flex items-center justify-center h-4 w-4 rounded-sm hover:bg-ndi-teal/15"
        onClick={onRemove}
      >
        <XIcon className="h-3 w-3" aria-hidden />
      </button>
    </span>
  );
}

function FacetSidebar({
  species,
  regions,
  licenses,
  activeSpecies,
  activeRegions,
  activeLicenses,
  onToggleSpecies,
  onToggleRegion,
  onToggleLicense,
  loading,
}: {
  species: string[];
  regions: string[];
  licenses: string[];
  activeSpecies: string[];
  activeRegions: string[];
  activeLicenses: string[];
  onToggleSpecies: (v: string) => void;
  onToggleRegion: (v: string) => void;
  onToggleLicense: (v: string) => void;
  loading: boolean;
}) {
  const [open, setOpen] = useState(false);
  const hasAny = species.length + regions.length + licenses.length > 0;

  return (
    <>
      {/* Mobile toggle */}
      <div className="md:hidden">
        <Button
          variant="secondary"
          size="sm"
          onClick={() => setOpen((v) => !v)}
          className="w-full justify-center"
        >
          {open ? 'Hide filters' : 'Show filters'}
        </Button>
      </div>

      <aside
        className={cn(
          'md:sticky md:top-20 md:self-start md:block space-y-3',
          open ? 'block' : 'hidden md:block',
        )}
        aria-label="Dataset filters"
      >
        <FacetGroup
          title="Species"
          options={species}
          active={activeSpecies}
          onToggle={onToggleSpecies}
          loading={loading}
          emptyHint="No species aggregated yet."
        />
        <FacetGroup
          title="Brain region"
          options={regions}
          active={activeRegions}
          onToggle={onToggleRegion}
          loading={loading}
          emptyHint="No regions aggregated yet."
        />
        <FacetGroup
          title="License"
          options={licenses}
          active={activeLicenses}
          onToggle={onToggleLicense}
          loading={false}
          emptyHint="No licenses on current page."
        />
        {!loading && !hasAny && (
          <p className="text-[11.5px] text-fg-muted px-1">
            Facets will appear here once the first datasets index.
          </p>
        )}
      </aside>
    </>
  );
}

function FacetGroup({
  title,
  options,
  active,
  onToggle,
  loading,
  emptyHint,
}: {
  title: string;
  options: string[];
  active: string[];
  onToggle: (v: string) => void;
  loading: boolean;
  emptyHint: string;
}) {
  return (
    <div className="bg-white rounded-xl border border-border-subtle p-4">
      <h5 className="text-[11px] font-bold tracking-[0.1em] uppercase text-fg-muted mb-3">
        {title}
      </h5>
      {loading ? (
        <div className="space-y-2">
          {Array.from({ length: 4 }).map((_, i) => (
            <div key={i} className="h-4 bg-bg-muted rounded animate-pulse" />
          ))}
        </div>
      ) : options.length === 0 ? (
        <p className="text-[11.5px] text-fg-muted">{emptyHint}</p>
      ) : (
        <ul className="space-y-1">
          {options.slice(0, 24).map((opt) => {
            const checked = active.includes(opt);
            return (
              <li key={opt}>
                <label
                  className={cn(
                    'flex items-center gap-2 py-1 text-[13px] cursor-pointer rounded-md px-1',
                    checked ? 'text-ndi-teal font-medium' : 'text-fg-secondary hover:text-brand-navy',
                  )}
                >
                  <input
                    type="checkbox"
                    checked={checked}
                    onChange={() => onToggle(opt)}
                    className="accent-[var(--ndi-teal)] h-3.5 w-3.5 m-0"
                  />
                  <span className="truncate" title={opt}>
                    {opt}
                  </span>
                </label>
              </li>
            );
          })}
          {options.length > 24 && (
            <li className="text-[11px] text-fg-muted px-1 pt-1">
              + {options.length - 24} more (refine with search)
            </li>
          )}
        </ul>
      )}
    </div>
  );
}

// ── Helpers ──────────────────────────────────────────────────────────

function parseCsv(v: string | null): string[] {
  if (!v) return [];
  return v.split(',').map((s) => s.trim()).filter(Boolean);
}

interface MatchCriteria {
  q: string;
  species: string[];
  regions: string[];
  license: string[];
}

function matchesFilters(d: DatasetRecord, c: MatchCriteria): boolean {
  // Text search across user-visible strings.
  if (c.q.trim()) {
    const needle = c.q.toLowerCase();
    const hay = [
      d.name,
      d.abstract,
      d.description,
      d.doi,
      d.pubMedId,
      d.species,
      d.brainRegions,
      ...(d.contributors?.map((x) => `${x.firstName ?? ''} ${x.lastName ?? ''}`) ?? []),
    ]
      .filter(Boolean)
      .join(' ')
      .toLowerCase();
    if (!hay.includes(needle)) return false;
  }
  // Facet checkboxes — match against the record's comma-separated
  // fields OR the synthesizer-backed summary pills (whichever is
  // populated). At least one value in the active set must appear.
  if (c.species.length) {
    if (!anyMatch(c.species, datasetSpeciesValues(d))) return false;
  }
  if (c.regions.length) {
    if (!anyMatch(c.regions, datasetRegionValues(d))) return false;
  }
  if (c.license.length) {
    if (!d.license || !c.license.includes(d.license)) return false;
  }
  return true;
}

function datasetSpeciesValues(d: DatasetRecord): string[] {
  const raw = (d.species ?? '').split(',').map((s) => s.trim()).filter(Boolean);
  const summary = (d.summary?.species ?? []).map((s) => s.label);
  return [...raw, ...summary];
}

function datasetRegionValues(d: DatasetRecord): string[] {
  const raw = (d.brainRegions ?? '').split(',').map((s) => s.trim()).filter(Boolean);
  const summary = (d.summary?.brainRegions ?? []).map((s) => s.label);
  return [...raw, ...summary];
}

function anyMatch(needles: string[], hay: string[]): boolean {
  const lower = hay.map((h) => h.toLowerCase());
  return needles.some((n) => lower.some((h) => h === n.toLowerCase() || h.includes(n.toLowerCase())));
}

function compareBy(sort: SortMode) {
  return (a: DatasetRecord, b: DatasetRecord) => {
    switch (sort) {
      case 'newest':
        return dateOf(b) - dateOf(a);
      case 'oldest':
        return dateOf(a) - dateOf(b);
      case 'name':
        return (a.name ?? '').localeCompare(b.name ?? '');
      case 'relevance':
      default:
        return 0;
    }
  };
}

function dateOf(d: DatasetRecord): number {
  const s = d.uploadedAt || d.updatedAt || d.createdAt;
  return s ? new Date(s).getTime() : 0;
}
