"""treatment_timeline_service — project a dataset's ``treatment`` documents
into a Gantt-style horizontal timeline payload (one row per subject,
one bar per treatment period).

This service ports the orchestration logic that used to live in the
Next.js chat tool layer (``apps/web/lib/ndi/tools/treatment-timeline.ts``)
to Python so the heart of NDI processing lives next to ndi-python where
it belongs. The TS handler now becomes a thin proxy.

Endpoint strategy
─────────────────
1. PRIMARY: call :meth:`SummaryTableService.single_class` for the
   ``treatment`` class. Each projected row carries
   ``treatmentName``, ``treatmentOntology``, ``numericValue``,
   ``stringValue`` and ``subjectDocumentIdentifier``.
2. FALLBACK: if the primary returns zero rows, call
   :meth:`TabularQueryService.violin_groups` with
   ``variableNameContains="Treatment"``. That hits any
   ``ontologyTableRow`` whose schema surfaces a ``Treatment_*``
   column. We synthesize one bar per group with
   ``subject = "group:<name>"`` so the chart at least shows the
   treatment groups, even if per-subject granularity is lost.

Temporal extraction
───────────────────
Per-row best-effort, in order:

- ``startDate``/``endDate`` (or ``startTime``/``endTime``) — explicit
  field pair when present.
- ``numericValue`` as ``[start, end]`` (length-2 array), as scalar
  point ``[start, start+1]`` (length-1 array OR raw number).
- ``stringValue`` parseable as ISO date → ``[date, date+1 day]``.
- ELSE: synthesize an ordinal slot per subject: each treatment in
  order gets ``[i, i+1]``.

The ``temporal_source`` discriminator surfaces how timing was
derived so the caller can mention the caveat in prose:

- ``"explicit"`` — every plotted row carried real timing.
- ``"ordinal"`` — every plotted row was synthesized.
- ``"mixed"`` — some explicit, some synthesized.

Output shape
────────────
Returns RAW data (``items``, ``total_subjects``, ``total_treatments``,
``temporal_source``, optional ``empty_hint``). The chat tool's
``chart_payload`` framing is chat-specific and is reassembled by the
TS proxy — keeping the backend response chart-agnostic so the
workspace can consume the same payload directly.

``empty_hint`` is set ONLY when BOTH the primary table and the
fallback tabular_query returned zero rows, OR when rows came back
but none had a usable subject + treatment pair to plot.
"""
from __future__ import annotations

import math
from datetime import UTC, datetime, timedelta
from typing import Any, Literal

from ..auth.session import SessionData
from ..observability.logging import get_logger
from .summary_table_service import SummaryTableService
from .tabular_query_service import TabularQueryService

log = get_logger(__name__)


# Default + hard-cap for subjects in a single chart. Beyond ~100 the
# chart becomes a wall of bars; Plotly's row sizing also chokes the
# chat panel at that count. Matches the TS handler's bounds.
DEFAULT_MAX_SUBJECTS = 30
HARD_CAP_MAX_SUBJECTS = 100


# Type alias for the temporal-source discriminator.
TemporalSource = Literal["explicit", "ordinal", "mixed"]


