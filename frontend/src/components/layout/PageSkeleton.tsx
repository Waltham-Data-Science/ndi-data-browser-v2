/**
 * Lazy-route fallback (audit 2026-04-23, #52).
 *
 * Rendered while React.lazy is loading a route chunk. Matches the
 * approximate hero-band geometry every page renders so the handoff
 * from skeleton → hydrated page shifts layout as little as possible.
 *
 * Uses `aria-hidden` because this is a pure visual placeholder — the
 * loading state's semantic meaning is communicated by the route
 * transition itself. Screen readers announce the new page via
 * useDocumentTitle + the page's h1.
 */
export function PageSkeleton() {
  return (
    <div aria-hidden="true" className="w-full">
      <div
        className="relative mx-auto"
        style={{
          background: 'var(--grad-depth)',
          minHeight: '260px',
        }}
      >
        <div className="mx-auto max-w-[1200px] px-6 pt-14 pb-8">
          <div className="skeleton h-5 w-28 rounded" />
          <div className="skeleton mt-5 h-9 w-3/5 rounded" />
          <div className="skeleton mt-3 h-4 w-2/5 rounded" />
        </div>
      </div>
      <div className="mx-auto max-w-[1200px] px-6 py-10 space-y-4">
        <div className="skeleton h-6 w-1/3 rounded" />
        <div className="skeleton h-4 w-5/6 rounded" />
        <div className="skeleton h-4 w-4/5 rounded" />
        <div className="skeleton h-4 w-3/4 rounded" />
      </div>
    </div>
  );
}
