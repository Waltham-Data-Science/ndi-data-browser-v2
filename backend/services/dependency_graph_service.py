"""Document dependency graph walker — bounded BFS via indexed ndiquery.

Walks both directions from a target document up to `max_depth` levels:

- Upstream: each node's own `data.depends_on[].value` ndiIds → resolve
  each to its mongo id via `ndiquery exact_string base.id=<ndiId>`, then
  bulk-fetch the bodies so we can continue walking their depends_on.

- Downstream: `ndiquery depends_on * [ndiId]` returns all docs that
  depend on the current node. We only need `{id, ndiId, name, className}`
  per hit — no further doc body needed since we pivot on ndiId for the
  next round.

Hard clamp `max_depth=3` per plan §M5 backend step 2 to bound cloud cost.
Response includes `truncated: bool` so the frontend renders a banner when
the graph has unexpanded frontiers.

Cached in Redis for 10 min per `(dataset_id, doc_id)`.
"""
from __future__ import annotations

import asyncio
import time
from typing import Any

from ..cache.redis_table import RedisTableCache
from ..clients.ndi_cloud import BULK_FETCH_MAX, NdiCloudClient
from ..observability.logging import get_logger
from .document_service import _normalize_document

log = get_logger(__name__)

DEP_GRAPH_TTL_SECONDS = 600  # 10 minutes
MAX_DEPTH_HARD_CAP = 3  # plan §M5 backend step 2
MAX_NODES_HARD_CAP = 500  # defensive guard for pathological graphs
MAX_CONCURRENT_RESOLUTIONS = 8


