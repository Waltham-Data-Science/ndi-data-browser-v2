"""DatasetProvenance synthesizer — Plan B B5.

Aggregates three dataset-scoped provenance signals into one structured fact
sheet:

1. **``branchOf``** — the parent dataset this one was branched from
   (``IDataset.branchOf`` on the cloud side).
2. **Branches** — children datasets forked from this one
   (``GET /datasets/:datasetId/branches``).
3. **Cross-dataset document dependencies** — aggregated from each document's
   ``data.depends_on[].value`` ndiIds. An edge from source → target is
   recorded when a document in *this* dataset depends on a document in a
   *different* dataset. Keyed by ``(sourceDatasetId, targetDatasetId,
   viaDocumentClass)`` and counted. Same-dataset edges are the document
   dependency graph (see ``dependency_graph_service``) and intentionally
   not included here.

Vocabulary lock
---------------

This is the **dataset-derivation** graph. We deliberately avoid the word
"lineage" — the cloud's ``classLineage`` is *class-ISA* lineage (a
spikesorting doc's superclasses list), a completely different concept.
Using "lineage" would be a naming clash. The code, UI, and docs use
"provenance" / "derivation" / "branches".

Relationship to :mod:`backend.services.dependency_graph_service`
----------------------------------------------------------------

The existing document-level dependency graph walks BFS both directions from
a *single document*, producing a node-edge graph constrained to one dataset
(the cloud's ``ndiquery`` scope). This service composes similar primitives
but at a coarser grain: aggregate across *every document in the dataset*
and only surface *cross-dataset* edges (same-dataset refs are redundant
with the per-doc graph). The per-doc graph and the dataset provenance
therefore answer different questions:

- Per-doc graph (M5 / ``dependency_graph_service``): "What depends on THIS
  spikesorting document, within this dataset?"
- Dataset provenance (B5, this file): "Which OTHER datasets does this
  dataset's data point at, and how many refs of each document class?"

Cache key: ``provenance:v1:{dataset_id}:{user_scope}`` with a 5-minute TTL
matching :mod:`dataset_summary_service`'s freshness strategy (amendment
§4.B3: a dataset published at T=0 must appear on the provenance card
within minutes, not hours).

HTTP boundary: every cloud call routes through :mod:`backend.clients.ndi_cloud`.
No direct ``httpx`` / ``requests`` / ``aiohttp`` / ``urllib3`` imports —
enforced by :mod:`backend.tests.unit.test_services_http_boundary` (ADR-009).
"""
from __future__ import annotations

import asyncio
import time
from datetime import UTC, datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, StrictStr, conint

from ..auth.session import SessionData, user_scope_for
from ..cache.redis_table import RedisTableCache
from ..clients.ndi_cloud import BULK_FETCH_MAX, NdiCloudClient
from ..observability.logging import get_logger

log = get_logger(__name__)

PROVENANCE_SCHEMA_VERSION = "provenance:v1"
PROVENANCE_CACHE_TTL_SECONDS = 5 * 60  # freshness > TTL economy (amendment §4.B3)
PROVENANCE_KEY_PREFIX = "provenance:v1"

# Concurrency bounds. Shared per-request to amortize HTTP/2 multiplexing.
_BULK_FETCH_CONCURRENCY = 3  # matches summary_table_service convention
_NDI_RESOLVE_CONCURRENCY = 8  # ndiquery resolution for each unique target ndiId

# Safety caps — bound pathological datasets. Exceeding a cap does NOT fail
# the build; it truncates the aggregation and records a warning-equivalent
# counter on the log line so operators can spot datasets that push limits.
_MAX_CLASSES_WALKED = 25  # top-N classes by count sorted desc; tail is rarely load-bearing
_MAX_UNIQUE_TARGETS = 1000  # unique ndiIds we try to resolve
_CLASSES_SKIP = frozenset(
    {
        # Counters-only classes that never carry depends_on (class-counts
        # lists them for completeness). Skipping saves a round-trip.
        "unknown",
    }
)


