"""Aggregate-documents service — Stream 4.9 (2026-05-16).

Closes ADR-001 (Heart-on-Railway) compliance debt: the original aggregation
ran on Vercel (TypeScript) and walked up to 50K documents in JS — wrong
runtime for that workload. This service mirrors the TS handler exactly so
the chat tool can be rewritten as a thin client.

Pipeline:
    1. Validate input (scope / searchstructure / valueField).
    2. Forward the searchstructure to `ndi.cloud /ndiquery` via the
       existing `NdiCloudClient.ndiquery` plumbing.
    3. Walk the returned documents, extract numeric values at
       ``valueField`` (dotted path under ``data.*``).
    4. Group by ``groupBy`` (dotted path) when set. Drop docs that have a
       numeric value but no group label so ``numeric_matches`` stays
       honest.
    5. Compute per-group stats (count, mean, median, std, min, max).
    6. Surface granular per-group sample docs + contributing datasets so
       the cloud-app TS client can build per-group / per-dataset
       References without re-walking the cloud response.

Cost guardrails:
    - ``max_docs`` caps the scan window at 50,000. Default 5,000 matches
      the TS handler.
    - Reference list capped at 30 (REFERENCE_CAP) — beyond that the
      chat's citation panel becomes wall-of-chips noise. Mirrors TS.
"""
from __future__ import annotations

import math
from typing import Any

from pydantic import BaseModel, Field, field_validator

from ..clients.ndi_cloud import NdiCloudClient
from .query_service import QueryRequest

# ---------------------------------------------------------------------------
# Bounds (mirror the TS handler's constants)
# ---------------------------------------------------------------------------

MAX_DOCS_DEFAULT = 5_000
MAX_DOCS_CEILING = 50_000
REFERENCE_CAP = 30


# ---------------------------------------------------------------------------
# Request shape
# ---------------------------------------------------------------------------

class AggregateDocumentsRequest(BaseModel):
    """Pydantic schema matching the TS `AggregateDocumentsInput`.

    `searchstructure` and `scope` are re-used from `QueryRequest`'s
    validation pattern (same ops allowlist, same scope grammar) — see
    `query_service.py` for the canonical contract.
    """

    scope: str = Field(..., min_length=1, max_length=2048)
    searchstructure: list[dict[str, Any]] = Field(..., min_length=1, max_length=20)
    valueField: str = Field(..., min_length=1, max_length=256)
    groupBy: str | None = Field(default=None, min_length=1, max_length=256)
    maxDocs: int | None = Field(default=None, ge=1, le=MAX_DOCS_CEILING)

    @field_validator("scope")
    @classmethod
    def _check_scope(cls, v: str) -> str:
        # Delegate to the same validator QueryRequest uses — keeps the
        # two endpoints in lockstep on which scopes are valid.
        return QueryRequest._check_scope(v)  # type: ignore[no-any-return]


# ---------------------------------------------------------------------------
# Service
# ---------------------------------------------------------------------------

class AggregateDocumentsService:
    """Stateless per-call aggregator over an `ndi_query` result set."""

    def __init__(self, cloud: NdiCloudClient) -> None:
        self.cloud = cloud

    async def aggregate(
        self,
        req: AggregateDocumentsRequest,
        *,
        access_token: str | None,
    ) -> dict[str, Any]:
        max_docs = req.maxDocs or MAX_DOCS_DEFAULT

        body = await self.cloud.ndiquery(
            searchstructure=[_normalize_node(n) for n in req.searchstructure],
            scope=req.scope,
            access_token=access_token,
        )

        all_docs = body.get("documents") or []
        if not isinstance(all_docs, list):
            all_docs = []
        total_items = int(body.get("totalItems") or len(all_docs))
        scanned = all_docs[:max_docs]
        truncated = total_items > len(scanned) or len(all_docs) > max_docs

        # Bucket values by group. When groupBy is unset all values fall into
        # the 'all' bucket. Per-group sample doc is the FIRST contributing
        # document so the chat can build a "one example from each bucket"
        # citation chip.
        buckets: dict[str, list[float]] = {}
        sample_docs: dict[str, dict[str, Any]] = {}
        group_order: list[str] = []
        numeric_matches = 0

        for doc in scanned:
            v = _extract_numeric(doc, req.valueField)
            if v is None:
                continue

            group_key = "all"
            if req.groupBy:
                g = _extract_string(doc, req.groupBy)
                # Numeric value exists but no group label → skip (matches the
                # TS handler's behavior so numeric_matches is honest).
                if g is None:
                    continue
                group_key = g

            numeric_matches += 1
            if group_key not in buckets:
                buckets[group_key] = []
                group_order.append(group_key)
                sample_docs[group_key] = doc
            buckets[group_key].append(v)

        groups: list[dict[str, Any]] = []
        for name in group_order:
            vals = buckets.get(name) or []
            if not vals:
                continue
            stats = _summary_stats(vals)
            sample = sample_docs.get(name)
            groups.append({
                "group": name,
                **stats,
                "sample_doc": _project_sample(sample) if sample else None,
            })

        # Contributing-dataset list (capped) for the TS client to build
        # dataset-level References without re-walking the scan window.
        datasets_contributing: list[str] = []
        seen: set[str] = set()
        for doc in scanned:
            ds = _doc_dataset_id(doc)
            if not ds or ds in seen:
                continue
            seen.add(ds)
            datasets_contributing.append(ds)
            if len(datasets_contributing) >= REFERENCE_CAP:
                break

        return {
            "total_items": total_items,
            "numeric_matches": numeric_matches,
            "truncated": truncated,
            "valueField": req.valueField,
            "scanned_docs": len(scanned),
            "groups": groups,
            "datasets_contributing": datasets_contributing,
        }