class DependencyGraphService:
    def __init__(
        self,
        cloud: NdiCloudClient,
        *,
        cache: RedisTableCache | None = None,
    ) -> None:
        self.cloud = cloud
        self.cache = cache

    async def get_graph(
        self,
        dataset_id: str,
        document_id: str,
        *,
        max_depth: int = 3,
        access_token: str | None,
    ) -> dict[str, Any]:
        depth = max(1, min(MAX_DEPTH_HARD_CAP, int(max_depth or 1)))
        if self.cache is not None:
            key = _dep_graph_key(
                dataset_id, document_id, depth, authed=access_token is not None,
            )
            return await self.cache.get_or_compute(
                key,
                lambda: self._build_graph(
                    dataset_id, document_id, depth, access_token=access_token,
                ),
            )
        return await self._build_graph(
            dataset_id, document_id, depth, access_token=access_token,
        )

    async def _build_graph(  # noqa: PLR0912, PLR0915 — single BFS orchestrator; splitting would obscure the frontier bookkeeping
        self,
        dataset_id: str,
        document_id: str,
        max_depth: int,
        *,
        access_token: str | None,
    ) -> dict[str, Any]:
        t0 = time.perf_counter()
        target_raw = await self.cloud.get_document(
            dataset_id, document_id, access_token=access_token,
        )
        target = _normalize_document(target_raw)
        target_ndi = _ndi_id(target)
        if not target_ndi:
            log.warning("dep_graph.target_missing_ndi_id", document_id=document_id)
            return _empty_graph(document_id, reason="target has no ndiId")

        nodes: dict[str, dict[str, Any]] = {}
        edges: list[dict[str, Any]] = []
        truncated = False

        _add_node(
            nodes,
            ndi_id=target_ndi,
            mongo_id=target.get("id") or target.get("_id"),
            name=_doc_name(target),
            class_name=_class_name(target),
            is_target=True,
        )

        # Upstream BFS — need full doc bodies to keep walking depends_on.
        upstream_frontier: list[tuple[dict[str, Any], int]] = [(target, 0)]
        while upstream_frontier:
            next_frontier: list[tuple[dict[str, Any], int]] = []
            for doc, depth in upstream_frontier:
                if depth >= max_depth:
                    if _depends_on_edges(doc):
                        truncated = True
                    continue
                if len(nodes) >= MAX_NODES_HARD_CAP:
                    truncated = True
                    break
                edges_from_doc = _depends_on_edges(doc)
                dep_ndis = list(dict.fromkeys(e["value"] for e in edges_from_doc if e.get("value")))
                if not dep_ndis:
                    continue
                # Resolve each ndiId → mongo metadata in parallel.
                resolved = await self._resolve_ndi_ids(
                    dataset_id, dep_ndis, access_token=access_token,
                )
                unresolved = [d for d in dep_ndis if d not in resolved]
                if unresolved:
                    log.info(
                        "dep_graph.unresolved_upstream",
                        count=len(unresolved),
                        sample=unresolved[:3],
                    )
                # Bulk-fetch the bodies we need for continued upstream walking.
                mongo_ids = [m["id"] for m in resolved.values() if m.get("id")]
                bodies: list[dict[str, Any]] = []
                if mongo_ids:
                    for i in range(0, len(mongo_ids), BULK_FETCH_MAX):
                        batch = mongo_ids[i : i + BULK_FETCH_MAX]
                        try:
                            chunk = await self.cloud.bulk_fetch(
                                dataset_id, batch, access_token=access_token,
                            )
                            bodies.extend(chunk)
                        except Exception as e:
                            log.warning("dep_graph.upstream_bulk_fetch_failed", error=str(e))
                by_ndi_body = {_ndi_id(b): b for b in bodies if _ndi_id(b)}

                current_ndi = _ndi_id(doc) or target_ndi
                for edge_desc in edges_from_doc:
                    dep_ndi = edge_desc.get("value")
                    if not dep_ndi:
                        continue
                    meta = resolved.get(dep_ndi)
                    body = by_ndi_body.get(dep_ndi)
                    class_name = (
                        (meta or {}).get("className")
                        or _class_name(body)
                        or ""
                    )
                    node_name = _doc_name(body) or (meta or {}).get("name") or ""
                    mongo = (meta or {}).get("id")
                    _add_node(
                        nodes,
                        ndi_id=dep_ndi,
                        mongo_id=mongo,
                        name=node_name,
                        class_name=class_name,
                    )
                    edges.append({
                        "source": current_ndi,
                        "target": dep_ndi,
                        "label": edge_desc.get("name") or "depends_on",
                        "direction": "upstream",
                    })
                    if body is not None:
                        next_frontier.append((body, depth + 1))
                if len(nodes) >= MAX_NODES_HARD_CAP:
                    truncated = True
                    break
            upstream_frontier = next_frontier

        # Downstream BFS — only need node metadata to pivot on ndiId.
        seen_downstream: set[str] = {target_ndi}
        current_level: list[str] = [target_ndi]
        for _ in range(max_depth):
            if not current_level or len(nodes) >= MAX_NODES_HARD_CAP:
                if len(nodes) >= MAX_NODES_HARD_CAP and current_level:
                    truncated = True
                break
            next_level: list[str] = []
            results = await asyncio.gather(
                *[
                    self._downstream_of(dataset_id, ndi, access_token=access_token)
                    for ndi in current_level
                ],
                return_exceptions=True,
            )
            for ndi, r in zip(current_level, results, strict=True):
                if isinstance(r, BaseException):
                    log.warning("dep_graph.downstream_query_failed", ndi=ndi, error=str(r))
                    continue
                downstream_docs, n_matches = r
                if n_matches > len(downstream_docs):
                    truncated = True
                for d in downstream_docs:
                    dep_ndi = d.get("ndiId")
                    if not dep_ndi:
                        continue
                    _add_node(
                        nodes,
                        ndi_id=dep_ndi,
                        mongo_id=d.get("id"),
                        name=d.get("name") or "",
                        class_name=d.get("className") or "",
                    )
                    edges.append({
                        "source": dep_ndi,
                        "target": ndi,
                        "label": "depends_on",
                        "direction": "downstream",
                    })
                    if dep_ndi not in seen_downstream:
                        seen_downstream.add(dep_ndi)
                        next_level.append(dep_ndi)
                    if len(nodes) >= MAX_NODES_HARD_CAP:
                        truncated = True
                        break
            current_level = next_level

        elapsed_ms = int((time.perf_counter() - t0) * 1000)
        log.info(
            "dep_graph.build",
            dataset_id=dataset_id,
            document_id=document_id,
            max_depth=max_depth,
            nodes=len(nodes),
            edges=len(edges),
            truncated=truncated,
            ms=elapsed_ms,
        )
        return {
            "target_id": document_id,
            "target_ndi_id": target_ndi,
            "nodes": list(nodes.values()),
            "edges": _deduplicate_edges(edges),
            "node_count": len(nodes),
            "edge_count": len(edges),
            "truncated": truncated,
            "max_depth": max_depth,
        }

    async def _resolve_ndi_ids(
        self,
        dataset_id: str,
        ndi_ids: list[str],
        *,
        access_token: str | None,
    ) -> dict[str, dict[str, Any]]:
        """Map each ndiId → {id (mongo), ndiId, name, className} via
        `ndiquery exact_string base.id=<ndiId>`. Empty for unresolvable ids.
        """
        if not ndi_ids:
            return {}
        sem = asyncio.Semaphore(MAX_CONCURRENT_RESOLUTIONS)

        async def one(nid: str) -> tuple[str, dict[str, Any] | None]:
            async with sem:
                try:
                    body = await self.cloud.ndiquery(
                        searchstructure=[
                            {"operation": "exact_string", "field": "base.id", "param1": nid},
                        ],
                        scope=dataset_id,
                        access_token=access_token,
                        page_size=5,
                        fetch_all=False,
                    )
                except Exception as e:
                    log.warning("dep_graph.resolve_failed", ndi=nid, error=str(e))
                    return (nid, None)
                docs = body.get("documents") or []
                if not docs:
                    return (nid, None)
                d = docs[0]
                return (nid, {
                    "id": d.get("id"),
                    "ndiId": d.get("ndiId"),
                    "name": d.get("name"),
                    "className": d.get("className"),
                })

        pairs = await asyncio.gather(*[one(n) for n in ndi_ids])
        return {nid: meta for nid, meta in pairs if meta is not None}

    async def _downstream_of(
        self,
        dataset_id: str,
        ndi_id: str,
        *,
        access_token: str | None,
    ) -> tuple[list[dict[str, Any]], int]:
        """Return (downstream_docs, total_matches). The downstream query uses
        the indexed depends_on path — fast even on dense datasets."""
        body = await self.cloud.ndiquery(
            searchstructure=[
                {"operation": "depends_on", "param1": "*", "param2": [ndi_id]},
            ],
            scope=dataset_id,
            access_token=access_token,
            page_size=200,
            fetch_all=False,
        )
        docs = body.get("documents") or []
        total = int(body.get("number_matches") or body.get("totalItems") or len(docs))
        return list(docs), total


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _depends_on_edges(doc: dict[str, Any] | None) -> list[dict[str, Any]]:
    """Return depends_on as a list of {name, value} regardless of the cloud's
    single-dict-vs-list encoding, filtering entries with empty values."""
    if not doc:
        return []
    deps = (doc.get("data") or {}).get("depends_on")
    if deps is None:
        return []
    if isinstance(deps, dict):
        deps = [deps]
    if not isinstance(deps, list):
        return []
    out: list[dict[str, Any]] = []
    for d in deps:
        if not isinstance(d, dict):
            continue
        v = d.get("value")
        if isinstance(v, str) and v:
            out.append({"name": d.get("name") or "depends_on", "value": v})
    return out


