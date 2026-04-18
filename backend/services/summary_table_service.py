"""Summary tables via chained ndiquery + bulk-fetch.

Single-class: ndiquery returns IDs, bulk-fetch in parallel batches of 500,
project the class-appropriate fields. Companion enrichment classes
(openminds_subject, probe_location, subject, treatment) are fetched
dataset-wide in parallel and joined client-side via depends_on.

Combined: chained across subject → element → element_epoch using indexed
`depends_on` + `isa`.

Openminds projection rules
--------------------------

`openminds_subject` is a polymorphic class with two schemas discovered in
the M4a Day-1 audit (see backend/tests/fixtures/openminds/README.md):

- Schema A (Species, BiologicalSex, GeneticStrainType): ontology ID in
  `data.openminds.fields.preferredOntologyIdentifier`.
- Schema B (Strain): ontology ID in `data.openminds.fields.ontologyIdentifier`
  plus list-valued ndi:// reference fields (species, geneticStrainType,
  backgroundStrain) pointing to sibling companion docs for the same subject.

`_openminds_name_and_ontology()` dispatches by type so Strain's WBStrain IDs
surface correctly. `_resolve_ndi_ref()` walks Schema B nested references by
ndiId against the full enrichment set.

t0_t1 normalization
-------------------

Some datasets (Haley) emit dual-clock epochs: `epoch_clock="dev_local_time,
exp_global_time"` with `t0_t1=[[dev_t0, global_t0], [dev_t1, global_t1]]`.
Others (Van Hooser) emit a single dev clock with a flat `t0_t1=[t0, t1]`.
`_normalize_t0_t1()` always returns two `{devTime, globalTime}` objects so
the frontend renders a single shape.
"""
from __future__ import annotations

import asyncio
import time
from typing import Any

from ..auth.session import SessionData, user_scope_for
from ..cache.redis_table import RedisTableCache
from ..clients.ndi_cloud import BULK_FETCH_MAX, NdiCloudClient
from ..observability.logging import get_logger
from ..observability.metrics import table_build_duration_seconds

log = get_logger(__name__)

# Raised from 3 to 6 during M4a cache work. Haley has ~19 bulk-fetch batches
# for 9032 openminds_subject docs (at 500/batch); at concurrency 3 that's 7
# sequential rounds, at 6 it's 4. Cloud Lambda concurrency budget supports
# 6 comfortably — confirm with Steve before raising further.
MAX_CONCURRENT_BULK_FETCH = 6

# Enrichment plan per primary class. Each listed class is fetched dataset-
# wide in parallel and its docs indexed by the `depends_on` edge the
# projection needs. `subject` and `openminds_subject` are always pulled when
# the primary class is subject-scoped; probe_location is pulled for
# element-centric rows; treatment is pulled when the row is subject-
# attributable (subject, element, element_epoch).
_ENRICHMENTS_FOR: dict[str, list[str]] = {
    "subject":        ["openminds_subject", "treatment"],
    "element":        ["subject", "openminds_subject", "probe_location"],
    "probe":          ["subject", "openminds_subject", "probe_location"],
    "element_epoch":  ["element", "subject", "openminds_subject", "probe_location", "treatment"],
    "epoch":          ["element", "subject", "openminds_subject", "probe_location", "treatment"],
    "treatment":      ["subject", "openminds_subject"],
    "openminds_subject": [],
    "probe_location": [],
    "openminds":      [],
}

# Enrichments whose failure should abort the table build (and skip caching).
# Non-required enrichments can return empty if the cloud is flaky — the
# table still renders with SummaryTableView's auto-hide-empty-column falloff
# for those columns. Required ones, if empty, mean the row shape is
# incomplete and we'd rather serve a retry-able error than cache a broken
# table for the full TTL window (plan §M4a step 3).
_REQUIRED_ENRICHMENTS: dict[str, set[str]] = {
    "subject":        {"openminds_subject"},
    "element":        {"subject"},
    "probe":          {"subject"},
    "element_epoch":  {"element", "subject"},
    "epoch":          {"element", "subject"},
    # treatment + probe_location are optional context — empty is acceptable.
}


