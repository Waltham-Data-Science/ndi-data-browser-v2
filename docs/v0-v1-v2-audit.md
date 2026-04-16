# v0 / v1 / v2 Visual + Feature Audit

Dated 2026-04-16. Purpose: before building the rest of M4–M7, map what v2 is missing compared to the rich v1 frontend (`ndi-data-browser`) and against the tutorial specs (`NDI-matlab` and `NDI-python` tutorial renders) that drove v1's design.

## The three frontends

| | v0 | v1 | v2 |
|---|---|---|---|
| Location | First v2 local preview (initial commit `9f0eba2`, pre-deploy) | `ndi-data-browser` repo, live at `ndi-data-browser-production.up.railway.app` | `ndi-data-browser-v2` repo, live at `ndb-v2-production.up.railway.app` |
| Backend | v2 cloud-first (no SQLite) | v1 SQLite-download + old-backend workarounds | v2 cloud-first (no SQLite) |
| Frontend architecture | Same as v2 | React + shadcn + TanStack Table/Virtual + uPlot + d3 | React + Tailwind + TanStack Query |
| Total feature LOC (pages + feature components) | ~914 pages + ~511 comp = **~1,425** | ~1,347 pages + ~2,717 comp = **~4,064** | Same as v0 |
| Status | Snapshot — UI equivalent to v2 today | Kept alive during cutover | The build target going forward |

**Conclusion:** v0 and v2 render the same pages — the only differences since the initial commit are SPA-fallback + lint fixes. The real gap is **v2 vs v1**, and that gap is substantial (~2.6× more feature code in v1).

## Tutorial ground truth (what the UI needs to support)

The tutorials that drove v1's feature choices:

### NDI-matlab tutorial series (`analyzing_first_physiology_experiment/`)

| Tutorial | Key visualization | Status in v2 |
|---|---|---|
| 2.1 | Raw voltage trace with **numbered stimulus markers overlaid** (pan/zoom) | Missing — v2 `TimeseriesChart` is 40 LOC stub |
| 2.3 | Spike waveform overlay, spike-time markers on trace, cluster-coloured spikes | Missing |
| 2.4 | **6-panel tuning-curve grid** (2 neurons × {mean, F1, F2}, each with angle vs response + fit + error bars) | Missing |
| 2.5 | **Database dependency graph** (visual tree of document deps) | Backend: indexed `depends_on` ready. Frontend: missing — v1 has `DependencyGraph.tsx` (333 LOC) |

### NDI-python tutorial HTMLs

**`tutorial_67f723d574f5f79c6062389d.html`** — Dabrowska rat electrophysiology + optogenetics:
- View subject summary table (22 rows × 14 cols incl. strain, species, sex, treatment, optogenetic target — all with ontology term IDs)
- Filter subjects by strain
- View probe summary table (22 × 9: probe type, location ontology UBERON, cell type ontology)
- View epoch summary table (22 × 8: approach/mixture ontologies)
- Combined summary table + epoch filtering by approach / mixture / cell type
- Plot electrophysiology: Select subject → probes → epoch → read timeseries → **plot Vm and I traces (dual trace)**
- Plot Elevated Plus Maze (EPM) data by treatment group (grouped bar/box)
- Plot Fear-Potentiated Startle: avg startle amplitude by phase, cued vs non-cued fear %, cued fear by treatment group

**`tutorial_682e7772cdf3f24938176fac.html`** — Haley C. elegans behavior + E. coli imaging:
- View NDI file types (doc class counts table)
- **View ontology term definitions** (click term → definition popover/panel)
- Subject summary table (22 × 15 including C. elegans strain WBStrain ontologies)
- Filter subjects
- Bacterial plate summary tables
- Retrieve subject behavior
- **Get position of subject over time** (x/y trajectory)
- Get associated video and image metadata
- **Plot an image/mask with subject position overlaid** (scientific image + trajectory layer)
- **Play video of the subject** (with time-synced position)
- Get distance to patch edge over time
- **Plot distance to nearest patch edge** (timeseries)
- Get analysis of patch encounters (behavioral event analysis)
- E. coli microscopy images + masks (fluorescence overlays)

### Tables observed in tutorial outputs