def _ndi_id(doc: dict[str, Any] | None) -> str | None:
    if not doc:
        return None
    base = (doc.get("data") or {}).get("base") or {}
    return base.get("id") or doc.get("ndiId")


def _doc_name(doc: dict[str, Any] | None) -> str:
    if not doc:
        return ""
    return (
        doc.get("name")
        or ((doc.get("data") or {}).get("base") or {}).get("name")
        or ""
    )


def _class_name(doc: dict[str, Any] | None) -> str:
    if not doc:
        return ""
    return (
        doc.get("className")
        or ((doc.get("data") or {}).get("document_class") or {}).get("class_name")
        or ""
    )


def _add_node(
    nodes: dict[str, dict[str, Any]],
    *,
    ndi_id: str,
    mongo_id: str | None,
    name: str,
    class_name: str,
    is_target: bool = False,
) -> None:
    """Idempotent insert. Preserves isTarget=True once set and prefers richer
    metadata on re-visits (e.g. downstream pass fills in a class_name we
    didn't see in the upstream pass)."""
    existing = nodes.get(ndi_id)
    if existing is None:
        nodes[ndi_id] = {
            "id": mongo_id,
            "ndiId": ndi_id,
            "name": name,
            "className": class_name,
            "isTarget": is_target,
        }
        return
    if is_target:
        existing["isTarget"] = True
    if not existing.get("id") and mongo_id:
        existing["id"] = mongo_id
    if not existing.get("name") and name:
        existing["name"] = name
    if not existing.get("className") and class_name:
        existing["className"] = class_name


def _deduplicate_edges(edges: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Collapse identical (source, target, direction) tuples. Preserves the
    first-seen label."""
    seen: set[tuple[str, str, str]] = set()
    out: list[dict[str, Any]] = []
    for e in edges:
        key = (e["source"], e["target"], e["direction"])
        if key in seen:
            continue
        seen.add(key)
        out.append(e)
    return out


def _empty_graph(document_id: str, *, reason: str) -> dict[str, Any]:
    return {
        "target_id": document_id,
        "target_ndi_id": None,
        "nodes": [],
        "edges": [],
        "node_count": 0,
        "edge_count": 0,
        "truncated": False,
        "max_depth": 0,
        "error": reason,
    }


def _dep_graph_key(
    dataset_id: str, doc_id: str, depth: int, *, authed: bool,
) -> str:
    """Namespaced separately from table cache. 10-min TTL.

    Includes `RedisTableCache.SCHEMA_VERSION` so shape changes invalidate
    stale blobs immediately on deploy rather than waiting for TTL.
    """
    mode = "authed" if authed else "public"
    return f"depgraph:{RedisTableCache.SCHEMA_VERSION}:{dataset_id}:{doc_id}:{depth}:{mode}"


# Ensure RedisTableCache TTL is 10 min for this key-space. Keep the cache
# object itself reusable by writing a thin wrapper that overrides ttl at
# set time — RedisTableCache uses its ttl_seconds field for all writes, so
# we accept one-TTL-per-cache and document the dep graph to reuse the
# table cache's TTL OR inject a second cache instance.
#
# Actual wiring in app.py creates a DepGraphCache (RedisTableCache with
# ttl_seconds=DEP_GRAPH_TTL_SECONDS) and injects that.
