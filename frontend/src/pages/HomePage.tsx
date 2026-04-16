import { Link } from 'react-router-dom';
import { Database, Search, Zap } from 'lucide-react';
import { Button } from '@/components/ui/Button';
import { Card, CardBody } from '@/components/ui/Card';

export function HomePage() {
  return (
    <div className="space-y-8">
      <section className="rounded-xl bg-gradient-to-br from-brand-navy to-brand-500 p-8 text-white shadow-sm">
        <h1 className="text-3xl font-bold sm:text-4xl">Explore NDI Cloud neuroscience datasets</h1>
        <p className="mt-2 max-w-2xl text-slate-100">
          Browse published datasets, run indexed cross-cloud queries, and inspect subjects, probes,
          and epochs without downloading a thing.
        </p>
        <div className="mt-6 flex flex-wrap gap-2">
          <Link to="/datasets">
            <Button variant="secondary" className="text-slate-900">Browse datasets</Button>
          </Link>
          <Link to="/query">
            <Button variant="ghost" className="text-white hover:bg-white/10">Open query builder</Button>
          </Link>
        </div>
      </section>

      <section className="grid gap-4 sm:grid-cols-3">
        <Feature
          icon={<Database className="h-5 w-5 text-brand-500" />}
          title="Cloud-first"
          body="Every query hits indexed cloud endpoints. No waiting on downloads."
        />
        <Feature
          icon={<Zap className="h-5 w-5 text-brand-500" />}
          title="Fast summary tables"
          body="Subjects, probes, epochs, combined joins — built with chained ndiquery + bulk-fetch."
        />
        <Feature
          icon={<Search className="h-5 w-5 text-brand-500" />}
          title="Cross-cloud search"
          body="Find where any document is referenced, across every dataset you can access."
        />
      </section>
    </div>
  );
}

function Feature({ icon, title, body }: { icon: React.ReactNode; title: string; body: string }) {
  return (
    <Card>
      <CardBody className="space-y-2">
        {icon}
        <h3 className="font-semibold text-slate-900 dark:text-slate-100">{title}</h3>
        <p className="text-sm text-slate-600 dark:text-slate-300">{body}</p>
      </CardBody>
    </Card>
  );
}
