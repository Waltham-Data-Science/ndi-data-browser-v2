"""Distribution visualizations (violin / box) computed server-side.

Reuses summary tables as source-of-truth; given a dataset, class, and numeric field,
returns quartiles + kernel-density-estimate for a violin plot.
"""
from __future__ import annotations

from typing import Any

import numpy as np
from scipy import stats

from ..clients.ndi_cloud import NdiCloudClient
from .summary_table_service import SummaryTableService


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
        access_token: str | None,
    ) -> dict[str, Any]:
        table = await self.tables.single_class(dataset_id, class_name, access_token=access_token)
        values: list[float] = []
        for row in table["rows"]:
            v = row.get(field)
            try:
                values.append(float(v))
            except (TypeError, ValueError):
                continue
        if not values:
            return {"n": 0, "quartiles": None, "kde": None, "raw": []}
        arr = np.asarray(values)
        q = np.percentile(arr, [25, 50, 75])
        k = stats.gaussian_kde(arr) if len(arr) > 1 else None
        xs = np.linspace(float(arr.min()), float(arr.max()), 200) if len(arr) > 1 else arr
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