Every major table has **10–15 columns**, with every ontology column rendered as a clickable term ID that opens a definition popover. Example column sets:

| Subjects | Probes | Epochs |
|---|---|---|
| `SubjectIdentifier` | `ProbeDocumentIdentifier` | `EpochNumber` |
| `SubjectLocalIdentifier` | `ProbeName` | `EpochDocumentIdentifier` |
| `SubjectDocumentIdentifier` | `ProbeType` | `ProbeDocumentIdentifier` |
| `SessionDocumentIdentifier` | `ProbeReference` | `SubjectDocumentIdentifier` |
| `StrainName` / `StrainOntology` | `ProbeLocationName` / `ProbeLocationOntology` | `MixtureName` / `MixtureOntology` |
| `GeneticStrainTypeName` | `CellTypeName` / `CellTypeOntology` | `ApproachName` / `ApproachOntology` |
| `SpeciesName` / `SpeciesOntology` | | |
| `BackgroundStrainName` / `BackgroundStrainOntology` | | |
| `BiologicalSexName` / `BiologicalSexOntology` | | |
| Treatment-specific fields (FoodRestriction, OptogeneticStim, etc.) | | |

v1 has all these columns defined in `frontend/src/data/table-column-definitions.ts` (325 LOC, 64 entries across 7 table types, 5 ontology providers). **v2 has no equivalent.**

## What v2 is currently missing (prioritized)

### Tier 1 — blocks tutorial parity

These are the things a user following a tutorial literally cannot do in v2:

| # | Feature | Where it lives in v1 | Tutorial reference |
|---|---|---|---|
| T1.1 | Rich summary tables with 10–15+ tutorial-aligned columns | `SummaryTableView.tsx` (363) + `table-column-definitions.ts` (325) | Every tutorial |
| T1.2 | `OntologyPopover` on every term ID (UBERON, NCBITaxon, WBStrain, PATO, EMPTY, RRID, CL, OM, CHEBI, EFO, PubChem, NDIC, NCIm) | `OntologyPopover.tsx` (76) | Haley "View ontology term definitions" |
| T1.3 | Column info tooltips (ℹ️ hover on header → full description) | `SummaryTableView.tsx` | All tutorials |
| T1.4 | Table filters (per-column + global search) + auto-hide empty columns + column picker + CSV export | `SummaryTableView.tsx` | "Filter subjects by strain", "Filter epochs by approach" |
| T1.5 | Combined table (subject ⋈ probe ⋈ epoch, fully joined with all metadata) | `SummaryTableView` + backend service | "Combined summary table and epoch filtering" |
| T1.6 | Document class count table (all doc types + counts, clickable to drill in) | `DocumentTypeSelector.tsx` (42) | "View NDI file types" |
| T1.7 | Unified `DataPanel` for timeseries/image/video/fitcurve | `DataPanel.tsx` (82) + 5 subcomponents | "Plot Vm and I traces", "Plot image/mask" |
| T1.8 | Proper `TimeseriesChart` (uPlot config, axes, units, cursor) | `TimeseriesChart.tsx` (286) | "Plot Vm and I traces", "Plot distance..." |
| T1.9 | `ImageViewer` (scientific PNG/TIFF, frame stepper for multi-frame, zoom, value-under-cursor) | `ImageViewer.tsx` (125) | "Plot image/mask with subject position" |
| T1.10 | `VideoPlayer` (signed cloud URL, scrub bar, keep aspect) | `VideoPlayer.tsx` (38) | "Play video of the subject" |

### Tier 2 — rich dataset UX

