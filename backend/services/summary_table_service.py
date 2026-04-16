"""Summary tables via chained ndiquery + bulk-fetch.

Single-class: ndiquery returns IDs, bulk-fetch in parallel batches of 500 (max 3 concurrent),
project the class-appropriate fields.

Combined: chained across subject → probe → epoch using indexed `depends_on`.
"""
from __future__ import annotations

import asyncio
import time
from typing import Any

from ..clients.ndi_cloud import BULK_FETCH_MAX, NdiCloudClient
from ..observability.logging import get_logger
from ..observability.metrics import table_build_duration_seconds

log = get_logger(__name__)

MAX_CONCURRENT_BULK_FETCH = 3

# When a primary class has an openminds-* companion that holds rich metadata,
# we fetch it in parallel and merge fields via depends_on.
_ENRICHMENT_FOR: dict[str, str] = {
    "subject": "openminds_subject",
    "probe": "probe_location",
    "element": "probe_location",
}


class SummaryTableService:
    def __init__(self, cloud: NdiCloudClient) -> None:
        self.cloud = cloud

    async def single_class(
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

        # Enrichment: fetch the companion openminds/* class that holds rich metadata,
        # join on depends_on (ndiId, not mongo id) back to the primary docs, and
        # merge fields. Each openminds doc typically represents ONE property
        # (species, age, sex, strain), so we accumulate into a list per primary.
        enrichment_class = _ENRICHMENT_FOR.get(class_name)
        if enrichment_class and docs:
            try:
                # Fetch ALL enrichment docs for this class (dataset-scoped), then
                # filter locally — cheaper than depends_on on a list with hundreds
                # of values and avoids any query-size limits.
                enriched_body = await self.cloud.ndiquery(
                    searchstructure=[{"operation": "isa", "param1": enrichment_class}],
                    scope=dataset_id,
                    access_token=access_token,
                )
                enriched_ids = _extract_ids(enriched_body)
                enriched_docs = await self._bulk_fetch_all(
                    dataset_id, enriched_ids, access_token=access_token,
                )
                # Index by the primary ndiId they depend on.
                by_primary: dict[str, list[dict[str, Any]]] = {}
                for e in enriched_docs:
                    for dep in _depends_on_values(e):
                        by_primary.setdefault(dep, []).append(e)
                for d in docs:
                    pid = (d.get("data") or {}).get("base", {}).get("id")
                    if pid and pid in by_primary:
                        d["_enriched_list"] = by_primary[pid]
            except Exception as e:
                log.warning("table.enrichment_failed", class_name=class_name, error=str(e))

        columns, rows = _project_for_class(class_name, docs)
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
        access_token: str | None,
    ) -> dict[str, Any]:
        build_start = time.perf_counter()
        # NDI's canonical chain is subject → element → element_epoch. Some
        # datasets use older "probe" / "epoch" class names; try both.
        subjects, elements, element_epochs = await asyncio.gather(
            self._fetch_class(dataset_id, "subject", access_token=access_token),
            self._fetch_class_any(dataset_id, ["element", "probe"], access_token=access_token),
            self._fetch_class_any(dataset_id, ["element_epoch", "epoch"], access_token=access_token),
        )
        probes = elements
        epochs = element_epochs

        # Also fetch the enrichment docs for the species column.
        om_subjects = await self._fetch_class(dataset_id, "openminds_subject", access_token=access_token)
        species_by_subject_ndi: dict[str, str] = {}
        for om in om_subjects:
            om_type = str(((om.get("data") or {}).get("openminds") or {}).get("openminds_type", ""))
            if not om_type.endswith("Species"):
                continue
            species = (((om.get("data") or {}).get("openminds") or {}).get("fields") or {}).get("name")
            if not species:
                continue
            for dep in _depends_on_values(om):
                species_by_subject_ndi[dep] = species

        # Index by ndiId (base.id) — that's what depends_on uses.
        def _ndi(d: dict[str, Any]) -> str | None:
            return (d.get("data") or {}).get("base", {}).get("id") or d.get("ndiId")

        subject_by_ndi = {_ndi(s): s for s in subjects if _ndi(s)}
        probe_by_ndi = {_ndi(p): p for p in probes if _ndi(p)}

        rows: list[dict[str, Any]] = []
        for epoch in epochs:
            epoch_deps = _depends_on_values(epoch)
            probe = next((probe_by_ndi[d] for d in epoch_deps if d in probe_by_ndi), None)
            probe_deps = _depends_on_values(probe) if probe else []
            subject = next((subject_by_ndi[d] for d in probe_deps if d in subject_by_ndi), None)
            subj_ndi = _ndi(subject) if subject else None
            t0, t1 = _t0_t1(epoch)
            rows.append({
                "subject": _subject_display_name(subject) if subject else None,
                "probe": _first(probe, "element.name", "probe.name") if probe else None,
                "epoch": _first(epoch, "epochid.epochid", "element_epoch.epoch_id")
                         or _project_name(epoch),
                "species": species_by_subject_ndi.get(subj_ndi) if subj_ndi else None,
                "type": _first(probe, "element.type", "probe.type") if probe else None,
                "start": t0,
                "stop": t1,
                "subjectId": subj_ndi,
                "probeId": _ndi(probe) if probe else None,
                "epochId": _ndi(epoch),
            })

        elapsed = time.perf_counter() - build_start
        table_build_duration_seconds.labels(class_name="combined").observe(elapsed)
        log.info(
            "table.build.combined",
            dataset_id=dataset_id,
            subjects=len(subjects),
            probes=len(probes),
            epochs=len(epochs),
            ms=int(elapsed * 1000),
        )
        return {
            "columns": [
                {"key": "subject", "label": "Subject"},
                {"key": "species", "label": "Species"},
                {"key": "probe", "label": "Probe"},
                {"key": "type", "label": "Probe type"},
                {"key": "epoch", "label": "Epoch"},
                {"key": "start", "label": "Start"},
                {"key": "stop", "label": "Stop"},
            ],
            "rows": rows,
        }

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

    async def _fetch_class_dep(
        self,
        dataset_id: str,
        class_name: str,
        *,
        depends_on: list[str],
        access_token: str | None,
    ) -> list[dict[str, Any]]:
        if not depends_on:
            return []
        structure = [
            {"operation": "isa", "param1": class_name},
            {"operation": "depends_on", "param1": "*", "param2": depends_on},
        ]
        body = await self.cloud.ndiquery(
            searchstructure=structure,
            scope=dataset_id,
            access_token=access_token,
        )
        ids = _extract_ids(body)
        return await self._bulk_fetch_all(dataset_id, ids, access_token=access_token)

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