class SummaryTableService:
    def __init__(
        self,
        cloud: NdiCloudClient,
        *,
        cache: RedisTableCache | None = None,
    ) -> None:
        self.cloud = cloud
        self.cache = cache

    async def single_class(
        self,
        dataset_id: str,
        class_name: str,
        *,
        session: SessionData | None,
    ) -> dict[str, Any]:
        access_token = session.access_token if session else None
        if self.cache is not None:
            key = RedisTableCache.table_key(
                dataset_id, class_name, user_scope=user_scope_for(session),
            )
            return await self.cache.get_or_compute(
                key,
                lambda: self._build_single_class(
                    dataset_id, class_name, access_token=access_token,
                ),
            )
        return await self._build_single_class(
            dataset_id, class_name, access_token=access_token,
        )

    async def _build_single_class(
        self,
        dataset_id: str,
        class_name: str,
        *,
        access_token: str | None,
    ) -> dict[str, Any]:
        t0 = time.perf_counter()
        body = await self.cloud.ndiquery(
            searchstructure=[{"operation": "isa", "param1": class_name}],
            scope=dataset_id,
            access_token=access_token,
        )
        ids = _extract_ids(body)
        docs = await self._bulk_fetch_all(dataset_id, ids, access_token=access_token)

        # Fetch all enrichment classes in parallel. If any REQUIRED enrichment
        # fails, raise — the cache layer skips writes on exceptions, so a
        # transient cloud failure doesn't pin a broken empty-enrichment table
        # into Redis for the full TTL window. Plan §M4a step 3: "Skip cache
        # if cloud call fails."
        enrich_classes = _ENRICHMENTS_FOR.get(class_name, [])
        enriched: dict[str, list[dict[str, Any]]] = {}
        if enrich_classes and docs:
            results = await asyncio.gather(
                *[
                    self._fetch_class(dataset_id, ec, access_token=access_token)
                    for ec in enrich_classes
                ],
                return_exceptions=True,
            )
            for ec, r in zip(enrich_classes, results, strict=True):
                if isinstance(r, BaseException):
                    log.warning(
                        "table.enrichment_failed",
                        primary=class_name,
                        enrichment=ec,
                        error=str(r),
                    )
                    # Required enrichment failed — propagate so the cache
                    # doesn't pin a broken build. The caller re-tries on the
                    # next request, which may succeed against a healthy cloud.
                    if ec in _REQUIRED_ENRICHMENTS.get(class_name, set()):
                        raise RuntimeError(
                            f"Required enrichment {ec!r} failed while building "
                            f"{class_name} table: {r}",
                        )
                    enriched[ec] = []
                else:
                    enriched[ec] = r

        columns, rows = _project_for_class(class_name, docs, enriched)
        table_build_duration_seconds.labels(class_name=class_name).observe(
            time.perf_counter() - t0,
        )
        log.info(
            "table.build.single",
            dataset_id=dataset_id,
            class_name=class_name,
            ids=len(ids),
            rows=len(rows),
            ms=int((time.perf_counter() - t0) * 1000),
        )
        return {"columns": columns, "rows": rows}

    async def combined(
        self,
        dataset_id: str,
        *,
        session: SessionData | None,
    ) -> dict[str, Any]:
        access_token = session.access_token if session else None
        if self.cache is not None:
            key = RedisTableCache.table_key(
                dataset_id, "combined", user_scope=user_scope_for(session),
            )
            return await self.cache.get_or_compute(
                key,
                lambda: self._build_combined(dataset_id, access_token=access_token),
            )
        return await self._build_combined(dataset_id, access_token=access_token)

    async def _build_combined(
        self,
        dataset_id: str,
        *,
        access_token: str | None,
    ) -> dict[str, Any]:
        build_start = time.perf_counter()
        # NDI's canonical chain is subject → element → element_epoch. Some
        # datasets use older "probe" / "epoch" class names; try both.
        subjects, elements, element_epochs, om_subjects, probe_locations, treatments = (
            await asyncio.gather(
                self._fetch_class(dataset_id, "subject", access_token=access_token),
                self._fetch_class_any(
                    dataset_id, ["element", "probe"], access_token=access_token,
                ),
                self._fetch_class_any(
                    dataset_id, ["element_epoch", "epoch"], access_token=access_token,
                ),
                self._fetch_class(
                    dataset_id, "openminds_subject", access_token=access_token,
                ),
                self._fetch_class(
                    dataset_id, "probe_location", access_token=access_token,
                ),
                self._fetch_class(
                    dataset_id, "treatment", access_token=access_token,
                ),
            )
        )
        enriched: dict[str, list[dict[str, Any]]] = {
            "subject": subjects,
            "element": elements,
            "openminds_subject": om_subjects,
            "probe_location": probe_locations,
            "treatment": treatments,
        }

        # Index subjects + elements by ndiId for fast join.
        subject_by_ndi = _index_by_ndi_id(subjects)
        element_by_ndi = _index_by_ndi_id(elements)

        # Attach openminds enrichment list per subject so _row_subject can
        # use the exact same shape it sees in single_class.
        _attach_openminds_enrichment(subjects, om_subjects)

        rows: list[dict[str, Any]] = []
        for epoch in element_epochs:
            element_ndi = _epoch_element_ndi(epoch)
            element = element_by_ndi.get(element_ndi) if element_ndi else None
            subject_ndi = _element_subject_ndi(element) if element else None
            subject = subject_by_ndi.get(subject_ndi) if subject_ndi else None

            subj_row = _row_subject(subject, enriched) if subject else {}
            probe_row = _row_probe(element, enriched) if element else {}
            epoch_row = _row_epoch(epoch, enriched, subject=subject, element=element)

            rows.append({
                # Top-level joined summary used by v2's existing combined consumer.
                "subject": _subject_display_name(subject) if subject else None,
                "species": subj_row.get("speciesName"),
                "speciesOntology": subj_row.get("speciesOntology"),
                "strain": subj_row.get("strainName"),
                "strainOntology": subj_row.get("strainOntology"),
                "sex": subj_row.get("biologicalSexName"),
                "probe": probe_row.get("probeName"),
                "probeLocationName": probe_row.get("probeLocationName"),
                "probeLocationOntology": probe_row.get("probeLocationOntology"),
                "type": probe_row.get("probeType"),
                "epoch": epoch_row.get("epochNumber"),
                "approachName": epoch_row.get("approachName"),
                "approachOntology": epoch_row.get("approachOntology"),
                "start": epoch_row.get("epochStart"),
                "stop": epoch_row.get("epochStop"),
                "subjectId": subject_ndi,
                "probeId": element_ndi,
                "epochId": _ndi_id(epoch),
            })

        elapsed = time.perf_counter() - build_start
        table_build_duration_seconds.labels(class_name="combined").observe(elapsed)
        log.info(
            "table.build.combined",
            dataset_id=dataset_id,
            subjects=len(subjects),
            elements=len(elements),
            element_epochs=len(element_epochs),
            ms=int(elapsed * 1000),
        )
        return {
            "columns": [
                {"key": "subject", "label": "Subject"},
                {"key": "species", "label": "Species"},
                {"key": "speciesOntology", "label": "Species Ontology"},
                {"key": "strain", "label": "Strain"},
                {"key": "strainOntology", "label": "Strain Ontology"},
                {"key": "sex", "label": "Sex"},
                {"key": "probe", "label": "Probe"},
                {"key": "probeLocationName", "label": "Probe Location"},
                {"key": "probeLocationOntology", "label": "Probe Location Ontology"},
                {"key": "type", "label": "Probe type"},
                {"key": "epoch", "label": "Epoch"},
                {"key": "approachName", "label": "Approach"},
                {"key": "approachOntology", "label": "Approach Ontology"},
                {"key": "start", "label": "Start"},
                {"key": "stop", "label": "Stop"},
            ],
            "rows": rows,
        }

    async def ontology_tables(
        self,
        dataset_id: str,
        *,
        session: SessionData | None,
    ) -> dict[str, Any]:
        """Project `ontologyTableRow` docs into one TableResponse per distinct
        `variableNames` schema.

        Each `ontologyTableRow` carries a CSV of `variableNames`, a matching
        CSV of human-readable `names`, a matching CSV of `ontologyNodes`
        (term IDs per column), and a `data` object keyed by variableName
        holding the row's numeric/string values. Rows with the same
        variableNames CSV share a table schema.

        Returns
        -------
        {
          "groups": [
            {
              "variableNames": [str, ...],
              "names": [str, ...],
              "ontologyNodes": [str, ...],
              "table": {"columns": [{key, label, ontologyTerm?}, ...], "rows": [...]},
              "docIds": [str, ...],
              "rowCount": int,
            },
            ...
          ]
        }
        """
        access_token = session.access_token if session else None
        if self.cache is not None:
            key = RedisTableCache.table_key(
                dataset_id, "ontology", user_scope=user_scope_for(session),
            )
            return await self.cache.get_or_compute(
                key,
                lambda: self._build_ontology_tables(dataset_id, access_token=access_token),
            )
        return await self._build_ontology_tables(dataset_id, access_token=access_token)

    async def _build_ontology_tables(
        self,
        dataset_id: str,
        *,
        access_token: str | None,
    ) -> dict[str, Any]:
        t0 = time.perf_counter()
        docs = await self._fetch_class(
            dataset_id, "ontologyTableRow", access_token=access_token,
        )

        # Group by variableNames CSV — that's the schema identity.
        groups: dict[str, dict[str, Any]] = {}
        for doc in docs:
            otr = (doc.get("data") or {}).get("ontologyTableRow") or {}
            var_csv = otr.get("variableNames")
            if not isinstance(var_csv, str) or not var_csv:
                continue
            variable_names = [v.strip() for v in var_csv.split(",") if v.strip()]
            if not variable_names:
                continue
            group = groups.setdefault(
                var_csv,
                {
                    "variableNames": variable_names,
                    "names": _split_csv(otr.get("names"), len(variable_names)),
                    "ontologyNodes": _split_csv(otr.get("ontologyNodes"), len(variable_names)),
                    "rows": [],
                    "docIds": [],
                },
            )
            row_data = otr.get("data") or {}
            # Project each row as a dict keyed by variableName for frontend
            # consumption (matches SummaryTableView's Record<string, unknown>).
            row: dict[str, Any] = {name: row_data.get(name) for name in variable_names}
            group["rows"].append(row)
            if doc.get("id"):
                group["docIds"].append(doc["id"])

        # Build a stable output — sorted by row count desc so the fullest
        # tables show first in the selector.
        out_groups: list[dict[str, Any]] = []
        for g in sorted(groups.values(), key=lambda g: -len(g["rows"])):
            columns = []
            for i, var in enumerate(g["variableNames"]):
                label = g["names"][i] if i < len(g["names"]) else var
                term = g["ontologyNodes"][i] if i < len(g["ontologyNodes"]) else None
                columns.append({
                    "key": var,
                    "label": label or var,
                    "ontologyTerm": term or None,
                })
            out_groups.append({
                "variableNames": g["variableNames"],
                "names": g["names"],
                "ontologyNodes": g["ontologyNodes"],
                "table": {"columns": columns, "rows": g["rows"]},
                "docIds": g["docIds"],
                "rowCount": len(g["rows"]),
            })

        table_build_duration_seconds.labels(class_name="ontology").observe(
            time.perf_counter() - t0,
        )
        log.info(
            "table.build.ontology",
            dataset_id=dataset_id,
            groups=len(out_groups),
            total_rows=sum(g["rowCount"] for g in out_groups),
            ms=int((time.perf_counter() - t0) * 1000),
        )
        return {"groups": out_groups}

    # --- Internal ---

    async def _fetch_class(
        self,
        dataset_id: str,
        class_name: str,
        *,
        access_token: str | None,
    ) -> list[dict[str, Any]]:
        body = await self.cloud.ndiquery(
            searchstructure=[{"operation": "isa", "param1": class_name}],
            scope=dataset_id,
            access_token=access_token,
        )
        ids = _extract_ids(body)
        return await self._bulk_fetch_all(dataset_id, ids, access_token=access_token)

    async def _fetch_class_any(
        self,
        dataset_id: str,
        candidates: list[str],
        *,
        access_token: str | None,
    ) -> list[dict[str, Any]]:
        """Try each class in order; return results from the first non-empty one."""
        for c in candidates:
            docs = await self._fetch_class(dataset_id, c, access_token=access_token)
            if docs:
                return docs
        return []

    async def _bulk_fetch_all(
        self,
        dataset_id: str,
        ids: list[str],
        *,
        access_token: str | None,
    ) -> list[dict[str, Any]]:
        if not ids:
            return []
        batches = [ids[i : i + BULK_FETCH_MAX] for i in range(0, len(ids), BULK_FETCH_MAX)]
        sem = asyncio.Semaphore(MAX_CONCURRENT_BULK_FETCH)

        async def _fetch(batch: list[str]) -> list[dict[str, Any]]:
            async with sem:
                return await self.cloud.bulk_fetch(dataset_id, batch, access_token=access_token)

        results = await asyncio.gather(*[_fetch(b) for b in batches])
        flat: list[dict[str, Any]] = []
        for r in results:
            flat.extend(r)
        return flat


