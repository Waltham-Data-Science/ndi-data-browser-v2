"""tabular_query_service — aggregate ``ontologyTableRow`` documents
into per-group statistics + raw values for violin/jitter rendering.

Used by the chat's ``tabular_query`` tool. The chat passes a substring
match against an ``ontologyTableRow`` column name (e.g.
``"ElevatedPlusMaze_OpenArmNorth_Entries"``) plus an optional
grouping column key (e.g. ``"treatment_group"``). The service:

1. Calls :meth:`SummaryTableService.ontology_tables` which projects
   the dataset's ``ontologyTableRow`` docs into one group per
   distinct ``variableNames`` schema
2. Finds the first group containing a column whose key/label matches
   the substring; that column is the value column
3. If ``groupBy`` is given, finds the column with that key inside the
   same group; that's the grouping column
4. Iterates rows (each row is a dict keyed by column key), bucketing
   numeric values by group label
5. Computes per-group stats (mean, median, std, min/max, q1/q3,
   count) plus the raw values (capped + stride-sampled) for the
   violin's jitter overlay
6. Returns the response shape :class:`ViolinChart` consumes

Notable: this service does NOT call NDI-python — it operates on the
already-decoded ``ontologyTableRow`` shape that
``SummaryTableService`` projects from cloud-node. NDI-python becomes
valuable on the binary/decoding side, not the tabular-aggregation
side. Keeping this service pure-Python (statistics module only) keeps
it fast + side-effect-free.
"""
from __future__ import annotations

import math
import statistics
from typing import Any

from ..auth.session import SessionData
from ..observability.logging import get_logger
from .summary_table_service import SummaryTableService

log = get_logger(__name__)


# Bound the response size — a violin with 100 groups isn't a chart,
# it's a wall of text. The chat tool's `groupOrder` parameter is the
# right escape hatch when callers really want a curated subset.
MAX_GROUPS = 20

# Per-group raw-value cap. Plotly's violin trace can comfortably
# render ~500 jitter points per group before the chart slows down on
# resize. Beyond that we stride-sample. The summary stats are computed
# on the FULL value list before sampling, so they remain accurate.
MAX_VALUES_PER_GROUP = 500


class TabularQueryService:
    """Aggregate ontologyTableRow docs into per-group stats."""

    def __init__(self, summary: SummaryTableService) -> None:
        self.summary = summary

    async def violin_groups(  # noqa: PLR0911 (linear-control-flow with early-return per failure mode is clearer than a state machine)
        self,
        dataset_id: str,
        variable_name_contains: str,
        *,
        group_by: str | None,
        group_order: list[str] | None,
        session: SessionData | None,
    ) -> dict[str, Any]:
        """Return ``{groups: [...], yLabel, xLabel, source?}``.

        Each group has the shape consumed by
        ``apps/web/components/charts/ViolinChart.tsx``::

            {name, values, count, mean, median, std, min, max, q1, q3}
        """
        if not variable_name_contains:
            return _empty_response(group_by, reason="empty variableNameContains")

        ontology = await self.summary.ontology_tables(dataset_id, session=session)
        groups = ontology.get("groups", [])
        if not groups:
            return _empty_response(
                group_by, reason="no ontologyTableRow docs in dataset",
            )

        match = _find_matching_group(groups, variable_name_contains)
        if match is None:
            return _empty_response(
                group_by,
                reason=f"no ontologyTableRow column matched '{variable_name_contains}'",
                available={"variable_names": [
                    " | ".join(g.get("variableNames", []))[:120]
                    for g in groups[:5]
                ]},
            )

        group, value_col, value_label = match
        rows = (group.get("table") or {}).get("rows") or []
        if not rows:
            return _empty_response(
                group_by,
                reason="matched group had no rows",
                yLabel=value_label,
            )

        # Resolve the groupBy column. Like the value column, callers
        # rarely know the exact column key — substring-match against the
        # group's columns (key OR label, case-insensitive). When the user
        # leaves group_by unset, this returns None and the bucketing
        # produces a single 'all' group.
        resolved_group_col = (
            _resolve_group_column(group, group_by) if group_by else None
        )
        if group_by and resolved_group_col is None:
            return _empty_response(
                group_by,
                reason=f"no column matched groupBy '{group_by}' in the "
                       f"selected table",
                yLabel=value_label,
                available={"columns": [
                    c.get("key")
                    for c in (group.get("table") or {}).get("columns") or []
                    if c.get("key") != value_col
                ][:20]},
            )

        buckets, order_seen = _bucket_rows(rows, value_col, resolved_group_col)
        if not buckets:
            return _empty_response(
                group_by,
                reason="no numeric values in matched column",
                yLabel=value_label,
            )

        ordered_keys = _ordered_group_keys(buckets, order_seen, group_order)
        out_groups = _build_group_payloads(buckets, ordered_keys)

        result: dict[str, Any] = {
            "groups": out_groups,
            "yLabel": value_label,
            "xLabel": group_by or "group",
        }
        doc_ids = group.get("docIds") or []
        if doc_ids:
            result["source"] = {
                "dataset_id": dataset_id,
                "document_id": doc_ids[0],
                "variable_name": value_label,
            }
        return result


