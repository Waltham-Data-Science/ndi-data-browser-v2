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

# Sample-row docId cap per group. The frontend builds one
# click-through citation chip per docId (e.g. "Sample Saline row"),
# so 3 per group keeps the chip count manageable on charts with many
# groups while still letting the user verify each group's data. The
# full set of contributing rows is reachable from the table-view
# citation (the primary chip).
MAX_DOC_IDS_PER_GROUP = 3


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

        # docIds is parallel to rows (same index) per
        # SummaryTableService.ontology_tables contract.
        parallel_doc_ids = group.get("docIds") or []
        buckets, bucket_doc_ids, order_seen = _bucket_rows(
            rows, parallel_doc_ids, value_col, resolved_group_col,
        )
        if not buckets:
            return _empty_response(
                group_by,
                reason="no numeric values in matched column",
                yLabel=value_label,
            )

        ordered_keys = _ordered_group_keys(buckets, order_seen, group_order)
        out_groups = _build_group_payloads(
            buckets, bucket_doc_ids, ordered_keys,
        )

        result: dict[str, Any] = {
            "groups": out_groups,
            "yLabel": value_label,
            "xLabel": group_by or "group",
        }
        # `source` is preserved for backwards compat — the per-group
        # `docIds` arrays on each entry of `groups` are the granular
        # truth. A consumer that only wants a single representative doc
        # still has `source.document_id`; consumers that want per-group
        # sample-row drill-downs read `groups[i].docIds`.
        if parallel_doc_ids:
            result["source"] = {
                "dataset_id": dataset_id,
                "document_id": parallel_doc_ids[0],
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


def _alphanumeric_lower(s: str) -> str:
    """Lowercase + strip non-alphanumerics for fuzzy substring matching.

    Stream 5.1 (2026-05-15): real column keys in ontologyTableRow tables
    use underscores and CamelCase intermixed
    (``ElevatedPlusMaze_OpenArmNorth_Entries``), while users / the chat
    sometimes type contiguous CamelCase (``OpenArmNorthEntries``). A
    direct case-insensitive substring match misses these because the
    underscore breaks contiguity. Normalizing BOTH sides to
    alphanumeric-only lowercase makes the comparison whitespace- and
    punctuation-insensitive without changing the contiguity check.
    """
    return "".join(ch for ch in s.lower() if ch.isalnum())


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

    Match strategy is two-pass (Stream 5.1, 2026-05-15):
      1. Direct case-insensitive substring match on key OR label
         (precise; preserves existing semantics).
      2. Alphanumeric-stripped fallback when pass 1 returns no
         numeric-column hit. Catches the `OpenArmNorthEntries` ↔
         `ElevatedPlusMaze_OpenArmNorth_Entries` mismatch.

    Ties broken by first-seen order (group order is already sorted by
    row count desc in SummaryTableService).
    """
    needle_lower = needle.lower()
    needle_alnum = _alphanumeric_lower(needle)
    # Pass 1 + Pass 2 share the loop; we capture the best precise hit
    # first, then the best fuzzy hit. Precise wins on equal numeric
    # counts.
    best_precise: tuple[dict[str, Any], str, str, int] | None = None
    best_fuzzy: tuple[dict[str, Any], str, str, int] | None = None
    for g in groups:
        table = g.get("table") or {}
        cols = table.get("columns") or []
        rows = table.get("rows") or []
        for col in cols:
            key = str(col.get("key", ""))
            label = str(col.get("label", ""))
            key_lower = key.lower()
            label_lower = label.lower()
            is_precise = (
                needle_lower in key_lower or needle_lower in label_lower
            )
            # Skip fuzzy work when precise already matches (saves the
            # alnum compute on huge tables).
            is_fuzzy = is_precise or (
                needle_alnum
                and (
                    needle_alnum in _alphanumeric_lower(key)
                    or needle_alnum in _alphanumeric_lower(label)
                )
            )
            if not is_fuzzy:
                continue
            numeric_count = sum(1 for row in rows if _is_finite_numeric(row.get(key)))
            if numeric_count == 0:
                continue
            tuple_ = (g, key, label or key, numeric_count)
            if is_precise:
                if best_precise is None or numeric_count > best_precise[3]:
                    best_precise = tuple_
            elif best_fuzzy is None or numeric_count > best_fuzzy[3]:
                best_fuzzy = tuple_
    best = best_precise if best_precise is not None else best_fuzzy
    if best is None:
        return None
    return best[0], best[1], best[2]


def _resolve_group_column(  # noqa: PLR0911 — linear three-pass match is clearer than one collapsed branch
    group: dict[str, Any],
    group_by: str,
) -> str | None:
    """Resolve a possibly-imprecise group_by argument to an actual
    column key in the matched group.

    Three-pass resolution (Stream 5.1 expanded 2026-05-15):
      1. Exact key match (literal column-key argument from the user).
      2. Case-insensitive substring match on key, then on label
         (preserves precision for column-name fragments).
      3. Alphanumeric-stripped substring match on key, then label —
         catches the `Treatment_CNOOrSaline` ↔ `CNOorSaline` shape
         where users mix underscore + CamelCase variants.

    Returns None when nothing matches so the caller can surface an
    explicit error with the available column list.
    """
    needle_lower = group_by.lower()
    needle_alnum = _alphanumeric_lower(group_by)
    cols = (group.get("table") or {}).get("columns") or []
    # Pass 1: exact key match wins immediately.
    for col in cols:
        if str(col.get("key", "")) == group_by:
            return group_by
    # Pass 2: case-insensitive substring — key first (more stable
    # than labels).
    for col in cols:
        if needle_lower in str(col.get("key", "")).lower():
            return str(col["key"])
    for col in cols:
        if needle_lower in str(col.get("label", "")).lower():
            return str(col["key"])
    # Pass 3: alphanumeric-stripped substring fallback.
    if not needle_alnum:
        return None
    for col in cols:
        if needle_alnum in _alphanumeric_lower(str(col.get("key", ""))):
            return str(col["key"])
    for col in cols:
        if needle_alnum in _alphanumeric_lower(str(col.get("label", ""))):
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
    parallel_doc_ids: list[str],
    value_col: str,
    group_by: str | None,
) -> tuple[dict[str, list[float]], dict[str, list[str]], list[str]]:
    """Walk rows, extract numeric value + grouping label + per-row docId.

    `parallel_doc_ids` is the ontologyTables-projection's docIds list,
    same index order as `rows`. When the lists desynchronize (rows
    longer than docIds — possible if the projection ever drops a doc
    without dropping its row), we silently skip the missing-docId case
    rather than spinning up bogus IDs.

    Returns (buckets_by_group_name, doc_ids_by_group_name, order_seen).
    The per-bucket docIds list is parallel to the per-bucket values
    list — `doc_ids_by_group_name[g][i]` is the document that
    contributed `buckets[g][i]`.
    """
    buckets: dict[str, list[float]] = {}
    bucket_doc_ids: dict[str, list[str]] = {}
    order_seen: list[str] = []
    for i, row in enumerate(rows):
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
            bucket_doc_ids[g] = []
            order_seen.append(g)
        buckets[g].append(v)
        # Track the contributing docId when the projection surfaced one
        # at this index. Missing docIds are tolerated (skip-only) so a
        # partial projection doesn't poison the citations.
        if i < len(parallel_doc_ids):
            doc_id = parallel_doc_ids[i]
            if isinstance(doc_id, str) and doc_id:
                bucket_doc_ids[g].append(doc_id)
    return buckets, bucket_doc_ids, order_seen


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
    bucket_doc_ids: dict[str, list[str]],
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
        # Per-group sample of contributing docIds. The chat consumes
        # these to build per-group sample-row references so the user
        # can drill into specific examples (e.g. "one Saline row" /
        # "one CNO row") while the primary citation still points to
        # the aggregated table view. Capped to avoid blowing the chip
        # count on charts with many groups — 3 examples per group is
        # plenty for verification.
        group_doc_ids = bucket_doc_ids.get(name) or []
        out.append({
            "name": name,
            "values": sampled,
            "docIds": group_doc_ids[:MAX_DOC_IDS_PER_GROUP],
            "totalRows": len(vals),
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