| # | Feature | v1 LOC | Notes |
|---|---|---|---|
| T2.1 | Dataset detail richness: ORCID-linked contributors, funding, DOI/PubMed/Pub links, associated publications, abstract, related datasets, branching info | `DatasetDetailPage.tsx` (443) | v2's sidebar has ~half of these |
| T2.2 | Top-level Summary Tables / Raw Documents toggle | `DocumentExplorerPage.tsx` (465) | v2 mixes both |
| T2.3 | Tab icons (lucide-react) | `DocumentExplorerPage.tsx` | v2 is text-only |
| T2.4 | Loading states that surface document counts being processed | `DocumentExplorerPage.tsx` | v2 shows generic skeleton |
| T2.5 | Distribution visualization as `ViolinPlot` grouped by treatment/approach/strain | `ViolinPlot.tsx` (206) with D3 KDE | Backend computes; no frontend |
| T2.6 | Dependency graph (visual + text view) for a document | `DependencyGraph.tsx` (333) | Backend indexed; no frontend |
| T2.7 | AboutPage explaining NDI model + attribution | `AboutPage.tsx` (187) | v2 has none |
| T2.8 | Rich `DatasetCard` for the list (abstract, doc count, contributors, badges) | `DatasetCard.tsx` (78) | v2 has inline simpler cards |
| T2.9 | Dedicated `DatasetSearch` component | `DatasetSearch.tsx` (21) | v2 has inline search |
| T2.10 | Enhanced `QueryBuilder` with quick-class shortcuts, operation descriptions from API, simple-vs-advanced toggle | `QueryBuilder.tsx` (280) | v2 has 184 LOC builder |

### Tier 3 — plot types the tutorials render (what "done" looks like)

Inspecting the embedded PNGs from the Matlab-rendered tutorial HTMLs (canonical source at `https://ndi-cloud-tutorials.s3.us-east-2.amazonaws.com/tutorial_*.html`, local copies under `NDI-matlab/src/ndi/+ndi/+setup/+conv/{haley,dabrowska}/tutorial_*.html`) reveals the concrete plot conventions v2 must match:

| # | Plot type | Rendering details | Tutorial |
|---|---|---|---|
| T3.1 | Stimulus-marker overlay on timeseries | Numbered labels (1,2,...) with horizontal black bars above the voltage trace, indicating stim IDs and durations | matlab 2.1 |
| T3.2 | Tuning-curve grid | 2×3 subplot (2 cells × {mean, F1, F2}), angle vs response, points with error bars + fitted curve, dashed baseline | matlab 2.4 |
| T3.3 | Scientific image + colorbar + metadata title | Grayscale microscopy image, vertical colorbar with units (e.g. intensity 0–28000), multi-line title with experiment params ("target OD600 at seeding = 10, growth time at room temp = 01:00:50") | Haley "Plot an image or mask" |
| T3.4 | Image + trajectory overlay (simple) | Scientific image background with red path traced over it; time indicator ("time = 00:59:57") | Haley "Plot image/mask with subject position" |
| T3.5 | Image + trajectory overlay with landmarks + time-gradient color | Dark background; white circles = landmarks (food patches in grid); multi-colored trajectory lines where color = time (blue→green→yellow→orange→red) showing subject path over time | Haley same section |
| T3.6 | Stacked dual timeseries with shared x-axis | Top panel: distance to patch edge (pixels) vs time (color gradient blue→red as time proceeds). Bottom panel: closest patch # (step function) vs time. Zero-reference line on top panel | Haley "Plot distance to nearest patch edge" |
| T3.7 | Multi-trace timeseries with sweep colorbar | Many voltage traces overlaid on single axis, colored by injected current, vertical colorbar mapping current (pA) to color via rainbow/viridis | Dabrowska "Plot electrophysiology" |
| T3.8 | Spike-waveform overlay + cluster colouring | Each detected spike as a polyline in time-voltage, colored by cluster assignment; summary stats in title | matlab 2.3 |
| T3.9 | Violin + scatter + box-whisker combo, grouped | Two violin shapes side-by-side (e.g. Saline vs CNO treatment), with individual data points scattered inside, plus a thin vertical box-whisker (median + IQR) inside each violin. Same color per group | Dabrowska "Plot EPM data", "Plot Fear-Potentiated Startle" |
| T3.10 | Patch encounter timeline | Table of events + timeline overlay showing when each encounter began/ended | Haley "Get analysis of patch encounters" |

Concrete rendering requirements this implies for v2:
- **Colorbar component** with configurable min/max, unit label, and colormap (rainbow/viridis/grayscale)
- **Multi-trace timeseries** (currently `TimeseriesChart` renders single-channel only)
- **Overlay layer system** on the image viewer — pass trajectory points or masks as SVG layered on top of the raster
- **Time-gradient color mapping** for trajectories (rainbow over time window)
- **Stacked-axis chart** with shared x and synchronized cursor
- **Multi-line plot title** rendering experiment parameters (not just the doc name)
- **Violin/box combo renderer** — v1 has this, just needs to handle grouped categorical x like Saline vs CNO