# ---------------------------------------------------------------------------
# Internal helpers — each is single-purpose so the orchestrator stays linear.
# ---------------------------------------------------------------------------


def _empty_response(
    group_by: str | None,
    *,
    reason: str,
    yLabel: str = "",
    available: dict[str, Any] | None = None,
) -> dict[str, Any]:
    meta: dict[str, Any] = {"reason": reason}
    if available:
        meta.update(available)
    return {
        "groups": [],
        "yLabel": yLabel,
        "xLabel": group_by or "",
        "_meta": meta,
    }


def _find_matching_group(
    groups: list[dict[str, Any]],
    needle: str,
) -> tuple[dict[str, Any], str, str] | None:
    """Locate the best ontologyTableRow column matching the search
    substring, preferring columns whose values are numeric.

    Real ontologyTableRow tables typically have multiple columns whose
    names share the same topic prefix (e.g. ``ElevatedPlusMaze: Test
    Identifier`` + ``ElevatedPlusMaze: Open Arm Entries`` + …). A naive
    first-match would pick the identifier column → no numeric values →
    empty violin. We instead score each matching column by how many
    rows have finite-numeric values in it, and return the highest-
    scoring column across all groups.

    Ties broken by first-seen order (group order is already sorted by
    row count desc in SummaryTableService).
    """
    needle_lower = needle.lower()
    best: tuple[dict[str, Any], str, str, int] | None = None
    for g in groups:
        table = g.get("table") or {}
        cols = table.get("columns") or []
        rows = table.get("rows") or []
        for col in cols:
            key = str(col.get("key", ""))
            label = str(col.get("label", ""))
            if needle_lower not in key.lower() and needle_lower not in label.lower():
                continue
            numeric_count = sum(1 for row in rows if _is_finite_numeric(row.get(key)))
            if numeric_count == 0:
                continue
            if best is None or numeric_count > best[3]:
                best = (g, key, label or key, numeric_count)
    if best is None:
        return None
    return best[0], best[1], best[2]


def _resolve_group_column(group: dict[str, Any], group_by: str) -> str | None:
    """Resolve a possibly-imprecise group_by argument to an actual
    column key in the matched group.

    Substring-match against `key` first (exact key wins immediately),
    then against `label`. Returns None when nothing matches so the
    caller can surface an explicit error.
    """
    needle_lower = group_by.lower()
    cols = (group.get("table") or {}).get("columns") or []
    # Exact key match wins immediately.
    for col in cols:
        if str(col.get("key", "")) == group_by:
            return group_by
    # Substring fallback — key first (more stable than labels).
    for col in cols:
        if needle_lower in str(col.get("key", "")).lower():
            return str(col["key"])
    for col in cols:
        if needle_lower in str(col.get("label", "")).lower():
            return str(col["key"])
    return None


def _is_finite_numeric(v: Any) -> bool:
    """Defensive coerce — `True` only when `v` parses to a finite float."""
    if v is None:
        return False
    try:
        return math.isfinite(float(v))
    except (TypeError, ValueError):
        return False