# ---------------------------------------------------------------------------
# Helpers — ported from apps/web/lib/ndi/tools/aggregate-documents.ts
# ---------------------------------------------------------------------------

def _normalize_node(n: dict[str, Any]) -> dict[str, Any]:
    """Strip None-valued keys so the cloud sees the same compact shape the
    TS client used to send."""
    out: dict[str, Any] = {"operation": n.get("operation")}
    for k in ("field", "param1", "param2"):
        if k in n and n[k] is not None:
            out[k] = n[k]
    return out


def _lookup_path(obj: Any, path: str) -> Any:
    """Walk a dotted path under an arbitrary nested dict. Returns None on
    any missing segment or non-dict ancestor."""
    if not path:
        return None
    cur: Any = obj
    for seg in path.split("."):
        if cur is None or not isinstance(cur, dict):
            return None
        cur = cur.get(seg)
    return cur


def _extract_numeric(doc: dict[str, Any], path: str) -> float | None:
    """Pull a finite numeric value at ``path``. Coerces string-encoded
    numbers (e.g. ``"3.14"`` → 3.14) the same way the TS helper does.
    Returns None when the path is missing OR the value is NaN/Inf."""
    raw = _lookup_path(doc, path)
    if isinstance(raw, bool):
        # bools are technically int subclasses in Python; the TS code
        # accepts numbers only.
        return None
    if isinstance(raw, (int, float)):
        return float(raw) if math.isfinite(float(raw)) else None
    if isinstance(raw, str):
        try:
            parsed = float(raw)
        except ValueError:
            return None
        return parsed if math.isfinite(parsed) else None
    return None


def _extract_string(doc: dict[str, Any], path: str) -> str | None:
    """Pull a non-empty string value at ``path``. Coerces booleans and
    numbers to strings (mirrors the TS helper) so groupBy works against
    numeric / boolean group labels."""
    raw = _lookup_path(doc, path)
    if isinstance(raw, str):
        return raw if len(raw) > 0 else None
    if isinstance(raw, bool):
        return "true" if raw else "false"
    if isinstance(raw, (int, float)):
        return str(raw)
    return None


def _summary_stats(values: list[float]) -> dict[str, float]:
    """count / mean / median / std / min / max over a non-empty list.

    Uses the sample standard deviation (N-1 denominator) when len >= 2 to
    match the TS handler. Median uses the linear-interpolation midpoint
    for even-length lists.
    """
    n = len(values)
    sorted_vals = sorted(values)
    mean = sum(sorted_vals) / n
    if n % 2 == 1:
        median = sorted_vals[(n - 1) // 2]
    else:
        median = (sorted_vals[n // 2 - 1] + sorted_vals[n // 2]) / 2
    if n >= 2:
        sq = sum((v - mean) * (v - mean) for v in sorted_vals)
        std = math.sqrt(sq / (n - 1))
    else:
        std = 0.0
    return {
        "count": n,
        "mean": mean,
        "median": median,
        "std": std,
        "min": sorted_vals[0],
        "max": sorted_vals[-1],
    }


def _doc_dataset_id(doc: dict[str, Any]) -> str | None:
    """Best-effort dataset id extraction. Cloud responses use either
    ``datasetId`` or ``dataset`` depending on age of the doc.
    """
    ds = doc.get("datasetId") or doc.get("dataset")
    return str(ds) if ds else None


def _project_sample(doc: dict[str, Any]) -> dict[str, Any] | None:
    """Compact per-group sample doc for the TS client's chip-builder.

    Only carries the three fields the client needs to build a Reference:
    doc id, dataset id, class name. Stripping the rest keeps the response
    small (the chat is the primary consumer; bigger responses bloat the
    token budget for the LLM's tool result).
    """
    doc_id = doc.get("id") or doc.get("_id") or doc.get("ndiId")
    dataset_id = _doc_dataset_id(doc)
    cls = (
        (doc.get("document_class") or {}).get("class_name")
        if isinstance(doc.get("document_class"), dict)
        else None
    ) or "document"
    if not doc_id or not dataset_id:
        return None
    return {
        "id": str(doc_id),
        "dataset_id": dataset_id,
        "class": str(cls),
    }