### Tier 4 — UX polish

| # | Feature |
|---|---|
| T4.1 | Real NDI Cloud brand footer ("Powered by NDICloud" like v1, replace "Waltham Data Science") |
| T4.2 | NDI Cloud logo/wordmark in header (v1 uses the hexagon + NDICloud type, v2 uses a stock database icon) |
| T4.3 | Breadcrumb links ("← Back to dataset" instead of nav pills) |
| T4.4 | Skeleton shapes that match final layout (not uniform rectangles) |
| T4.5 | Keyboard navigation + focus management across tabs and tables |
| T4.6 | Deep-linkable table filter state (URL query params) |
| T4.7 | Dark mode parity (v2 has partial dark classes) |

## Visual-style gaps

Based on screenshots at viewport 1440×900:

**v1 home** — directly shows 8 dataset cards in a grid with abstracts + contributor names + doc counts + strain/size badges. "Published Datasets" header, search box, "Powered by NDICloud" footer. High information density.

**v2 home** — marketing hero ("Explore NDI Cloud neuroscience datasets") + 3 feature cards ("Cloud-first", "Fast summary tables", "Cross-cloud search") + CTAs. Zero actual data on first view. User must click to see datasets.

**v1 dataset detail** — rich right sidebar with docs/subjects/size/created/updated + DOI + "Not cached" status + Download button + Explore Documents CTA; below: Document Types table, Associated Publications, Contributors with ORCID, Corresponding Authors, Funding.

**v2 dataset detail** — left sidebar with name + abstract + contributors (plain text, no ORCID) + funding + pubMed + dates; right main area with tabs (text-only, no icons) and a class-counts bar chart.

**v1 document explorer** — top: dataset title + doc count + cached status; right: Summary Tables / Raw Documents toggle; bottom tabs with icons (Combined | Subjects | Probes | Epochs | Elements | Ontology | Treatment | OpenMINDS). Progress-aware loading states.

**v2 documents** — tabs for Subjects | Elements | Epochs | Combined | Treatments | All documents (text only, fewer options). Working but minimal.

## The rewrite decision

The gap is wide enough that piecemeal patches will add churn. Recommend a **guided rewrite** of the user-facing pages, keeping v2's backend + architectural wins (Redis sessions, error catalog, observability, stateless cloud-first) and porting v1's rich interaction patterns verbatim where they exist.

### What stays

- Backend services (dataset, document, summary-table, query, binary, ontology, visualize)
- Auth layer (Redis sessions, CSRF, rate limiter)
- Error catalog + typed client mapper
- API client (`frontend/src/api/*`) — the shape is right
- Dockerfile + railway.toml + CI
- `AppShell` skeleton, Router setup

### What gets rewritten

| v2 file | Replacement |
|---|---|
| `pages/HomePage.tsx` | Merge with `DatasetsPage` — home IS the dataset catalog (matches v1 UX) |
| `pages/DatasetsPage.tsx` | Richer `DatasetCard`, `DatasetSearch` components |
| `pages/DatasetDetailPage.tsx` | Full-metadata sidebar + class-counts + Explore Documents CTA |
| `pages/DocumentsListPage.tsx` | New `DocumentExplorerPage` with Summary Tables / Raw Documents toggle + rich tab bar w/ icons |
| `pages/TableTab.tsx` | New `SummaryTableView` (TanStack Table + virtualization + filters + column picker + CSV + ontology popovers + column info tooltips) |
| `pages/DocumentDetailPage.tsx` | Full `DocumentDetail` with JSON view + `DataPanel` + `DependencyGraph` |
| `pages/QueryPage.tsx` | Port v1's `QueryBuilder` (simple + advanced modes, quick-class, op descriptions from API) |
| `pages/LoginPage.tsx` | Keep shape, minor polish |
| `pages/MyDatasetsPage.tsx` | Keep; reuse new `DatasetCard` |
| `components/errors/*` | Keep |
| `components/layout/AppShell.tsx` | Add NDI Cloud brand assets (logo, header, footer) |