def _bucket_rows(
    rows: list[dict[str, Any]],
    value_col: str,
    group_by: str | None,
) -> tuple[dict[str, list[float]], list[str]]:
    """Walk rows, extract numeric value + grouping label.

    Returns (buckets_by_group_name, order_seen).
    """
    buckets: dict[str, list[float]] = {}
    order_seen: list[str] = []
    for row in rows:
        v_raw = row.get(value_col)
        if v_raw is None:
            continue
        try:
            v = float(v_raw)
        except (TypeError, ValueError):
            continue
        if not math.isfinite(v):
            continue
        if group_by:
            g_raw = row.get(group_by)
            if g_raw is None:
                continue
            g = str(g_raw)
        else:
            g = "all"
        if g not in buckets:
            buckets[g] = []
            order_seen.append(g)
        buckets[g].append(v)
    return buckets, order_seen


def _ordered_group_keys(
    buckets: dict[str, list[float]],
    order_seen: list[str],
    group_order: list[str] | None,
) -> list[str]:
    """Resolve final group ordering. Caller's explicit `group_order`
    wins; unspecified groups append at the end (never silently
    dropped); finally capped to MAX_GROUPS."""
    if group_order:
        ordered = [g for g in group_order if g in buckets]
        for g in order_seen:
            if g not in ordered:
                ordered.append(g)
    else:
        ordered = list(order_seen)
    return ordered[:MAX_GROUPS]


def _build_group_payloads(
    buckets: dict[str, list[float]],
    ordered_keys: list[str],
) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for name in ordered_keys:
        vals = buckets.get(name) or []
        if not vals:
            continue
        stats = _summary_stats(vals)
        # Cap raw values for the response payload — stats above were
        # computed on the FULL list so they remain accurate.
        sampled = _stride_sample(vals, MAX_VALUES_PER_GROUP)
        out.append({
            "name": name,
            "values": sampled,
            **stats,
        })
    return out


def _summary_stats(values: list[float]) -> dict[str, float | int]:
    """Compute the stats payload ViolinChart expects."""
    n = len(values)
    sorted_v = sorted(values)
    mean = statistics.fmean(values)
    median = statistics.median(values)
    std = statistics.stdev(values) if n >= 2 else 0.0
    # Linear-interpolated percentile — matches numpy.percentile default
    # closely enough for chart annotation purposes.
    q1 = _percentile(sorted_v, 25)
    q3 = _percentile(sorted_v, 75)
    return {
        "count": n,
        "mean": float(mean),
        "median": float(median),
        "std": float(std),
        "min": float(sorted_v[0]),
        "max": float(sorted_v[-1]),
        "q1": float(q1),
        "q3": float(q3),
    }


def _percentile(sorted_values: list[float], p: float) -> float:
    """Linear-interpolated percentile on a pre-sorted list."""
    if not sorted_values:
        return 0.0
    if len(sorted_values) == 1:
        return sorted_values[0]
    rank = (p / 100.0) * (len(sorted_values) - 1)
    lo = math.floor(rank)
    hi = math.ceil(rank)
    if lo == hi:
        return sorted_values[lo]
    frac = rank - lo
    return sorted_values[lo] * (1 - frac) + sorted_values[hi] * frac


def _stride_sample(values: list[float], cap: int) -> list[float]:
    """Stride-sample to (at most) `cap` points. Preserves first + last
    via linspace-style stepping so the violin's jitter overlay shows
    the distribution shape end-to-end."""
    n = len(values)
    if n <= cap:
        return list(values)
    if cap <= 2:
        return [values[0], values[-1]][:cap]
    step = (n - 1) / (cap - 1)
    indices = [round(i * step) for i in range(cap)]
    # Dedupe in case rounding collapses adjacent indices (rare;
    # happens only when `cap` approaches `n`).
    seen: set[int] = set()
    picked: list[int] = []
    for i in indices:
        if i not in seen:
            seen.add(i)
            picked.append(i)
    return [values[i] for i in picked]