# ---------------------------------------------------------------------------
# Data-shape contracts — mirrored in frontend/src/types/dataset-provenance.ts
# ---------------------------------------------------------------------------

class DatasetDependencyEdge(BaseModel):
    """One aggregated cross-dataset edge.

    Semantics: at least ``edgeCount`` documents of class ``viaDocumentClass``
    in ``sourceDatasetId`` carry a ``depends_on`` reference to a document in
    ``targetDatasetId``.

    Edges are always source→target (this dataset depends on another). The
    inverse relationship ("who depends on us") is not aggregated here — it
    would require scanning every OTHER dataset, which is a different cost
    profile. A future "reverse provenance" endpoint could add it.
    """

    model_config = ConfigDict(extra="forbid")

    sourceDatasetId: StrictStr
    targetDatasetId: StrictStr
    viaDocumentClass: StrictStr
    edgeCount: conint(ge=1)  # type: ignore[valid-type]


class DatasetProvenance(BaseModel):
    """Structured dataset-level provenance. ``branchOf = None`` means this
    dataset is not a branch of anything; ``branches = []`` means no children
    have been forked off it; ``documentDependencies = []`` means no
    cross-dataset ``depends_on`` references were found.
    """

    model_config = ConfigDict(extra="forbid")

    datasetId: StrictStr
    branchOf: StrictStr | None = None
    branches: list[StrictStr] = Field(default_factory=list)
    documentDependencies: list[DatasetDependencyEdge] = Field(default_factory=list)
    computedAt: StrictStr
    schemaVersion: Literal["provenance:v1"] = "provenance:v1"


# ---------------------------------------------------------------------------
# Service
# ---------------------------------------------------------------------------

