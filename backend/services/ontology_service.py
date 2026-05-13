"""Ontology term lookup across 13 providers with local cache."""
from __future__ import annotations

import asyncio
from typing import Any

import httpx

from ..errors import OntologyLookupFailed
from ..observability.logging import get_logger
from .ontology_cache import OntologyCache, OntologyTerm

log = get_logger(__name__)


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
        if cached is not None:
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
            raise OntologyLookupFailed(f"Could not look up {term}")
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

    _OLS_PROVIDERS = {"CL": "cl", "NCBITaxon": "ncbitaxon", "CHEBI": "chebi", "PATO": "pato", "EFO": "efo"}

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
        url = f"https://wormbase.org/species/c_elegans/strain/{strain_id}"
        return OntologyTerm(provider="WBStrain", term_id=strain_id, label=strain_id, definition=None, url=url)

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