# --- Projection ---

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


def _depends_on_values(doc: dict[str, Any] | None) -> list[str]:
    """Accept depends_on as {name,value} OR list of {name,value}."""
    if not doc:
        return []
    data = doc.get("data") or {}
    deps = data.get("depends_on")
    if deps is None:
        return []
    if isinstance(deps, dict):
        deps = [deps]
    if not isinstance(deps, list):
        return []
    out: list[str] = []
    for d in deps:
        if not isinstance(d, dict):
            continue
        v = d.get("value")
        if isinstance(v, str) and v:
            out.append(v)
    return out


def _data_get(doc: dict[str, Any] | None, key: str) -> Any:
    if not doc:
        return None
    data = doc.get("data") or {}
    # Try direct and class-namespaced (e.g., doc["data"]["subject"][key]).
    for k in (key, "subject", "probe", "epoch", "element"):
        v = data.get(k)
        if isinstance(v, dict) and key in v:
            return v.get(key)
        if k == key and v is not None:
            return v
    return None


def _project_name(doc: dict[str, Any] | None) -> str | None:
    if not doc:
        return None
    return doc.get("name") or (doc.get("data") or {}).get("name")


_SUBJECT_COLUMNS = [
    {"key": "name", "label": "Name"},
    {"key": "species", "label": "Species"},
    {"key": "sex", "label": "Sex"},
    {"key": "strain", "label": "Strain"},
    {"key": "ageAtRecording", "label": "Age at recording"},
    {"key": "description", "label": "Description"},
]

_PROBE_COLUMNS = [
    {"key": "name", "label": "Name"},
    {"key": "type", "label": "Type"},
    {"key": "reference", "label": "Reference"},
    {"key": "description", "label": "Description"},
]

_EPOCH_COLUMNS = [
    {"key": "name", "label": "Name"},
    {"key": "epoch_id", "label": "Epoch ID"},
    {"key": "epoch_start", "label": "Start"},
    {"key": "epoch_stop", "label": "Stop"},
]

_ELEMENT_COLUMNS = [
    {"key": "name", "label": "Name"},
    {"key": "elementClass", "label": "Class"},
    {"key": "description", "label": "Description"},
]

_GENERIC_COLUMNS = [
    {"key": "name", "label": "Name"},
    {"key": "id", "label": "ID"},
]