class DatasetProvenanceService:
    """Compose one :class:`DatasetProvenance` per dataset. Stateless across
    requests; one service instance per :class:`NdiCloudClient` (wired in
    :mod:`backend.routers._deps`).
    """

    def __init__(
        self,
        cloud: NdiCloudClient,
        *,
        cache: RedisTableCache | None = None,
    ) -> None:
        self.cloud = cloud
        self.cache = cache

    async def build_provenance(
        self,
        dataset_id: str,
        *,
        session: SessionData | None,
    ) -> DatasetProvenance:
        access_token = session.access_token if session else None
        if self.cache is not None:
            key = provenance_cache_key(dataset_id, session)
            payload = await self.cache.get_or_compute(
                key,
                lambda: self._build_and_serialize(
                    dataset_id, access_token=access_token,
                ),
            )
            return DatasetProvenance.model_validate(payload)
        payload = await self._build_and_serialize(
            dataset_id, access_token=access_token,
        )
        return DatasetProvenance.model_validate(payload)

    async def _build_and_serialize(
        self, dataset_id: str, *, access_token: str | None,
    ) -> dict[str, Any]:
        prov = await self._build(dataset_id, access_token=access_token)
        return prov.model_dump(mode="json")

    async def _build(
        self, dataset_id: str, *, access_token: str | None,
    ) -> DatasetProvenance:
        t0 = time.perf_counter()

        # Stage 1: the three cheap metadata calls. get_dataset can 404 — let
        # NotFound propagate unchanged through the cloud client.
        dataset_task = self.cloud.get_dataset(
            dataset_id, access_token=access_token,
        )
        branches_task = self._branches_safely(
            dataset_id, access_token=access_token,
        )
        counts_task = self.cloud.get_document_class_counts(
            dataset_id, access_token=access_token,
        )
        dataset_raw, branches_raw, counts_raw = await asyncio.gather(
            dataset_task, branches_task, counts_task,
        )

        branch_of = _branch_of_from_raw(dataset_raw)
        branch_ids = _branch_ids_from_raw(branches_raw)

        # Stage 2: walk documents for cross-dataset depends_on edges.
        classes = _classes_to_walk(counts_raw)
        edges = await self._aggregate_cross_dataset_edges(
            dataset_id,
            classes,
            access_token=access_token,
        )

        prov = DatasetProvenance(
            datasetId=dataset_id,
            branchOf=branch_of,
            branches=branch_ids,
            documentDependencies=edges,
            computedAt=_now_iso8601(),
        )
        log.info(
            "dataset_provenance.build",
            dataset_id=dataset_id,
            branch_of=branch_of or "",
            branches=len(branch_ids),
            classes_walked=len(classes),
            edges=len(edges),
            ms=int((time.perf_counter() - t0) * 1000),
        )
        return prov

    # --- branches helper (tolerate 404 on /branches for older cloud builds) ---

    async def _branches_safely(
        self, dataset_id: str, *, access_token: str | None,
    ) -> list[dict[str, Any]]:
        try:
            return await self.cloud.get_dataset_branches(
                dataset_id, access_token=access_token,
            )
        except Exception as e:
            # A 404 from /branches means "no branches or endpoint not yet
            # deployed"; either way, fall back to empty rather than failing
            # the whole provenance build. /datasets/:id will still have
            # surfaced a NotFound if the dataset itself is missing.
            log.warning(
                "dataset_provenance.branches_failed",
                dataset_id=dataset_id,
                error=str(e),
            )
            return []

    # --- Cross-dataset aggregation --------------------------------------

    async def _aggregate_cross_dataset_edges(
        self,
        dataset_id: str,
        classes: list[str],
        *,
        access_token: str | None,
    ) -> list[DatasetDependencyEdge]:
        """Walk each document class in the dataset, collect every
        ``depends_on`` ndiId → dataset resolution, and emit one aggregated
        edge per ``(source, target, viaClass)`` tuple.
        """
        if not classes:
            return []

        bulk_sem = asyncio.Semaphore(_BULK_FETCH_CONCURRENCY)

        # Step 1: collect (via_class, target_ndi_id) pairs from every doc
        # in each walked class. Dedupe per-class so repeat (via_class,
        # ndi_id) pairs only trigger one resolution.
        pairs_by_class: dict[str, set[str]] = {}
        for class_name in classes:
            try:
                docs = await self._fetch_class_docs(
                    dataset_id, class_name,
                    access_token=access_token, sem=bulk_sem,
                )
            except Exception as e:
                # One class failing (e.g. transient 5xx) should not torpedo
                # the whole build. Log + continue — the edges we DO compute
                # are still accurate.
                log.warning(
                    "dataset_provenance.class_fetch_failed",
                    dataset_id=dataset_id,
                    class_name=class_name,
                    error=str(e),
                )
                continue
            for doc in docs:
                for ndi_id in _depends_on_ndi_ids(doc):
                    pairs_by_class.setdefault(class_name, set()).add(ndi_id)

        if not pairs_by_class:
            return []

        # Step 2: collect the universe of unique ndiIds across classes.
        # Resolve each once — we re-use the resolution across classes
        # because a ndiId's target dataset is class-invariant.
        unique_ids: set[str] = set()
        for ids in pairs_by_class.values():
            unique_ids.update(ids)
        if len(unique_ids) > _MAX_UNIQUE_TARGETS:
            log.warning(
                "dataset_provenance.target_cap_exceeded",
                dataset_id=dataset_id,
                unique_ids=len(unique_ids),
                cap=_MAX_UNIQUE_TARGETS,
            )
            # Truncate deterministically (sorted so tests see stable order).
            unique_ids = set(sorted(unique_ids)[:_MAX_UNIQUE_TARGETS])

        target_by_ndi = await self._resolve_target_datasets(
            list(unique_ids), access_token=access_token,
        )

        # Step 3: bucket by (target_dataset, via_class). Same-dataset edges
        # (target == source) are not cross-dataset and get dropped.
        counts: dict[tuple[str, str], int] = {}
        for class_name, ndi_ids in pairs_by_class.items():
            for ndi_id in ndi_ids:
                target_ds = target_by_ndi.get(ndi_id)
                if not target_ds or target_ds == dataset_id:
                    continue
                key = (target_ds, class_name)
                counts[key] = counts.get(key, 0) + 1

        edges = [
            DatasetDependencyEdge(
                sourceDatasetId=dataset_id,
                targetDatasetId=target_ds,
                viaDocumentClass=class_name,
                edgeCount=n,
            )
            for (target_ds, class_name), n in counts.items()
        ]
        # Stable ordering: target_ds asc, then via_class asc — the UI groups
        # by target, and stable order helps cache determinism.
        edges.sort(key=lambda e: (e.targetDatasetId, e.viaDocumentClass))
        return edges

    async def _fetch_class_docs(
        self,
        dataset_id: str,
        class_name: str,
        *,
        access_token: str | None,
        sem: asyncio.Semaphore,
    ) -> list[dict[str, Any]]:
        """``ndiquery isa=class_name scope=dataset_id`` → bulk-fetch all IDs
        in batches of ``BULK_FETCH_MAX`` under ``sem``.
        """
        body = await self.cloud.ndiquery(
            searchstructure=[{"operation": "isa", "param1": class_name}],
            scope=dataset_id,
            access_token=access_token,
        )
        ids = _extract_ids(body)
        if not ids:
            return []
        return await self._bulk_fetch_all(
            dataset_id, ids, access_token=access_token, sem=sem,
        )

    async def _bulk_fetch_all(
        self,
        dataset_id: str,
        ids: list[str],
        *,
        access_token: str | None,
        sem: asyncio.Semaphore,
    ) -> list[dict[str, Any]]:
        batches = [
            ids[i : i + BULK_FETCH_MAX]
            for i in range(0, len(ids), BULK_FETCH_MAX)
        ]

        async def _one(batch: list[str]) -> list[dict[str, Any]]:
            async with sem:
                return await self.cloud.bulk_fetch(
                    dataset_id, batch, access_token=access_token,
                )

        chunks = await asyncio.gather(*[_one(b) for b in batches])
        flat: list[dict[str, Any]] = []
        for c in chunks:
            flat.extend(c)
        return flat

    async def _resolve_target_datasets(
        self,
        ndi_ids: list[str],
        *,
        access_token: str | None,
    ) -> dict[str, str]:
        """Map each ndiId → owning dataset id.

        Uses ``ndiquery exact_string base.id=<ndi_id>`` scoped to ``public``
        (the widest scope anonymous callers can use). Authenticated callers
        pass their token along and the cloud includes private datasets the
        user has access to. Per the spike-0 Report A ``IDocument`` shape,
        the returned document carries a ``dataset`` field whose value is
        the owning MongoDB id.

        A ndiId that doesn't resolve (rare — deleted target, permission
        gap) is simply absent from the returned mapping.
        """
        if not ndi_ids:
            return {}
        sem = asyncio.Semaphore(_NDI_RESOLVE_CONCURRENCY)
        # Auth broadens the scope the cloud will search; anonymous callers
        # see only public datasets.
        scope = "all" if access_token else "public"

        async def _one(ndi_id: str) -> tuple[str, str | None]:
            async with sem:
                try:
                    body = await self.cloud.ndiquery(
                        searchstructure=[
                            {
                                "operation": "exact_string",
                                "field": "base.id",
                                "param1": ndi_id,
                            },
                        ],
                        scope=scope,
                        access_token=access_token,
                        page_size=5,
                        fetch_all=False,
                    )
                except Exception as e:
                    # Single-id resolution failure is tolerable — we just
                    # miss that edge. Log for observability.
                    log.warning(
                        "dataset_provenance.resolve_failed",
                        ndi_id=ndi_id,
                        error=str(e),
                    )
                    return (ndi_id, None)
                docs = body.get("documents") or []
                if not docs:
                    return (ndi_id, None)
                return (ndi_id, _owning_dataset_id(docs[0]))

        pairs = await asyncio.gather(*[_one(nid) for nid in ndi_ids])
        return {nid: ds for nid, ds in pairs if ds}


