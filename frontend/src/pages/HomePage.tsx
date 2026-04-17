/**
 * Home renders the dataset catalog directly. v1 UX: the catalog IS the
 * front door. The three-card marketing block from v0 was removed in M4b;
 * users who want the feature-intro text can see it in /about (M7).
 *
 * One-line hero preserved above the grid so the page still communicates
 * "what this is" at first paint without pushing the data below the fold.
 */
import { DatasetsPage } from './DatasetsPage';

export function HomePage() {
  return <DatasetsPage />;
}