class TreatmentTimelineService:
    """Build the treatment-timeline payload for a dataset."""

    def __init__(
        self,
        summary: SummaryTableService,
        tabular: TabularQueryService,
    ) -> None:
        self.summary = summary
        self.tabular = tabular

    async def compute_timeline(
        self,
        dataset_id: str,
        *,
        title: str | None,
        max_subjects: int,
        session: SessionData | None,
    ) -> dict[str, Any]:
        """Compute the timeline. Caller is responsible for clamping
        ``max_subjects`` to the [1, 100] window — the pydantic model
        on the router does that.
        """
        # --- Primary: /tables/treatment via SummaryTableService ---
        rows, available_columns = await self._fetch_primary_rows(
            dataset_id, session=session,
        )

        # --- Fallback: tabular_query for Treatment_* columns ---
        if not rows:
            fallback_rows, fallback_columns = await self._fetch_fallback_rows(
                dataset_id, session=session,
            )
            if fallback_rows:
                rows = fallback_rows
                if fallback_columns:
                    available_columns = fallback_columns

        items, total_subjects, temporal_source = _project_rows_to_items(
            rows, max_subjects=max_subjects,
        )

        empty_hint = _maybe_build_empty_hint(rows, items, available_columns)

        result: dict[str, Any] = {
            "datasetId": dataset_id,
            "items": items,
            "total_subjects": total_subjects,
            "total_treatments": len(items),
            "temporal_source": temporal_source,
        }
        if title:
            result["title"] = title
        if empty_hint is not None:
            result["empty_hint"] = empty_hint
        return result

    async def _fetch_primary_rows(
        self,
        dataset_id: str,
        *,
        session: SessionData | None,
    ) -> tuple[list[dict[str, Any]], list[str]]:
        """Pull treatment rows from the canonical
        ``/tables/treatment`` projection. Returns
        ``(rows, available_column_keys)``. The column-keys list is
        surfaced into ``empty_hint`` when we end up with no plottable
        items so the caller can tell users what fields the table
        DID carry.
        """
        try:
            table = await self.summary.single_class(
                dataset_id, "treatment", session=session,
            )
        except Exception as exc:
            # Service-internal failures (cloud unreachable, required
            # enrichment failed, etc.) should not abort the whole
            # endpoint — the fallback may still succeed, and even if
            # not, we want to return an empty_hint rather than a 500.
            log.warning(
                "treatment_timeline.primary_failed",
                dataset_id=dataset_id,
                error_type=type(exc).__name__,
                error=str(exc),
            )
            return [], []
        rows = table.get("rows") or []
        columns = [
            c.get("key") for c in (table.get("columns") or [])
            if isinstance(c.get("key"), str) and c.get("key")
        ]
        return list(rows), columns

    async def _fetch_fallback_rows(
        self,
        dataset_id: str,
        *,
        session: SessionData | None,
    ) -> tuple[list[dict[str, Any]], list[str]]:
        """Fallback when ``/tables/treatment`` is empty. Hits
        ``tabular_query`` against the ``Treatment`` substring; if
        that resolves to an ontologyTableRow ``Treatment_*`` column,
        the response carries one group per distinct value.

        We synthesize one row per group with
        ``subject = "group:<name>"`` and ``treatmentName = <name>``,
        no explicit timing. This loses per-subject granularity but at
        least surfaces the treatment categories visually.
        """
        try:
            result = await self.tabular.violin_groups(
                dataset_id,
                "Treatment",
                group_by=None,
                group_order=None,
                session=session,
            )
        except Exception as exc:
            log.warning(
                "treatment_timeline.fallback_failed",
                dataset_id=dataset_id,
                error_type=type(exc).__name__,
                error=str(exc),
            )
            return [], []
        groups = result.get("groups") or []
        if not groups:
            return [], []
        rows: list[dict[str, Any]] = [
            {
                "treatmentName": g.get("name"),
                "subjectDocumentIdentifier": f"group:{g.get('name')}",
            }
            for g in groups
            if isinstance(g, dict) and g.get("name")
        ]
        # tabular_query doesn't expose a column key list, but the
        # yLabel is the matched column's human label — useful diagnostic.
        columns: list[str] = []
        y_label = result.get("yLabel")
        if isinstance(y_label, str) and y_label:
            columns = [y_label]
        return rows, columns


# ---------------------------------------------------------------------------
# Projection — pure helpers, no IO
# ---------------------------------------------------------------------------


def _project_rows_to_items(
    rows: list[dict[str, Any]],
    *,
    max_subjects: int,
) -> tuple[list[dict[str, Any]], int, TemporalSource]:
    """Walk treatment rows and project to ``[{subject, treatment, start, end}, ...]``.

    Each subject gets its own ordinal counter so synthesized timing
    starts at ``[0, 1]`` for the first treatment per subject. The
    ``max_subjects`` cap applies to DISTINCT subjects (not bars) and
    is enforced first-seen — once we've seen N subjects, any
    subsequent row whose subject isn't already in the chart is
    dropped silently.
    """
    items: list[dict[str, Any]] = []
    seen_subjects: list[str] = []
    seen_subject_set: set[str] = set()
    subject_ordinal_counter: dict[str, int] = {}
    explicit_count = 0
    ordinal_count = 0

    for row in rows:
        subject = _pick_subject_label(row)
        if not subject:
            continue
        treatment = _pick_treatment_label(row)
        if not treatment:
            continue

        if subject not in seen_subject_set:
            if len(seen_subjects) >= max_subjects:
                # Cap enforced — silently drop subjects beyond N.
                continue
            seen_subject_set.add(subject)
            seen_subjects.append(subject)

        explicit = _extract_explicit_timing(row)
        if explicit is not None:
            start, end = explicit
            explicit_count += 1
        else:
            i = subject_ordinal_counter.get(subject, 0)
            start = i
            end = i + 1
            subject_ordinal_counter[subject] = i + 1
            ordinal_count += 1

        items.append(
            {
                "subject": subject,
                "treatment": treatment,
                "start": start,
                "end": end,
            },
        )

    temporal_source = _classify_temporal_source(explicit_count, ordinal_count)
    return items, len(seen_subjects), temporal_source


def _classify_temporal_source(
    explicit_count: int, ordinal_count: int,
) -> TemporalSource:
    """Discriminate the timing source. When both counts are zero
    (no items at all) we default to ``"ordinal"`` to match the TS
    handler's defaulting — the value is unused at the call site
    since the chart is empty, but it must be a valid literal."""
    if explicit_count > 0 and ordinal_count == 0:
        return "explicit"
    if explicit_count == 0 and ordinal_count > 0:
        return "ordinal"
    if explicit_count > 0 and ordinal_count > 0:
        return "mixed"
    return "ordinal"


