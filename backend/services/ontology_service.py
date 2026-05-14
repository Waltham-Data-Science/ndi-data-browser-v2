"""Ontology term lookup across 13 providers with local cache."""
from __future__ import annotations

import asyncio
import html as _html
import re
from typing import Any

import httpx

from ..errors import OntologyLookupFailed
from ..observability.logging import get_logger
from .ontology_cache import OntologyCache, OntologyTerm

log = get_logger(__name__)

# WormBase page-title pattern. The canonical strain page returns a title
# like ``<title>  N2 (strain) - WormBase : Nematode Information Resource</title>``
# regardless of release (verified on WS294 via a Wayback snapshot; the
# template hasn't changed in years). We anchor on ``(strain)`` rather than
# just stripping the suffix so we don't accidentally pick up the
# "Just a moment..." Cloudflare interstitial as a strain name.
_WB_TITLE_RE = re.compile(
    r"<title[^>]*>\s*([^<(]+?)\s*\(strain\)\s*-\s*WormBase",
    re.IGNORECASE | re.DOTALL,
)

# Secondary parse target: the page-title breadcrumb
# ``<h2> <a ...>Strain</a> &raquo; <span>N2</span> </h2>``. Used when the
# ``<title>`` element is missing or mangled (older snapshots, partial
# loads), so the scrape still resolves on a degraded page.
_WB_BREADCRUMB_RE = re.compile(
    r"<a[^>]*>\s*Strain\s*</a>\s*&raquo;\s*<span[^>]*>\s*([^<]+?)\s*</span>",
    re.IGNORECASE | re.DOTALL,
)