# ---------------------------------------------------------------------------
# Cache key helper (public for tests)
# ---------------------------------------------------------------------------

def provenance_cache_key(
    dataset_id: str, session: SessionData | None,
) -> str:
    """Per-user cache key so two users cannot share a cached entry — matches
    the PR-3 scoping scheme used by summary/dep-graph caches.
    """
    return f"{PROVENANCE_KEY_PREFIX}:{dataset_id}:{user_scope_for(session)}"


# ---------------------------------------------------------------------------
# Pure extraction helpers
# ---------------------------------------------------------------------------

def _branch_of_from_raw(raw: dict[str, Any]) -> str | None:
    """``IDataset.branchOf`` is an ObjectId on the cloud side, surfaced as
    a string on the JSON wire. Empty string / missing → ``None``.
    """
    v = raw.get("branchOf")
    if isinstance(v, str) and v.strip():
        return v.strip()
    return None


def _branch_ids_from_raw(branches: list[dict[str, Any]]) -> list[str]:
    """Pick the stringified id of each child dataset. The cloud returns
    ``id`` on list results and ``_id`` on a direct fetch; tolerate both
    so we don't couple to a single serializer shape.
    """
    ids: list[str] = []
    seen: set[str] = set()
    for b in branches:
        if not isinstance(b, dict):
            continue
        candidate = b.get("id") or b.get("_id")
        if not isinstance(candidate, str):
            continue
        stripped = candidate.strip()
        if not stripped or stripped in seen:
            continue
        seen.add(stripped)
        ids.append(stripped)
    return ids


