import { Link } from 'react-router-dom';
import {
  Activity,
  BookOpen,
  Brain,
  Cloud,
  Database,
  GitBranch,
  Layers,
  Scale,
  Search,
  Table,
} from 'lucide-react';

import { Card, CardBody, CardDescription, CardHeader, CardTitle } from '@/components/ui/Card';
import { Badge } from '@/components/ui/Badge';

/**
 * About page — explains the NDI data model at a glance and links into
 * the main pages for hands-on exploration.
 *
 * Layout:
 *   1. Depth-gradient hero band with eyebrow "ABOUT NDI DATA MODEL",
 *      H1 + subtitle, and the NDI emblem baked into the pattern overlay.
 *   2. Body: the 6 stacked cards (model description + feature rows +
 *      ontology chips + call-to-action), all in the centered 1200px
 *      column with the design-system color tokens.
 */
export function AboutPage() {
  return (
    <>
      {/* ── Hero band ─────────────────────────────────────────────── */}
      <section
        className="relative overflow-hidden text-white"
        style={{ background: 'var(--grad-depth)' }}
        aria-labelledby="about-hero"
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
        <div className="relative mx-auto max-w-[1200px] px-7 py-10 md:py-12">
          <div className="eyebrow mb-4">
            <span className="eyebrow-dot" aria-hidden />
            ABOUT NDI DATA MODEL
          </div>

          <h1
            id="about-hero"
            className="text-white font-display font-extrabold tracking-tight leading-tight text-[2rem] md:text-[2.25rem] mb-2"
          >
            How NDI models research data.
          </h1>

          <p className="text-white/70 text-[14.5px] leading-relaxed max-w-[620px]">
            Browse NDI Cloud datasets without downloading them. Cloud-first,
            tutorial-aligned, open source.
          </p>
        </div>
      </section>

      {/* ── Body ──────────────────────────────────────────────────── */}
      <section className="mx-auto max-w-[1200px] px-7 py-7">
        <div className="space-y-6 max-w-4xl">
          <Card>
            <CardHeader>
              <CardTitle className="text-base">The NDI data model</CardTitle>
              <CardDescription>
                Every dataset is a graph of typed <code className="font-mono">documents</code>.
                Six document classes anchor most workflows; everything else
                derives from them via <code className="font-mono">depends_on</code> edges.
              </CardDescription>
            </CardHeader>
            <CardBody className="grid gap-3 sm:grid-cols-2">
              <ModelRow
                icon={<Database className="h-4 w-4 text-ndi-teal" />}
                name="subject"
                desc="An experimental subject — one animal, cell culture, or preparation. Rich metadata arrives via its openminds_subject companion docs."
              />
              <ModelRow
                icon={<Activity className="h-4 w-4 text-ndi-teal" />}
                name="element"
                desc="A recording probe or data element (n-trode, patch electrode, camera). Optionally annotated with probe_location (UBERON / CL ontologies)."
              />
              <ModelRow
                icon={<Layers className="h-4 w-4 text-ndi-teal" />}
                name="element_epoch"
                desc="A time-bounded recording segment on a given element. Carries t0_t1 timestamps (dual-clock when synced globally) and the underlying binary file reference."
              />
              <ModelRow
                icon={<BookOpen className="h-4 w-4 text-ndi-teal" />}
                name="openminds_subject"
                desc="OpenMINDS-schema metadata per subject property: Species, Strain, BiologicalSex, GeneticStrainType, BackgroundStrain. Paired name + ontology term ID."
              />
              <ModelRow
                icon={<Brain className="h-4 w-4 text-ndi-teal" />}
                name="probe_location"
                desc="Anatomical location or cell type of a probe, keyed by UBERON (for locations) or Cell Ontology (CL) for cell types."
              />
              <ModelRow
                icon={<GitBranch className="h-4 w-4 text-ndi-teal" />}
                name="treatment"
                desc="A pharmacological / behavioral treatment applied to a subject, annotated against NDI's EMPTY: controlled vocabulary."
              />
            </CardBody>
          </Card>

          <Card>
            <CardHeader>
              <CardTitle className="text-base">What you can do here</CardTitle>
            </CardHeader>
            <CardBody className="grid gap-4 sm:grid-cols-2">
              <FeatureRow
                icon={<Cloud className="h-4 w-4 text-ndi-teal" />}
                title="Cloud-first"
                body="Every read hits the NDI Cloud indexed store. No downloads, no local mirror. Indexed classLineage + depends_on keep summary tables under 3 seconds on a warm cache."
              />
              <FeatureRow
                icon={<Table className="h-4 w-4 text-ndi-teal" />}
                title="Tutorial-parity tables"
                body="Subject / probe / epoch tables mirror the Matlab + Python tutorial columns, with clickable ontology term IDs that open definitions inline."
              />
              <FeatureRow
                icon={<Search className="h-4 w-4 text-ndi-teal" />}
                title="Cross-cloud query"
                body="Build queries with isa, depends_on, hasfield, and more — including negation. Scope across public / your org / specific datasets."
              />
              <FeatureRow
                icon={<Scale className="h-4 w-4 text-ndi-teal" />}
                title="Distribution viz"
                body="Pick any numeric column and group by a categorical one — the backend computes KDE + quartiles, the frontend renders a violin + box + jitter plot."
              />
            </CardBody>
          </Card>

          <Card>
            <CardHeader>
              <CardTitle className="text-base">Ontology providers</CardTitle>
              <CardDescription>
                Term IDs you&apos;ll see — click any in a table to open its definition.
              </CardDescription>
            </CardHeader>
            <CardBody className="flex flex-wrap gap-1.5">
              <Badge variant="secondary">NCBITaxon — species</Badge>
              <Badge variant="secondary">UBERON — anatomy</Badge>
              <Badge variant="secondary">CL — cell types</Badge>
              <Badge variant="secondary">PATO — phenotype & trait</Badge>
              <Badge variant="secondary">WBStrain — C. elegans strains</Badge>
              <Badge variant="secondary">RRID — research resources</Badge>
              <Badge variant="secondary">CHEBI — chemical entities</Badge>
              <Badge variant="secondary">PubChem — small molecules</Badge>
              <Badge variant="secondary">EFO — experimental factors</Badge>
              <Badge variant="secondary">OM — units of measure</Badge>
              <Badge variant="secondary">EMPTY — NDI controlled vocab</Badge>
            </CardBody>
          </Card>

          <Card>
            <CardHeader>
              <CardTitle className="text-base">Start exploring</CardTitle>
            </CardHeader>
            <CardBody className="flex flex-wrap gap-3">
              <Link
                to="/datasets"
                className="inline-flex items-center gap-1.5 rounded-md bg-ndi-teal px-3.5 py-1.5 text-sm font-medium text-white hover:brightness-110 transition-all"
              >
                <Database className="h-4 w-4" /> Browse datasets
              </Link>
              <Link
                to="/query"
                className="inline-flex items-center gap-1.5 rounded-md bg-white px-3.5 py-1.5 text-sm font-medium text-brand-navy ring-1 ring-border-subtle hover:bg-gray-50 hover:text-ndi-teal transition-colors"
              >
                <Search className="h-4 w-4" /> Open query builder
              </Link>
            </CardBody>
          </Card>

          <footer className="text-xs text-fg-muted">
            This browser is open source under MIT. Powered by{' '}
            <a
              href="https://ndi-cloud.com"
              className="underline hover:text-ndi-teal"
              target="_blank"
              rel="noopener noreferrer"
            >
              NDI Cloud
            </a>{' '}
            +{' '}
            <a
              href="https://github.com/VH-Lab/NDI-matlab"
              className="underline hover:text-ndi-teal"
              target="_blank"
              rel="noopener noreferrer"
            >
              NDI-matlab / NDI-python
            </a>
            .
          </footer>
        </div>
      </section>
    </>
  );
}

function ModelRow({
  icon,
  name,
  desc,
}: {
  icon: React.ReactNode;
  name: string;
  desc: string;
}) {
  return (
    <div className="flex items-start gap-2">
      <span className="mt-0.5">{icon}</span>
      <div className="space-y-0.5">
        <code className="font-mono text-xs text-brand-navy">{name}</code>
        <p className="text-xs text-fg-secondary leading-relaxed">{desc}</p>
      </div>
    </div>
  );
}

function FeatureRow({
  icon,
  title,
  body,
}: {
  icon: React.ReactNode;
  title: string;
  body: string;
}) {
  return (
    <div className="flex items-start gap-2">
      <span className="mt-0.5">{icon}</span>
      <div className="space-y-0.5">
        <p className="text-sm font-medium text-brand-navy">{title}</p>
        <p className="text-xs text-fg-secondary leading-relaxed">{body}</p>
      </div>
    </div>
  );
}