def _pick_subject_label(row: dict[str, Any]) -> str | None:
    """Prefer ``subjectDocumentIdentifier`` (canonical); fall back to
    a bare ``subject`` field for forward-compat with future backends.
    """
    s = row.get("subjectDocumentIdentifier")
    if isinstance(s, str) and s:
        return s
    alt = row.get("subject")
    if isinstance(alt, str) and alt:
        return alt
    return None


def _pick_treatment_label(row: dict[str, Any]) -> str | None:
    """Prefer ``treatmentName``; fall back to ``stringValue`` when the
    value column carries a categorical label and the name is missing.
    """
    t = row.get("treatmentName")
    if isinstance(t, str) and t:
        return t
    sv = row.get("stringValue")
    if isinstance(sv, str) and sv:
        return sv
    return None


def _extract_explicit_timing(
    row: dict[str, Any],
) -> tuple[float | str, float | str] | None:
    """Best-effort extract ``(start, end)`` from a treatment row, or
    None when no usable timing is present.

    Lookup order matches the TS handler:
      1. ``startDate`` + ``endDate`` (or ``startTime`` + ``endTime``)
         when both are non-empty strings/numbers.
      2. ``numericValue`` as ``[start, end]`` (length-2) or
         ``[start]`` (length-1 → ``[start, start+1]``) or raw scalar
         (treated the same as length-1).
      3. ``stringValue`` parseable as ISO date → ``[date, date + 1 day]``.
    """
    # Explicit field pair.
    start_field = row.get("startDate")
    if start_field is None:
        start_field = row.get("startTime")
    end_field = row.get("endDate")
    if end_field is None:
        end_field = row.get("endTime")
    if _is_usable_temporal_field(start_field) and _is_usable_temporal_field(end_field):
        # mypy: we already narrowed to str|number in the helper. The
        # return type Union retains the original literal value so
        # date strings flow through verbatim.
        return start_field, end_field  # type: ignore[return-value]

    # numericValue array OR scalar.
    nv = row.get("numericValue")
    if isinstance(nv, list):
        if len(nv) >= 2 and _is_finite_number(nv[0]) and _is_finite_number(nv[1]):
            return float(nv[0]), float(nv[1])
        if len(nv) == 1 and _is_finite_number(nv[0]):
            return float(nv[0]), float(nv[0]) + 1.0
    elif _is_finite_number(nv):
        return float(nv), float(nv) + 1.0

    # stringValue as parseable ISO date — synthesize a 1-day window.
    sv = row.get("stringValue")
    if isinstance(sv, str) and sv:
        parsed = _parse_iso_datetime(sv)
        if parsed is not None:
            end_dt = parsed + timedelta(days=1)
            # Match the TS handler's contract: original string back as
            # start so Plotly's date axis renders verbatim; end is the
            # +1 day ISO string.
            return sv, end_dt.isoformat()

    return None


def _is_usable_temporal_field(v: Any) -> bool:
    """A temporal field is usable when it's a non-empty string or a
    finite number. None / empty string / NaN / inf are rejected."""
    if isinstance(v, str):
        return bool(v)
    return _is_finite_number(v)


def _is_finite_number(v: Any) -> bool:
    """True iff ``v`` is a finite int/float (bool excluded — bool is
    a subclass of int in Python and we don't want True/False slipping
    through as 1/0)."""
    if isinstance(v, bool):
        return False
    if isinstance(v, (int, float)):
        return math.isfinite(float(v))
    return False


def _parse_iso_datetime(s: str) -> datetime | None:
    """Best-effort ISO-8601 parse. Accepts ``Z`` suffix (UTC), bare
    date strings (``YYYY-MM-DD``), and full datetimes. Returns None
    on failure — the caller falls back to ordinal timing.
    """
    # ``datetime.fromisoformat`` handles RFC-3339-style strings; we
    # normalize a trailing ``Z`` to ``+00:00`` because pre-3.11
    # interpreters reject it.
    normalized = s.replace("Z", "+00:00") if s.endswith("Z") else s
    try:
        dt = datetime.fromisoformat(normalized)
    except (ValueError, TypeError):
        return None
    # Make tz-aware so isoformat round-trips deterministically. Naive
    # → assume UTC (matching JS ``Date.parse`` of a bare date string).
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=UTC)
    return dt


def _maybe_build_empty_hint(
    rows: list[dict[str, Any]],
    items: list[dict[str, Any]],
    available_columns: list[str],
) -> dict[str, Any] | None:
    """Diagnostic envelope when the chart would render empty.

    Distinguishes the two empty modes (matches TS handler):

    - ``rows == []`` → "no temporal info in treatment docs (neither
      /tables/treatment nor tabular_query returned rows)".
    - ``rows`` non-empty but ``items == []`` → rows came back but
      none had a usable subject + treatment pair to plot.
    """
    if items:
        return None
    if not rows:
        reason = (
            "no temporal info in treatment docs "
            "(neither /tables/treatment nor tabular_query returned rows)"
        )
    else:
        reason = (
            "treatment rows returned but none had a usable subject + "
            "treatment pair to plot"
        )
    hint: dict[str, Any] = {"reason": reason}
    if available_columns:
        hint["available_columns"] = available_columns
    return hint