def _classes_to_walk(counts_raw: dict[str, Any]) -> list[str]:
    """From the class-counts response, pick the top classes to walk. We walk
    classes sorted by count descending because high-count classes are most
    likely to carry depends_on edges (elements, epochs, spikesorting...);
    zero-count classes and ``unknown`` are skipped entirely.
    """
    class_counts = counts_raw.get("classCounts") or {}
    if not isinstance(class_counts, dict):
        return []
    ranked = sorted(
        (
            (name, int(n))
            for name, n in class_counts.items()
            if isinstance(name, str)
            and isinstance(n, (int, float))
            and int(n) > 0
            and name not in _CLASSES_SKIP
        ),
        key=lambda item: item[1],
        reverse=True,
    )
    return [name for name, _ in ranked[:_MAX_CLASSES_WALKED]]


def _depends_on_ndi_ids(doc: dict[str, Any]) -> list[str]:
    """Extract the depends_on values (ndiIds) from a document. Tolerate the
    cloud's single-dict-vs-list encoding (observed both in the wild, mirrors
    the normalization in dependency_graph_service).
    """
    deps = (doc.get("data") or {}).get("depends_on")
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


def _owning_dataset_id(doc: dict[str, Any]) -> str | None:
    """Read the owning dataset id off a document returned by ndiquery.

    Per the cloud's IDocument shape, the field is ``dataset`` at the root.
    Some older cloud serializers expose it as ``datasetId``; tolerate both.
    """
    for k in ("dataset", "datasetId", "dataset_id"):
        v = doc.get(k)
        if isinstance(v, str) and v.strip():
            return v.strip()
    return None


def _extract_ids(body: dict[str, Any]) -> list[str]:
    """Pull the MongoDB ids out of an ndiquery body. Tolerates both the
    legacy ``ids`` key and the modern ``documents[].id`` shape.
    """
    ids_field = body.get("ids")
    if isinstance(ids_field, list):
        return [i for i in ids_field if isinstance(i, str)]
    docs = body.get("documents") or []
    out: list[str] = []
    for d in docs:
        if isinstance(d, dict):
            v = d.get("id") or d.get("_id")
            if isinstance(v, str):
                out.append(v)
    return out


def _now_iso8601() -> str:
    return datetime.now(UTC).isoformat().replace("+00:00", "Z")


__all__ = [
    "PROVENANCE_CACHE_TTL_SECONDS",
    "PROVENANCE_KEY_PREFIX",
    "PROVENANCE_SCHEMA_VERSION",
    "DatasetDependencyEdge",
    "DatasetProvenance",
    "DatasetProvenanceService",
    "provenance_cache_key",
]
