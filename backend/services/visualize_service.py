"""Distribution visualizations (violin / box / grouped) computed server-side.

Reuses summary tables as source-of-truth; given a dataset, class, a
numeric field, and an optional categorical `group_by` field, returns
per-group quartiles + kernel-density-estimate values ready for the
frontend ViolinPlot.
"""
from __future__ import annotations

from typing import Any

from ..auth.session import SessionData
from ..clients.ndi_cloud import NdiCloudClient
from .summary_table_service import SummaryTableService

# numpy + scipy are imported lazily inside the methods that use them
# (audit 2026-04-23, #57). Eagerly importing them at module load
# contributed ~500ms per-worker cold-start; the visualize endpoint is
# called seldomly and binary_service has the same shape. See
# _numpy_stats() below — every function that needs the math imports
# from the shared helper so we don't repeat the try/except dance.


class VisualizeService:
    def __init__(self, cloud: NdiCloudClient) -> None:
        self.cloud = cloud
        self.tables = SummaryTableService(cloud)

    async def distribution(
        self,
        dataset_id: str,
        class_name: str,
        field: str,
        *,
        group_by: str | None = None,
        session: SessionData | None,
    ) -> dict[str, Any]:
        """Return a distribution payload for a numeric `field` in the
        given class, optionally grouped by a categorical `group_by` key.

        With group_by:
            {
              field, group_by, n,
              groups: [
                {name, count, min, max, mean, std, median, q1, q3, values},
                ...
              ]
            }

        Without group_by (back-compat with pre-M6 callers):
            {n, min, max, mean, std, quartiles, kde, raw}
        """
        table = await self.tables.single_class(
            dataset_id, class_name, session=session,
        )
        rows = table.get("rows") or []

        if group_by:
            grouped: dict[str, list[float]] = {}
            for row in rows:
                raw_key = row.get(group_by)
                key = _coerce_group_key(raw_key)
                if key is None:
                    continue
                value = _coerce_float(row.get(field))
                if value is None:
                    continue
                grouped.setdefault(key, []).append(value)
            group_payloads: list[dict[str, Any]] = []
            for name, vals in grouped.items():
                if not vals:
                    continue
                group_payloads.append(_summarize_group(name, vals))
            group_payloads.sort(key=lambda g: -g["count"])
            return {
                "field": field,
                "groupBy": group_by,
                "n": sum(g["count"] for g in group_payloads),
                "groups": group_payloads,
            }

        values: list[float] = []
        for row in rows:
            v = _coerce_float(row.get(field))
            if v is not None:
                values.append(v)
        if not values:
            return {"n": 0, "quartiles": None, "kde": None, "raw": []}
        # Lazy-import — see module docstring note on audit #57.
        import numpy as np
        from scipy import stats
        arr = np.asarray(values)
        q = np.percentile(arr, [25, 50, 75])
        k = stats.gaussian_kde(arr) if len(arr) > 1 else None
        xs = (
            np.linspace(float(arr.min()), float(arr.max()), 200)
            if len(arr) > 1
            else arr
        )
        density = k(xs).tolist() if k is not None else [1.0] * len(xs)
        return {
            "n": len(arr),
            "min": float(arr.min()),
            "max": float(arr.max()),
            "mean": float(arr.mean()),
            "std": float(arr.std()),
            "quartiles": {"q1": float(q[0]), "median": float(q[1]), "q3": float(q[2])},
            "kde": {"x": xs.tolist(), "density": density},
            "raw": arr.tolist(),
        }


def _coerce_float(v: Any) -> float | None:
    if v is None:
        return None
    if isinstance(v, (int, float)) and not isinstance(v, bool):
        return float(v)
    if isinstance(v, str):
        try:
            return float(v)
        except ValueError:
            return None
    # Accept the {devTime, globalTime} epoch shape — use devTime.
    if isinstance(v, dict) and "devTime" in v:
        return _coerce_float(v["devTime"])
    return None


def _coerce_group_key(v: Any) -> str | None:
    if v is None:
        return None
    if isinstance(v, str):
        trimmed = v.strip()
        return trimmed or None
    if isinstance(v, (int, float, bool)):
        return str(v)
    return None


def _summarize_group(name: str, vals: list[float]) -> dict[str, Any]:
    # Lazy-import numpy — see module docstring note on audit #57.
    import numpy as np
    arr = np.asarray(vals, dtype=float)
    q = np.percentile(arr, [25, 50, 75])
    return {
        "name": name,
        "count": len(arr),
        "min": float(arr.min()),
        "max": float(arr.max()),
        "mean": float(arr.mean()),
        "std": float(arr.std()),
        "median": float(q[1]),
        "q1": float(q[0]),
        "q3": float(q[2]),
        # Cap jitter-points to 500 per group to keep payload small.
        "values": arr[: min(500, len(arr))].tolist(),
    }
