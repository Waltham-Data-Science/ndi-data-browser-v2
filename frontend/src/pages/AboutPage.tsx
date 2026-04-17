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
 * the main pages for hands-on exploration. Ported from v1 (187 LOC) with
 * v2 NDICloud branding and references to v2's actual feature set.
 */
export function AboutPage() {
  return (
    <div className="space-y-6 max-w-4xl">
      <header className="flex items-center gap-4">
        <img
          src="/brand/ndicloud-emblem.svg"
          alt=""
          aria-hidden="true"
          className="h-14 w-auto"
        />
        <div>
          <h1 className="text-2xl font-bold text-slate-900 dark:text-slate-100">
            About NDICloud Data Browser
          </h1>
          <p className="text-sm text-slate-600 dark:text-slate-400">
            Browse NDI Cloud datasets without downloading them. Cloud-first,
            tutorial-aligned, open source.
          </p>
        </div>
      </header>

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
            icon={<Database className="h-4 w-4 text-brand-500" />}
            name="subject"
            desc="An experimental subject — one animal, cell culture, or preparation. Rich metadata arrives via its openminds_subject companion docs."
          />
          <ModelRow
            icon={<Activity className="h-4 w-4 text-brand-500" />}
            name="element"
            desc="A recording probe or data element (n-trode, patch electrode, camera). Optionally annotated with probe_location (UBERON / CL ontologies)."
          />
          <ModelRow
            icon={<Layers className="h-4 w-4 text-brand-500" />}
            name="element_epoch"
            desc="A time-bounded recording segment on a given element. Carries t0_t1 timestamps (dual-clock when synced globally) and the underlying binary file reference."
          />
          <ModelRow
            icon={<BookOpen className="h-4 w-4 text-brand-500" />}
            name="openminds_subject"
            desc="OpenMINDS-schema metadata per subject property: Species, Strain, BiologicalSex, GeneticStrainType, BackgroundStrain. Paired name + ontology term ID."
          />
          <ModelRow
            icon={<Brain className="h-4 w-4 text-brand-500" />}
            name="probe_location"
            desc="Anatomical location or cell type of a probe, keyed by UBERON (for locations) or Cell Ontology (CL) for cell types."
          />
          <ModelRow
            icon={<GitBranch className="h-4 w-4 text-brand-500" />}
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
            icon={<Cloud className="h-4 w-4 text-brand-500" />}
            title="Cloud-first"
            body="Every read hits the NDI Cloud indexed store. No downloads, no local mirror. Indexed classLineage + depends_on keep summary tables under 3 seconds on a warm cache."
          />
          <FeatureRow
            icon={<Table className="h-4 w-4 text-brand-500" />}
            title="Tutorial-parity tables"
            body="Subject / probe / epoch tables mirror the Matlab + Python tutorial columns, with clickable ontology term IDs that open definitions inline."
          />
          <FeatureRow
            icon={<Search className="h-4 w-4 text-brand-500" />}
            title="Cross-cloud query"
            body="Build queries with isa, depends_on, hasfield, and more — including negation. Scope across public / your org / specific datasets."
          />
          <FeatureRow
            icon={<Scale className="h-4 w-4 text-brand-500" />}
            title="Distribution viz"
            body="Pick any numeric column and group by a categorical one — the backend computes KDE + quartiles, the frontend renders a violin + box + jitter plot."
          />
        </CardBody>
      </Card>

      <Card>
        <CardHeader>
          <CardTitle className="text-base">Ontology providers</CardTitle>
          <CardDescription>
            Term IDs you'll see — click any in a table to open its definition.
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
            className="inline-flex items-center gap-1.5 rounded-md bg-brand-500 px-3.5 py-1.5 text-sm font-medium text-white hover:bg-brand-600"
          >
            <Database className="h-4 w-4" /> Browse datasets
          </Link>
          <Link
            to="/query"
            className="inline-flex items-center gap-1.5 rounded-md bg-white px-3.5 py-1.5 text-sm font-medium text-slate-900 ring-1 ring-slate-300 hover:bg-slate-50 dark:bg-slate-800 dark:text-slate-100 dark:ring-slate-700"
          >
            <Search className="h-4 w-4" /> Open query builder
          </Link>
        </CardBody>
      </Card>

      <footer className="text-xs text-slate-500 dark:text-slate-400">
        This browser is open source under MIT. Powered by{' '}
        <a
          href="https://ndi-cloud.com"
          className="underline hover:text-brand-600"
          target="_blank"
          rel="noopener noreferrer"
        >
          NDICloud
        </a>{' '}
        +{' '}
        <a
          href="https://github.com/VH-Lab/NDI-matlab"
          className="underline hover:text-brand-600"
          target="_blank"
          rel="noopener noreferrer"
        >
          NDI-matlab / NDI-python
        </a>
        .
      </footer>
    </div>
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
        <code className="font-mono text-xs text-slate-900 dark:text-slate-100">{name}</code>
        <p className="text-xs text-slate-600 dark:text-slate-400 leading-relaxed">{desc}</p>
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
        <p className="text-sm font-medium text-slate-900 dark:text-slate-100">{title}</p>
        <p className="text-xs text-slate-600 dark:text-slate-400 leading-relaxed">{body}</p>
      </div>
    </div>
  );
}