### What's newly authored (didn't exist in v1 OR v2)

- Tuning-curve grid renderer (Tier 3.2)
- Subject-position trajectory overlay (Tier 3.3)
- Spike-waveform panel (Tier 3.4)
- Stimulus-marker overlay on timeseries (Tier 3.1)

## Revised M4–M7 roadmap

Replacing the original scope with tutorial-driven priorities.

### M4 — Rich summary tables + cross-cloud table joins

Quality gate: every tutorial summary table renders with full tutorial-spec columns; ontology popovers resolve; client-side filter/sort/column-picker/CSV work.

- [ ] Backend: expand `summary_table_service.py` projections to include all `openminds_*` companion fields (species, strain, sex, treatments, ontology term IDs). Already partially done — finish all classes.
- [ ] Backend: `GET /api/datasets/:id/doc-types` returning `[{className, count, description}]` ordered by count desc.
- [ ] Port `frontend/src/data/table-column-definitions.ts` from v1 verbatim (65 entries, 7 table types, ontology prefixes preserved).
- [ ] Port `components/ontology/OntologyPopover.tsx` from v1, wire to `/api/ontology/lookup`.
- [ ] Port `components/ontology/ontology-utils.ts` for `isOntologyTerm()` detector.
- [ ] Port `components/tables/SummaryTableView.tsx` from v1 (TanStack Table + virtualization + global search + per-column filter + column picker + auto-hide empty + CSV export + row click).
- [ ] Port `components/tables/TableSelector.tsx` (the top tab bar with icons).
- [ ] New `DocumentExplorerPage` with Summary Tables / Raw Documents toggle at top-right.
- [ ] Wire `useBatchOntologyLookup` in the API client to batch-prefetch terms visible in a table.
- [ ] E2E: for Dabrowska + Haley + Van Hooser live datasets, every summary table loads, ontology hover resolves, filter + sort + column picker + CSV work.

### M5 — Document detail + dependency graph + binary viz

Quality gate: document detail page shows raw JSON + dependency tree + binary data (timeseries / image / video / fitcurve) with tutorial-grade fidelity.

- [ ] Port `components/visualization/DataPanel.tsx` (unified type router).
- [ ] Port `components/visualization/TimeseriesChart.tsx` (286 LOC — proper uPlot config with units, cursor, multi-channel, zoom/pan).
  - [ ] Add stimulus-marker overlay layer (numbered bars above trace).
  - [ ] Add spike-marker overlay option (circles/squares at peak samples).
  - [ ] Add dual-trace mode for Vm + I on shared x-axis.
- [ ] Port `components/visualization/ImageViewer.tsx` (125 LOC — frame stepper, zoom, value-under-cursor, mask-overlay layer).
- [ ] Port `components/visualization/VideoPlayer.tsx` (scrub bar, time-synced position overlay hook).
- [ ] New `components/visualization/TrajectoryOverlay.tsx` for x/y-over-time paths on image/mask (Haley).
- [ ] Port `components/visualization/QuickPlot.tsx` (generic xy plotter for derived analyses like distance-over-time).
- [ ] Port `components/documents/DependencyGraph.tsx` (333 LOC visual tree + text fallback); backend endpoint if needed.
- [ ] Port `components/documents/DocumentDetail.tsx` with JSON tree + DataPanel + DependencyGraph.
- [ ] E2E: for a `session.reference` doc in Van Hooser, timeseries renders. For an image doc in Haley, image + trajectory renders. For a video doc in Haley, video plays. For any doc, dependency graph renders with navigable links.

### M6 — Query builder + cross-cloud UX + distribution viz

Quality gate: query builder handles every NDI operator including `~` negation; scope selector covers `public | my | all | dataset`; violin plots render distributions grouped by a categorical column.