class OntologyService:
    PROVIDERS = {
        "CL": "Cell Ontology",
        "NCBITaxon": "NCBI Taxonomy",
        "CHEBI": "Chemical Entities",
        "PATO": "Phenotype & Trait",
        "EFO": "Experimental Factor Ontology",
        "EMPTY": "Experimental Measurements/Purposes/Treatments",
        "RRID": "Research Resource Identifiers",
        "PubChem": "PubChem Compounds",
        "WBStrain": "WormBase Strains",
        "OM": "Units of Measure",
        "SNOMED": "SNOMED CT",
        "NDIC": "NDI Controlled Vocabulary",
        "NCIm": "NCI Metathesaurus",
    }

    def __init__(self, cache: OntologyCache) -> None:
        self.cache = cache
        self._http = httpx.AsyncClient(timeout=10.0, headers={"User-Agent": "ndi-data-browser-v2"})

    async def close(self) -> None:
        await self._http.aclose()

    async def lookup(self, term: str) -> OntologyTerm:
        provider, term_id = _split_term(term)
        if provider is None:
            raise OntologyLookupFailed("Term must be PROVIDER:ID, e.g. CL:0000540")
        cached = self.cache.get(provider, term_id)
        # IMPORTANT — only return cached entries that are REAL hits.
        # A "stub" cache entry (label=None AND definition=None) is what
        # ``OntologyCache.get`` returns for terms that were previously
        # looked up but came back empty. We DO NOT want to return such
        # stubs here, because:
        #
        #   1. Phase A (2026-05-13) wired ``ndi.ontology.lookup`` as a
        #      fallback for lab-specific prefixes (WBStrain, NDIC, etc.)
        #      that the legacy providers couldn't resolve. Terms looked
        #      up BEFORE Phase A were cached as stubs.
        #
        #   2. ``ONTOLOGY_CACHE_TTL_DAYS`` defaults to 30, so those
        #      pre-Phase-A stubs live for ~a month — and short-circuit
        #      the NDI-python fallback every time the term resurfaces.
        #
        # By treating stubs as cache MISSES we let the lookup pipeline
        # retry: existing providers (cheap; the OLS/SciCrunch/etc.
        # calls have their own outbound throttling) AND the NDI-python
        # fallback. On a successful resolution the new ``self.cache.set``
        # below OVERWRITES the stub — so each stuck stub heals on first
        # use rather than waiting for the 30-day TTL to expire.
        if cached is not None and (cached.label or cached.definition):
            return cached
        fetched: OntologyTerm | None = None
        try:
            fetched = await self._fetch_from_provider(provider, term_id)
        except Exception as e:
            log.warning("ontology.fetch_failed", provider=provider, term_id=term_id, error=str(e))
            # Don't raise yet — fall through to the NDI-python fallback, which
            # knows lab-specific terms (NDIC, WBStrain, internal Cre lines)
            # the existing providers may miss.

        # NDI-python fallback: only fire when existing path didn't yield a
        # usable record (stub with no label/definition, OR raised above).
        # This is a Phase A addition (2026-05-13) — see plan doc. Wrapped in
        # to_thread because ndi.ontology.lookup is sync and uses `requests`
        # internally, which would block the event loop if called directly.
        if fetched is None or (not fetched.label and not fetched.definition):
            ndi_term = await self._try_ndi_fallback(term, provider, term_id)
            if ndi_term is not None:
                self.cache.set(ndi_term)
                return ndi_term

        if fetched is None:
            # Both legacy AND NDI-python failed. Cache a stub so we don't
            # hammer the upstream providers, but if we had a prior stub
            # in cache just return it (avoid a redundant set).
            if cached is None:
                stub = OntologyTerm(
                    provider=provider, term_id=term_id,
                    label=None, definition=None, url=None,
                )
                self.cache.set(stub)
                return stub
            return cached
        self.cache.set(fetched)
        return fetched

    async def _try_ndi_fallback(
        self, term: str, provider: str, term_id: str,
    ) -> OntologyTerm | None:
        """Probe NDI-python's bundled ontology lookup. Returns None on miss
        (incl. NDI stack not installed, malformed input, unknown prefix).

        NDI's lookup hits the same OLS4 endpoints we do for many ontologies,
        but it ALSO ships a local CSV for NDIC and has hand-curated providers
        for WBStrain and a few others — that's where the additional hits
        come from. Net: this fallback rarely fires but catches the long tail."""
        try:
            from .ndi_python_service import lookup_ontology
            result = await asyncio.to_thread(lookup_ontology, term)
        except Exception as e:
            log.warning("ontology.ndi_fallback_failed", term=term, error=str(e))
            return None
        if result is None:
            return None
        # NDI's `.to_dict()` shape: {id, name, prefix, definition, synonyms, short_name}.
        # Map onto our OntologyTerm. We preserve the original PROVIDER (case
        # as it was passed in) so the cache key matches what the caller asked for.
        return OntologyTerm(
            provider=provider,
            term_id=term_id,
            label=result.get("name") or None,
            definition=result.get("definition") or None,
            url=None,
        )

    async def batch_lookup(self, terms: list[str]) -> list[OntologyTerm]:
        unique = list(dict.fromkeys(t for t in terms if t))
        results = await asyncio.gather(*[self._safe_lookup(t) for t in unique])
        return [r for r in results if r is not None]

    async def _safe_lookup(self, term: str) -> OntologyTerm | None:
        try:
            return await self.lookup(term)
        except OntologyLookupFailed:
            return None

    # OLS-resolvable providers. UBERON was previously omitted (live
    # check showed UBERON:0001870 returning label=null even though
    # OLS has it as "frontal cortex"). GO and OBI added for similar
    # completeness — these are all OBO ontologies hosted at the same
    # EBI OLS4 endpoint with identical query semantics.
    _OLS_PROVIDERS = {
        "CL": "cl",
        "NCBITaxon": "ncbitaxon",
        "CHEBI": "chebi",
        "PATO": "pato",
        "EFO": "efo",
        "UBERON": "uberon",
        "GO": "go",
        "OBI": "obi",
    }

    async def _fetch_from_provider(self, provider: str, term_id: str) -> OntologyTerm:
        ols = self._OLS_PROVIDERS.get(provider)
        if ols is not None:
            return await self._fetch_ols(ols, f"{provider}:{term_id}")
        if provider == "RRID":
            return await self._fetch_scicrunch(term_id)
        if provider == "WBStrain":
            return await self._fetch_wormbase(term_id)
        if provider == "PubChem":
            return await self._fetch_pubchem(term_id)
        # Fallback: record a stub so we don't hammer.
        return OntologyTerm(provider=provider, term_id=term_id, label=None, definition=None, url=None)

    async def _fetch_ols(self, ont: str, iri_id: str) -> OntologyTerm:
        # EBI OLS4 API. Encode IRI via the obolibrary namespace.
        safe = iri_id.replace(":", "_")
        iri = f"http://purl.obolibrary.org/obo/{safe}"
        r = await self._http.get(
            f"https://www.ebi.ac.uk/ols4/api/ontologies/{ont}/terms",
            params={"iri": iri},
        )
        r.raise_for_status()
        body = r.json()
        terms = (body.get("_embedded") or {}).get("terms") or []
        if not terms:
            raise ValueError("term not found in OLS")
        t = terms[0]
        return OntologyTerm(
            provider=iri_id.split(":", 1)[0],
            term_id=iri_id.split(":", 1)[1],
            label=t.get("label"),
            definition=_first_string(t.get("description") or t.get("obo_definition_citation")),
            url=t.get("iri"),
        )

    async def _fetch_scicrunch(self, rrid: str) -> OntologyTerm:
        url = "https://scicrunch.org/resolver/RRID:" + rrid
        try:
            r = await self._http.get(url + ".json", timeout=10.0)
            r.raise_for_status()
            body = r.json()
            hit = (body.get("hits") or {}).get("hits") or []
            if hit:
                src = hit[0].get("_source") or {}
                return OntologyTerm(
                    provider="RRID",
                    term_id=rrid,
                    label=src.get("item", {}).get("name"),
                    definition=src.get("item", {}).get("description"),
                    url=url,
                )
        except Exception:
            pass
        return OntologyTerm(provider="RRID", term_id=rrid, label=None, definition=None, url=url)

    async def _fetch_wormbase(self, strain_id: str) -> OntologyTerm:
        """Resolve a WBStrain CURIE to its human-readable strain name.

        NDI-python's WBStrain provider only returns a URL, not a label, so
        we GET the canonical strain page and parse the strain name from
        ``<title>`` (primary) or the page-title breadcrumb (secondary).
        Any failure — Cloudflare interstitial, timeout, 404, parse miss —
        falls through to ``label=None`` so the lookup pipeline degrades
        cleanly rather than crashing. Cache layering upstream means each
        strain page is hit at most once per TTL.
        """
        url = f"https://wormbase.org/species/c_elegans/strain/{strain_id}"
        label = await self._scrape_wormbase_label(url)
        return OntologyTerm(
            provider="WBStrain", term_id=strain_id,
            label=label, definition=None, url=url,
        )

    async def _scrape_wormbase_label(self, url: str) -> str | None:
        """Fetch ``url`` and extract the strain name from the HTML.

        Total budget is 5 seconds — WormBase pages are small (~70 KB) but
        Cloudflare can interpose. Returns ``None`` on any failure so the
        caller can fall through; never raises.
        """
        try:
            r = await self._http.get(url, timeout=5.0)
        except Exception as e:
            log.warning("ontology.wormbase.fetch_failed", url=url, error=str(e))
            return None
        if r.status_code != 200:
            log.warning(
                "ontology.wormbase.bad_status",
                url=url, status=r.status_code,
            )
            return None
        body = r.text
        m = _WB_TITLE_RE.search(body) or _WB_BREADCRUMB_RE.search(body)
        if m is None:
            return None
        label = _html.unescape(m.group(1)).strip()
        # Guard against empty captures and the Cloudflare "Just a moment"
        # text leaking through despite the ``(strain)`` anchor.
        if not label or label.lower().startswith("just a moment"):
            return None
        return label

    async def _fetch_pubchem(self, cid: str) -> OntologyTerm:
        url = f"https://pubchem.ncbi.nlm.nih.gov/compound/{cid}"
        try:
            r = await self._http.get(
                f"https://pubchem.ncbi.nlm.nih.gov/rest/pug/compound/cid/{cid}/property/IUPACName/JSON",
                timeout=10.0,
            )
            r.raise_for_status()
            name = (((r.json() or {}).get("PropertyTable") or {}).get("Properties") or [{}])[0].get("IUPACName")
            return OntologyTerm(provider="PubChem", term_id=cid, label=name, definition=None, url=url)
        except Exception:
            return OntologyTerm(provider="PubChem", term_id=cid, label=None, definition=None, url=url)

    def stats(self) -> dict[str, Any]:
        return self.cache.stats()


def _split_term(term: str) -> tuple[str | None, str]:
    if ":" not in term:
        return None, term
    provider, tid = term.split(":", 1)
    return provider, tid


def _first_string(v: Any) -> str | None:
    if isinstance(v, list) and v:
        first = v[0]
        if isinstance(first, str):
            return first
        if isinstance(first, dict):
            return first.get("definition")
    if isinstance(v, str):
        return v
    return None