# ---------------------------------------------------------------------------
# Generic extraction helpers
# ---------------------------------------------------------------------------

def _split_csv(value: Any, expected_len: int) -> list[str]:
    """Split a CSV string and pad/truncate to `expected_len` entries.

    ontologyTableRow carries three parallel CSVs (variableNames, names,
    ontologyNodes); if one is shorter we backfill with empties, if longer
    we truncate so column indices stay aligned.
    """
    if not isinstance(value, str):
        return [""] * expected_len
    parts = [v.strip() for v in value.split(",")]
    if len(parts) < expected_len:
        parts = parts + [""] * (expected_len - len(parts))
    return parts[:expected_len]


def _extract_ids(query_body: dict[str, Any]) -> list[str]:
    docs = query_body.get("documents") or query_body.get("ids") or []
    out: list[str] = []
    for d in docs:
        if isinstance(d, str):
            out.append(d)
        elif isinstance(d, dict):
            i = d.get("id") or d.get("ndiId") or d.get("_id")
            if isinstance(i, str):
                out.append(i)
    return out


def _depends_on_entries(doc: dict[str, Any] | None) -> list[dict[str, Any]]:
    """Accept depends_on as a single {name,value} dict OR a list of them."""
    if not doc:
        return []
    deps = (doc.get("data") or {}).get("depends_on")
    if deps is None:
        return []
    if isinstance(deps, dict):
        return [deps]
    if isinstance(deps, list):
        return [d for d in deps if isinstance(d, dict)]
    return []