def _project_for_class(class_name: str, docs: list[dict[str, Any]]) -> tuple[list[dict[str, str]], list[dict[str, Any]]]:
    if class_name == "subject":
        return _SUBJECT_COLUMNS, [_row_subject(d) for d in docs]
    if class_name in ("probe", "element"):
        return _PROBE_COLUMNS, [_row_probe(d) for d in docs]
    if class_name in ("epoch", "element_epoch"):
        return _EPOCH_COLUMNS, [_row_epoch(d) for d in docs]
    return _GENERIC_COLUMNS, [
        {"name": _project_name(d) or _first(d, "base.name"), "id": d.get("id") or d.get("ndiId")}
        for d in docs
    ]


def _first(d: dict[str, Any] | None, *paths: str) -> Any:
    """Look up dotted paths under d.data, first non-null wins."""
    if not d:
        return None
    roots: list[Any] = []
    if isinstance(d.get("data"), dict):
        roots.append(d["data"])
    for root in roots:
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


def _openminds_attr(d: dict[str, Any], type_suffix: str) -> Any:
    """Pull a value from d._enriched_list where each element is an openminds doc.

    Looks for openminds_type ending in `type_suffix` (e.g. 'Species', 'BiologicalSex',
    'StrainOfLaboratorySpecies', 'Age') and returns the .fields.name / .fields.value.
    """
    lst = d.get("_enriched_list") or []
    for e in lst:
        om = (e.get("data") or {}).get("openminds") or {}
        om_type = str(om.get("openminds_type", ""))
        if not om_type.endswith(type_suffix):
            continue
        fields = om.get("fields") or {}
        for key in ("name", "value", "label", "preferredLabel"):
            v = fields.get(key)
            if v not in (None, ""):
                return v
    return None


def _subject_display_name(d: dict[str, Any]) -> str | None:
    return (
        _project_name(d)
        or _first(d, "base.name", "subject.local_identifier", "openminds.subject.name",
                  "openminds.Subject.name", "subject.name")
    )


def _row_subject(d: dict[str, Any]) -> dict[str, Any]:
    return {
        "name": _subject_display_name(d),
        "species": _openminds_attr(d, "Species") or _first(d, "subject.species", "species"),
        "sex": _openminds_attr(d, "BiologicalSex") or _first(d, "subject.sex", "sex"),
        "strain": (
            _openminds_attr(d, "StrainOfLaboratorySpecies")
            or _openminds_attr(d, "Strain")
            or _first(d, "subject.strain", "strain")
        ),
        "ageAtRecording": (
            _openminds_attr(d, "Age")
            or _openminds_attr(d, "AgeCategory")
            or _first(d, "subject.age_at_recording", "subject.ageAtRecording", "age_at_recording")
        ),
        "description": _first(d, "subject.description", "description", "base.description"),
    }


def _row_probe(d: dict[str, Any]) -> dict[str, Any]:
    return {
        "name": (
            _first(d, "element.name", "probe.name")
            or _project_name(d)
            or _first(d, "base.name")
        ),
        "type": _first(d, "element.type", "probe.type", "element.ndi_element_class",
                       "probe.class", "type"),
        "reference": _first(d, "element.reference", "probe.reference", "reference"),
        "description": _first(d, "element.description", "probe.description",
                              "description", "base.description"),
    }


def _t0_t1(d: dict[str, Any] | None) -> tuple[Any, Any]:
    tt = _first(d, "element_epoch.t0_t1", "epoch.t0_t1", "t0_t1")
    if isinstance(tt, list) and len(tt) >= 2:
        return tt[0], tt[1]
    return None, None


def _row_epoch(d: dict[str, Any]) -> dict[str, Any]:
    t0, t1 = _t0_t1(d)
    return {
        "name": (
            _first(d, "epochid.epochid", "element_epoch.epoch_id", "element_epoch.name",
                   "epoch.name")
            or _project_name(d)
            or _first(d, "base.name")
        ),
        "epoch_id": _first(d, "epochid.epochid", "element_epoch.epoch_id",
                           "epoch.epoch_id", "epoch.id", "epoch_id"),
        "epoch_start": t0 if t0 is not None else _first(
            d, "element_epoch.epoch_start", "epoch.epoch_start", "epoch.start",
            "epochclock.t0", "epoch_start",
        ),
        "epoch_stop": t1 if t1 is not None else _first(
            d, "element_epoch.epoch_stop", "epoch.epoch_stop", "epoch.stop",
            "epochclock.t1", "epoch_stop",
        ),
    }


def _row_element(d: dict[str, Any]) -> dict[str, Any]:
    return {
        "name": _project_name(d) or _first(d, "base.name", "element.name"),
        "elementClass": _first(d, "element.class", "element.element_class", "element.type", "elementClass"),
        "description": _first(d, "element.description", "description", "base.description"),
    }
