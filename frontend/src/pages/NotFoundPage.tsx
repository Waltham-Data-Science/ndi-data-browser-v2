import { Link } from 'react-router-dom';

/**
 * 404 — depth-gradient hero with a single "back to the Data Commons"
 * call-to-action. No body section; the hero does all the work.
 */
export function NotFoundPage() {
  return (
    <section
      className="relative overflow-hidden text-white"
      style={{ background: 'var(--grad-depth)' }}
      aria-labelledby="notfound-hero"
    >
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
      <div className="relative mx-auto max-w-[1200px] px-7 py-16 md:py-20">
        <div className="eyebrow mb-4">
          <span className="eyebrow-dot" aria-hidden />
          404
        </div>

        <h1
          id="notfound-hero"
          className="text-white font-display font-extrabold tracking-tight leading-tight text-[2rem] md:text-[2.25rem] mb-2"
        >
          This page doesn&apos;t exist.
        </h1>

        <p className="text-white/70 text-[14.5px] leading-relaxed max-w-[620px] mb-6">
          Try the Data Commons instead.
        </p>

        <Link
          to="/datasets"
          className="inline-flex items-center gap-1.5 rounded-lg bg-ndi-teal px-5 py-2.5 text-[14px] font-semibold text-white hover:brightness-110 focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-brand-blue-3 transition-all"
          style={{ boxShadow: 'var(--shadow-cta)' }}
        >
          Browse datasets
        </Link>
      </div>
    </section>
  );
}