def _depends_on_values(doc: dict[str, Any] | None) -> list[str]:
    """All non-empty `value` strings from depends_on entries."""
    out: list[str] = []
    for d in _depends_on_entries(doc):
        v = d.get("value")
        if isinstance(v, str) and v:
            out.append(v)
    return out


def _depends_on_value_by_name(
    doc: dict[str, Any] | None, name: str,
) -> str | None:
    """Return the non-empty value of the depends_on edge with `name`, or None."""
    for d in _depends_on_entries(doc):
        if d.get("name") == name:
            v = d.get("value")
            if isinstance(v, str) and v:
                return v
    return None


def _ndi_id(doc: dict[str, Any] | None) -> str | None:
    """Return the document's ndiId — the base.id that depends_on edges reference."""
    if not doc:
        return None
    base = (doc.get("data") or {}).get("base") or {}
    return base.get("id") or doc.get("ndiId")


def _index_by_ndi_id(docs: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    """Build {ndiId → doc} index. Skips docs with missing ndiId."""
    return {nid: d for d in docs if (nid := _ndi_id(d))}


def _first(d: dict[str, Any] | None, *paths: str) -> Any:
    """Look up dotted paths under d.data, first non-null / non-empty wins."""
    if not d:
        return None
    root = d.get("data") if isinstance(d.get("data"), dict) else None
    if root is None:
        return None
    for p in paths:
        cur: Any = root
        ok = True
        for seg in p.split("."):
            if isinstance(cur, dict) and seg in cur:
                cur = cur[seg]
            else:
                ok = False
                break
        if ok and cur not in (None, ""):
            return cur
    return None


def _clean(v: Any) -> Any:
    """Normalize empty-string / whitespace-only to None. Pass through other types."""
    if v is None:
        return None
    if isinstance(v, str):
        s: str = v.strip()
        return s if s else None
    return v


# ---------------------------------------------------------------------------
# Openminds projection
# ---------------------------------------------------------------------------

def _openminds_type_suffix(om_doc: dict[str, Any] | None) -> str | None:
    """Return the terminal segment of openminds_type (e.g. 'Species', 'Strain')."""
    if not om_doc:
        return None
    om = (om_doc.get("data") or {}).get("openminds") or {}
    t = om.get("openminds_type")
    if not isinstance(t, str) or not t:
        return None
    return t.rsplit("/", 1)[-1]


def _openminds_ontology_key_for(type_suffix: str) -> str:
    """Schema B (Strain) uses `ontologyIdentifier`; Schema A uses
    `preferredOntologyIdentifier`. Locked by test_openminds_shape.py.
    """
    if type_suffix == "Strain":
        return "ontologyIdentifier"
    return "preferredOntologyIdentifier"


def _openminds_name_and_ontology(
    subject_doc: dict[str, Any], type_suffix: str,
) -> tuple[str | None, str | None]:
    """Return (name, ontology_id) for the first enrichment doc matching type_suffix.

    Dispatches by type on the ontology-key name to handle Schema A
    (Species / BiologicalSex / GeneticStrainType → preferredOntologyIdentifier)
    vs Schema B (Strain → ontologyIdentifier).
    """
    enriched = subject_doc.get("_enriched_openminds") or []
    for om_doc in enriched:
        if _openminds_type_suffix(om_doc) != type_suffix:
            continue
        fields = ((om_doc.get("data") or {}).get("openminds") or {}).get("fields") or {}
        name = _clean(fields.get("name"))
        ontology = _clean(fields.get(_openminds_ontology_key_for(type_suffix)))
        return (name, ontology)
    return (None, None)


def _openminds_age_at_recording(subject_doc: dict[str, Any]) -> Any:
    """Age is openminds-encoded as either `Age` (scalar) or `AgeCategory` docs.
    Returns the first non-empty `fields.name` or `fields.value`, preferring
    concrete Age over AgeCategory.
    """
    enriched = subject_doc.get("_enriched_openminds") or []
    # Prefer Age
    for suffix in ("Age", "AgeCategory"):
        for om_doc in enriched:
            if _openminds_type_suffix(om_doc) != suffix:
                continue
            fields = ((om_doc.get("data") or {}).get("openminds") or {}).get("fields") or {}
            for k in ("value", "name", "label"):
                v = _clean(fields.get(k))
                if v is not None:
                    return v
    return None


def _openminds_by_ndi_id(
    subject_doc: dict[str, Any],
) -> dict[str, dict[str, Any]]:
    """Index the subject's openminds companions by their ndiId, for resolving
    Schema B nested references (Strain.fields.backgroundStrain, .species, etc.).
    """
    out: dict[str, dict[str, Any]] = {}
    for om_doc in subject_doc.get("_enriched_openminds") or []:
        nid = _ndi_id(om_doc)
        if nid:
            out[nid] = om_doc
    return out


def _resolve_ndi_ref(
    index: dict[str, dict[str, Any]], ndi_uri: str,
) -> dict[str, Any] | None:
    """`ndi://<ndiId>` → doc via the given index. Returns None on miss."""
    if not isinstance(ndi_uri, str):
        return None
    prefix = "ndi://"
    if not ndi_uri.startswith(prefix):
        return None
    return index.get(ndi_uri[len(prefix):])


def _background_strain_from_strain(
    subject_doc: dict[str, Any],
) -> tuple[str | None, str | None]:
    """Follow the subject's Strain doc's `fields.backgroundStrain[]` ndi:// refs.
    Each ref points to another Strain-schema companion doc (Schema B) whose
    name + ontologyIdentifier describe the background strain. Returns the
    first non-empty (name, ontology) pair or (None, None) if no background
    strain is referenced / resolvable.

    Haley's N2 strain doc has `backgroundStrain: []` (empty) so this returns
    (None, None) in both live test datasets. When Dabrowska publishes with
    Sprague-Dawley backgrounds the refs will populate.
    """
    index = _openminds_by_ndi_id(subject_doc)
    for om_doc in subject_doc.get("_enriched_openminds") or []:
        if _openminds_type_suffix(om_doc) != "Strain":
            continue
        fields = ((om_doc.get("data") or {}).get("openminds") or {}).get("fields") or {}
        refs = fields.get("backgroundStrain")
        if not isinstance(refs, list):
            continue
        for ref in refs:
            bg = _resolve_ndi_ref(index, ref)
            if not bg:
                continue
            bg_fields = ((bg.get("data") or {}).get("openminds") or {}).get("fields") or {}
            name = _clean(bg_fields.get("name"))
            ontology = _clean(bg_fields.get("ontologyIdentifier"))
            if name or ontology:
                return (name, ontology)
    return (None, None)


def _attach_openminds_enrichment(
    subjects: list[dict[str, Any]],
    openminds_subjects: list[dict[str, Any]],
) -> None:
    """Index openminds companion docs by their subject_id depends_on edge and
    attach the full per-subject list onto each subject doc as
    `_enriched_openminds`. Idempotent: safe to call twice.
    """
    by_subject: dict[str, list[dict[str, Any]]] = {}
    for om_doc in openminds_subjects:
        sid = _depends_on_value_by_name(om_doc, "subject_id")
        if sid:
            by_subject.setdefault(sid, []).append(om_doc)
    for subject in subjects:
        sid = _ndi_id(subject)
        subject["_enriched_openminds"] = by_subject.get(sid, []) if sid else []


# ---------------------------------------------------------------------------
# Probe / element helpers
# ---------------------------------------------------------------------------

def _element_subject_ndi(element_doc: dict[str, Any] | None) -> str | None:
    return _depends_on_value_by_name(element_doc, "subject_id")


def _probe_locations_for(
    element_doc: dict[str, Any], probe_locations: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """All probe_location docs whose depends_on.probe_id matches this element's ndiId."""
    element_ndi = _ndi_id(element_doc)
    if not element_ndi:
        return []
    return [
        pl for pl in probe_locations
        if _depends_on_value_by_name(pl, "probe_id") == element_ndi
    ]


def _probe_location_split(
    locations: list[dict[str, Any]],
) -> tuple[tuple[str | None, str | None], tuple[str | None, str | None]]:
    """Split probe_location docs into (anatomical_location, cell_type) by the
    ontology prefix of `probe_location.ontology_name`:
    - UBERON: anatomical location
    - CL:    cell type
    - others: bucketed as location by default

    Each bucket returns (name, ontology). First hit wins per bucket.
    """
    loc: tuple[str | None, str | None] = (None, None)
    cell: tuple[str | None, str | None] = (None, None)
    for pl in locations:
        data = (pl.get("data") or {}).get("probe_location") or {}
        name = _clean(data.get("name"))
        ontology = _clean(data.get("ontology_name"))
        if ontology and ontology.upper().startswith("CL:"):
            if cell == (None, None):
                cell = (name, ontology)
        elif loc == (None, None):
            loc = (name, ontology)
    return loc, cell


# ---------------------------------------------------------------------------
# Epoch helpers
# ---------------------------------------------------------------------------

def _epoch_element_ndi(epoch_doc: dict[str, Any] | None) -> str | None:
    return _depends_on_value_by_name(epoch_doc, "element_id")


def _parse_epoch_clock(clock_header: Any) -> list[str]:
    """epoch_clock is a CSV like `"dev_local_time,exp_global_time"` or
    `"dev_local_time"`. Splits, strips, drops empties. Returns [] if not a string.
    """
    if not isinstance(clock_header, str):
        return []
    return [c.strip() for c in clock_header.split(",") if c.strip()]


def _clock_indices(clocks: list[str]) -> tuple[int, int | None]:
    """Return (dev_index, global_index or None).

    Dev/device clock is the first entry whose name contains `local` or `dev`
    — falls back to index 0 if no match.
    Global clock is the first entry whose name contains `global`.
    """
    dev_idx = 0
    global_idx: int | None = None
    for i, c in enumerate(clocks):
        low = c.lower()
        if dev_idx == 0 and ("local" in low or "dev" in low):
            dev_idx = i
        if global_idx is None and "global" in low:
            global_idx = i
    return dev_idx, global_idx


def _normalize_t0_t1(epoch_doc: dict[str, Any] | None) -> tuple[
    dict[str, Any] | None, dict[str, Any] | None,
]:
    """Always return two `{devTime, globalTime}` objects.

    Haley (dual-clock): epoch_clock=`dev_local_time,exp_global_time`,
        t0_t1=[[dev_t0, global_t0], [dev_t1, global_t1]] (nested 2x2).
    VH (scalar): epoch_clock=`dev_local_time`, t0_t1=[t0, t1] (flat).

    For scalar datasets `globalTime=None`. Returns (None, None) if t0_t1 is
    missing or malformed.
    """
    tt = _first(
        epoch_doc,
        "element_epoch.t0_t1", "epoch.t0_t1", "t0_t1",
    )
    if not isinstance(tt, list) or len(tt) < 2:
        return (None, None)

    clocks = _parse_epoch_clock(_first(
        epoch_doc,
        "element_epoch.epoch_clock", "epoch.epoch_clock", "epoch_clock",
    ))
    dev_idx, global_idx = _clock_indices(clocks)

    def pick(entry: Any) -> dict[str, Any]:
        if isinstance(entry, list):
            dev = entry[dev_idx] if 0 <= dev_idx < len(entry) else None
            glb = (
                entry[global_idx]
                if (global_idx is not None and 0 <= global_idx < len(entry))
                else None
            )
            return {"devTime": dev, "globalTime": glb}
        return {"devTime": entry, "globalTime": None}

    return pick(tt[0]), pick(tt[1])


# ---------------------------------------------------------------------------
# Treatment helpers
# ---------------------------------------------------------------------------

def _treatments_for_subject(
    subject_ndi: str | None, treatments: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Treatments whose depends_on.subject_id matches the subject's ndiId."""
    if not subject_ndi:
        return []
    return [
        t for t in treatments
        if _depends_on_value_by_name(t, "subject_id") == subject_ndi
    ]


def _treatment_by_ontology_prefix(
    treatments: list[dict[str, Any]], prefix: str,
) -> tuple[str | None, str | None]:
    """First treatment whose `treatment.ontologyName` starts with `prefix:`.
    Returns (name, ontology_id) — both normalized to None when empty.
    """
    for t in treatments:
        tdata = (t.get("data") or {}).get("treatment") or {}
        ontology = _clean(tdata.get("ontologyName"))
        if ontology and ontology.upper().startswith(f"{prefix.upper()}:"):
            return (_clean(tdata.get("name")), ontology)
    return (None, None)


# ---------------------------------------------------------------------------
# Subject display name
# ---------------------------------------------------------------------------

def _project_name(doc: dict[str, Any] | None) -> str | None:
    if not doc:
        return None
    v = _clean(doc.get("name")) or _clean((doc.get("data") or {}).get("name"))
    # _clean returns Any (pass-through for non-str); narrow here.
    return v if isinstance(v, str) else None


def _subject_display_name(d: dict[str, Any]) -> str | None:
    return (
        _project_name(d)
        or _clean(_first(
            d,
            "subject.local_identifier", "base.name", "subject.name",
            "openminds.subject.name", "openminds.Subject.name",
        ))
    )


# ---------------------------------------------------------------------------
# Row projections — tutorial-parity column sets (camelCase)
# ---------------------------------------------------------------------------

SUBJECT_COLUMNS: list[dict[str, str]] = [
    {"key": "subjectIdentifier",         "label": "Subject Identifier"},
    {"key": "subjectLocalIdentifier",    "label": "Local Identifier"},
    {"key": "subjectDocumentIdentifier", "label": "Subject Doc ID"},
    {"key": "sessionDocumentIdentifier", "label": "Session Doc ID"},
    {"key": "strainName",                "label": "Strain"},
    {"key": "strainOntology",            "label": "Strain Ontology"},
    {"key": "geneticStrainTypeName",     "label": "Genetic Strain Type"},
    {"key": "speciesName",               "label": "Species"},
    {"key": "speciesOntology",           "label": "Species Ontology"},
    {"key": "backgroundStrainName",      "label": "Background Strain"},
    {"key": "backgroundStrainOntology",  "label": "Background Strain Ontology"},
    {"key": "biologicalSexName",         "label": "Sex"},
    {"key": "biologicalSexOntology",     "label": "Sex Ontology"},
    {"key": "ageAtRecording",            "label": "Age at Recording"},
    {"key": "description",               "label": "Description"},
]

PROBE_COLUMNS: list[dict[str, str]] = [
    {"key": "probeDocumentIdentifier",   "label": "Probe Doc ID"},
    {"key": "probeName",                 "label": "Name"},
    {"key": "probeType",                 "label": "Type"},
    {"key": "probeReference",            "label": "Reference"},
    {"key": "probeLocationName",         "label": "Probe Location"},
    {"key": "probeLocationOntology",     "label": "Probe Location Ontology"},
    {"key": "cellTypeName",              "label": "Cell Type"},
    {"key": "cellTypeOntology",          "label": "Cell Type Ontology"},
    {"key": "subjectDocumentIdentifier", "label": "Subject Doc ID"},
]

EPOCH_COLUMNS: list[dict[str, str]] = [
    {"key": "epochNumber",               "label": "Epoch"},
    {"key": "epochDocumentIdentifier",   "label": "Epoch Doc ID"},
    {"key": "probeDocumentIdentifier",   "label": "Probe Doc ID"},
    {"key": "subjectDocumentIdentifier", "label": "Subject Doc ID"},
    {"key": "epochStart",                "label": "Start"},
    {"key": "epochStop",                 "label": "Stop"},
    {"key": "mixtureName",               "label": "Mixture"},
    {"key": "mixtureOntology",           "label": "Mixture Ontology"},
    {"key": "approachName",              "label": "Approach"},
    {"key": "approachOntology",          "label": "Approach Ontology"},
]

ELEMENT_COLUMNS: list[dict[str, str]] = PROBE_COLUMNS  # elements use the same shape

TREATMENT_COLUMNS: list[dict[str, str]] = [
    {"key": "treatmentName",             "label": "Treatment"},
    {"key": "treatmentOntology",         "label": "Treatment Ontology"},
    {"key": "numericValue",              "label": "Numeric Value"},
    {"key": "stringValue",               "label": "String Value"},
    {"key": "subjectDocumentIdentifier", "label": "Subject Doc ID"},
]

GENERIC_COLUMNS: list[dict[str, str]] = [
    {"key": "name",                      "label": "Name"},
    {"key": "documentIdentifier",        "label": "Doc ID"},
]


def _project_for_class(
    class_name: str,
    docs: list[dict[str, Any]],
    enriched: dict[str, list[dict[str, Any]]],
) -> tuple[list[dict[str, str]], list[dict[str, Any]]]:
    # Attach the openminds enrichment list to subjects (single source of truth
    # for Schema-A/B reads regardless of which primary class we're projecting).
    subjects = enriched.get("subject", [])
    om_subjects = enriched.get("openminds_subject", [])
    if om_subjects and subjects:
        _attach_openminds_enrichment(subjects, om_subjects)
    if class_name == "subject":
        # primary docs ARE the subjects; attach enrichment on them too.
        _attach_openminds_enrichment(docs, om_subjects)

    if class_name == "subject":
        treatments = enriched.get("treatment", [])
        return SUBJECT_COLUMNS, [_row_subject(d, {"treatment": treatments}) for d in docs]

    if class_name in ("probe", "element"):
        return PROBE_COLUMNS, [_row_probe(d, enriched) for d in docs]

    if class_name in ("epoch", "element_epoch"):
        element_by_ndi = _index_by_ndi_id(enriched.get("element", []))
        subject_by_ndi = _index_by_ndi_id(subjects)
        rows: list[dict[str, Any]] = []
        for epoch in docs:
            element = element_by_ndi.get(_epoch_element_ndi(epoch) or "")
            subject_ndi = _element_subject_ndi(element) if element else None
            subject = subject_by_ndi.get(subject_ndi) if subject_ndi else None
            rows.append(_row_epoch(epoch, enriched, subject=subject, element=element))
        return EPOCH_COLUMNS, rows

    if class_name == "treatment":
        return TREATMENT_COLUMNS, [_row_treatment(d) for d in docs]

    if class_name == "probe_location":
        return PROBE_COLUMNS, [_row_probe_location_only(d) for d in docs]

    return GENERIC_COLUMNS, [
        {
            "name": _project_name(d) or _clean(_first(d, "base.name")),
            "documentIdentifier": _ndi_id(d),
        }
        for d in docs
    ]


def _row_subject(
    d: dict[str, Any], enriched: dict[str, list[dict[str, Any]]],
) -> dict[str, Any]:
    """15-column tutorial-parity subject row (camelCase).

    Uses `_openminds_name_and_ontology()` dispatch on type_suffix so Schema A
    types read `preferredOntologyIdentifier` and Strain reads `ontologyIdentifier`.
    """
    strain_name, strain_ontology = _openminds_name_and_ontology(d, "Strain")
    species_name, species_ontology = _openminds_name_and_ontology(d, "Species")
    sex_name, sex_ontology = _openminds_name_and_ontology(d, "BiologicalSex")
    bg_name, bg_ontology = _background_strain_from_strain(d)
    gst_name, _ = _openminds_name_and_ontology(d, "GeneticStrainType")

    base = (d.get("data") or {}).get("base") or {}
    subj = (d.get("data") or {}).get("subject") or {}

    # subjectIdentifier: prefer the explicit local_identifier; fall back to
    # base.name (populated for VH). For Haley both resolve to the long
    # lab-prefixed local identifier.
    local_id = _clean(subj.get("local_identifier"))
    base_name = _clean(base.get("name"))

    return {
        "subjectIdentifier":         local_id or base_name,
        "subjectLocalIdentifier":    local_id or base_name,
        "subjectDocumentIdentifier": _clean(base.get("id")) or _ndi_id(d),
        "sessionDocumentIdentifier": _clean(base.get("session_id")),
        "strainName":                strain_name,
        "strainOntology":            strain_ontology,
        "geneticStrainTypeName":     gst_name,
        "speciesName":               species_name,
        "speciesOntology":           species_ontology,
        "backgroundStrainName":      bg_name,
        "backgroundStrainOntology":  bg_ontology,
        "biologicalSexName":         sex_name,
        "biologicalSexOntology":     sex_ontology,
        "ageAtRecording":            _openminds_age_at_recording(d)
                                      or _clean(_first(
                                          d, "subject.age_at_recording",
                                          "subject.ageAtRecording", "age_at_recording",
                                      )),
        "description":               _clean(_first(
                                          d, "subject.description",
                                          "description", "base.description",
                                      )),
    }


def _row_probe(
    d: dict[str, Any], enriched: dict[str, list[dict[str, Any]]],
) -> dict[str, Any]:
    """9-column tutorial-parity probe/element row (camelCase).

    Joins probe_location docs via `depends_on.probe_id` and splits them into
    anatomical location (UBERON) vs cell type (CL). The dataset may have
    zero probe_locations — columns remain empty and SummaryTableView auto-
    hides them client-side.
    """
    probe_locations = _probe_locations_for(d, enriched.get("probe_location", []))
    (loc_name, loc_ont), (cell_name, cell_ont) = _probe_location_split(probe_locations)
    return {
        "probeDocumentIdentifier": _ndi_id(d),
        "probeName":               _clean(_first(d, "element.name", "probe.name"))
                                    or _project_name(d)
                                    or _clean(_first(d, "base.name")),
        "probeType":               _clean(_first(
                                        d, "element.type", "probe.type",
                                        "element.ndi_element_class",
                                        "probe.class", "type",
                                   )),
        "probeReference":          _clean(_first(d, "element.reference", "probe.reference",
                                                    "reference")),
        "probeLocationName":       loc_name,
        "probeLocationOntology":   loc_ont,
        "cellTypeName":            cell_name,
        "cellTypeOntology":        cell_ont,
        "subjectDocumentIdentifier": _element_subject_ndi(d),
    }


def _row_probe_location_only(d: dict[str, Any]) -> dict[str, Any]:
    """Projection when probe_location is the primary class (rare; diagnostic)."""
    data = (d.get("data") or {}).get("probe_location") or {}
    ontology = _clean(data.get("ontology_name"))
    is_cell = bool(ontology and ontology.upper().startswith("CL:"))
    return {
        "probeDocumentIdentifier": _depends_on_value_by_name(d, "probe_id"),
        "probeName":               None,
        "probeType":               None,
        "probeReference":          None,
        "probeLocationName":       None if is_cell else _clean(data.get("name")),
        "probeLocationOntology":   None if is_cell else ontology,
        "cellTypeName":            _clean(data.get("name")) if is_cell else None,
        "cellTypeOntology":        ontology if is_cell else None,
        "subjectDocumentIdentifier": None,
    }


def _row_epoch(
    d: dict[str, Any],
    enriched: dict[str, list[dict[str, Any]]],
    *,
    subject: dict[str, Any] | None = None,
    element: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """10-column tutorial-parity epoch/element_epoch row.

    t0_t1 is normalized to `{devTime, globalTime}` objects — scalar datasets
    get `globalTime=None`. Approach and Mixture columns are populated from
    the subject's treatments via `treatment.ontologyName` prefix heuristics
    (plan §M4a step 1 — treatment-enriched epoch fields).
    """
    start, stop = _normalize_t0_t1(d)

    element_ndi = _epoch_element_ndi(d)
    if element is None:
        element_by_ndi = _index_by_ndi_id(enriched.get("element", []))
        element = element_by_ndi.get(element_ndi or "")

    subject_ndi = _element_subject_ndi(element) if element else None
    if subject is None and subject_ndi:
        subject_by_ndi = _index_by_ndi_id(enriched.get("subject", []))
        subject = subject_by_ndi.get(subject_ndi)

    subj_treatments = _treatments_for_subject(subject_ndi, enriched.get("treatment", []))
    # Heuristic mapping until we see Dabrowska's real ontology prefixes:
    # - "Approach" treatments live under EMPTY: (NDI's own controlled vocab
    #   for experimental approach, per tutorial data).
    # - "Mixture" treatments (e.g. CHEBI-based drug mixtures) live under
    #   CHEBI: in Dabrowska; fall back to EMPTY: for compatibility.
    approach_name, approach_ontology = _treatment_by_ontology_prefix(subj_treatments, "EMPTY")
    mixture_name, mixture_ontology = _treatment_by_ontology_prefix(subj_treatments, "CHEBI")

    return {
        "epochNumber":               _clean(_first(
                                          d, "epochid.epochid",
                                          "element_epoch.epoch_id",
                                          "element_epoch.name",
                                          "epoch.name",
                                     ))
                                     or _project_name(d)
                                     or _clean(_first(d, "base.name")),
        "epochDocumentIdentifier":   _ndi_id(d),
        "probeDocumentIdentifier":   element_ndi,
        "subjectDocumentIdentifier": subject_ndi,
        "epochStart":                start,
        "epochStop":                 stop,
        "mixtureName":               mixture_name,
        "mixtureOntology":           mixture_ontology,
        "approachName":              approach_name,
        "approachOntology":          approach_ontology,
    }


def _row_treatment(d: dict[str, Any]) -> dict[str, Any]:
    tdata = (d.get("data") or {}).get("treatment") or {}
    return {
        "treatmentName":             _clean(tdata.get("name")) or _project_name(d),
        "treatmentOntology":         _clean(tdata.get("ontologyName")),
        "numericValue":              tdata.get("numeric_value"),
        "stringValue":               _clean(tdata.get("string_value")),
        "subjectDocumentIdentifier": _depends_on_value_by_name(d, "subject_id"),
    }