- [ ] Port `components/query/QueryBuilder.tsx` (280 LOC — simple search + advanced filters + quick-class badges + operation descriptions from API).
- [ ] Enhance `/api/query/operations` to return op descriptions and expected params (backend adds a reflective endpoint).
- [ ] Scope selector: All public / My datasets / Everywhere / This dataset (dropdown for single-dataset case). Already mostly present.
- [ ] Port `components/visualization/ViolinPlot.tsx` (206 LOC D3 KDE implementation) + wire to `/api/visualize/distribution`.
- [ ] Cross-cloud: wire existing `AppearsElsewhere` component into document detail ("this subject appears in N other datasets").
- [ ] Ontology cross-linking: click any term → open query builder with `field: ontology.term_id, op: contains_string, param1: <term>` preloaded.
- [ ] E2E: run `isa=subject AND species=NCBITaxon:10090` across public scope, expect results. Run `~isa=subject` (negation). Open violin plot for strain × AgeAtRecording on Haley.

### M7 — UX polish + hardening + cutover

Quality gate: v2 matches v1's information density, Lighthouse ≥90, axe-core clean, load test passes, cutover runbook rehearsed.

- [ ] Brand: replace the generic database icon with NDI Cloud hexagon SVG. Wordmark "NDICloud" + "Data Browser" in header. "Powered by NDICloud" footer.
- [ ] Logo assets: copy SVG/PNG from `ndi-web-app/BrandAssets` (or v1 if different).
- [ ] AboutPage port (187 LOC).
- [ ] Loading-state progress: `Loading combined table... Processing N documents` (pass N from ndiquery result count).
- [ ] URL-persisted filter state on tables and query builder (TanStack Router-style, or manual `useSearchParams`).
- [ ] Dark-mode polish — the tailwind classes exist, needs a design pass.
- [ ] Lighthouse budgets (bundle ≤200 KB gzip, dataset list TTI ≤2s on 3G, detail ≤3s). Bundle is at 130 KB — room to grow.
- [ ] axe-core zero violations across every page.
- [ ] Load test: 200 concurrent users, p95 <1s dataset list, <3s combined table, zero 5xx.
- [ ] Feature-flag cutover to 100% over 4 days with automated rollback if p95 regresses >20%.
- [ ] Keep v1 at `ndi-data-browser-production.up.railway.app` archival for 2 weeks post-cutover.

## Non-goals (still deferred)

- Any SQLite dataset storage (ADR 004).
- Matlab/Python package parity — those are analysis tools, v2 is a browser.
- Dataset authoring / editing.
- Multi-org admin views.
- WebSocket real-time updates.

## Risk notes

1. **`SummaryTableView` port is the single biggest change** — 363 LOC of table logic + 325 LOC of column definitions + ontology popover batch-prefetch. This is the line item that makes v2 actually match v1. Budget it accordingly; don't compress.

2. **Tutorial dataset IDs are baked into tests** — Haley = `682e7772cdf3f24938176fac`, Van Hooser = `68839b1fbf243809c0800a01`, Dabrowska = `6896c654583596300a5b1b17`. Pin these in Playwright specs so tutorial-parity regressions are caught.

3. **Ontology cache warm-up** — first hit for any term in the 13 providers costs an external HTTP round-trip. Batch-prefetching all terms in a visible table hides this. Without the batch prefetch, the popover would stutter.

4. **Dependency graph performance** — for dense documents (e.g., Francesconi) the graph can have thousands of edges. v1 paginates / truncates. v2 should match.

5. **Binary decoding for rare formats** — the tutorials show Vm/I patch recordings, EPM video tracking, E. coli fluorescence — each is a different file format. v1 has NBF and VHSB parsers. Any format not in that list lands in `BINARY_DECODE_FAILED` — acceptable for now, tracked as technical debt.

## Execution order

The dependency chain (each item needs the ones above):

1. `OntologyPopover` + `ontology-utils` + `table-column-definitions`
2. `SummaryTableView` + `TableSelector` → enables M4 table parity.
3. `DataPanel` + `TimeseriesChart` + `ImageViewer` + `VideoPlayer` → enables M5 document detail.
4. `DependencyGraph` → finishes M5.
5. `QueryBuilder` port + `ViolinPlot` → M6.
6. Brand assets + AboutPage + loading states → M7 polish.

At each milestone: update E2E specs to validate against tutorial datasets, then cut a release tag, redeploy.
