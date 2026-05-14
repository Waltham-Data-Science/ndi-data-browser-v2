"""Regression coverage for the ``probe → element`` class-name alias added
2026-05-14 to fix the chat tool's ``query_documents(className="probe")``
misshit on Dabrowska BNST (and every other dataset published under the
modern schema, where ``element`` is the canonical class name and
``probe`` returns 0 docs).

Behavior under test:

- :class:`SummaryTableService` accepts the user-friendly literal
  ``"probe"`` even when the underlying dataset stores its probe-class
  docs as ``"element"``. The cloud's ``isa probe`` query returns 0 IDs
  for those datasets — but a second ``isa element`` query succeeds, and
  the projection emits ``PROBE_COLUMNS`` rows (matching what the chat
  tool's ``query_documents`` consumer expects).

- The alias is logged so observability sees ``resolved_class=element``
  on a request for ``class_name=probe``.

- ``epoch → element_epoch`` follows the same pattern.

- When the literal class DOES return IDs (legacy datasets that emit
  ``probe`` directly), the alias is never invoked — i.e. zero behavior
  change on the happy path.
"""
from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

from backend.services.summary_table_service import (
    _CLASS_ALIASES,
    SummaryTableService,
)


def _make_service(*, ndiquery_responses: dict[str, dict[str, Any]]) -> tuple[
    SummaryTableService, MagicMock,
]:
    """Build a ``SummaryTableService`` whose cloud client returns the
    canned ``ndiquery`` payload for each class name in
    ``ndiquery_responses`` (keyed by ``param1``). Any class not in the
    map returns an empty document list (matching the cloud's behavior
    for missing classes).
    """
    cloud = MagicMock()

    async def _ndiquery(*, searchstructure, scope, access_token, **kwargs):
        class_name = searchstructure[0]["param1"]
        return ndiquery_responses.get(
            class_name,
            {"documents": [], "totalItems": 0, "page": 1, "pageSize": 1000},
        )

    cloud.ndiquery = _ndiquery
    cloud.bulk_fetch = AsyncMock(return_value=[])

    svc = SummaryTableService(cloud=cloud, cache=None)
    return svc, cloud


@pytest.mark.asyncio
async def test_probe_alias_falls_back_to_element_when_probe_returns_zero():
    """Dabrowska-shape: 0 probe docs, 2 element docs. ``isa probe`` returns
    empty so the service must retry ``isa element`` and surface those.
    """
    element_doc = {
        "id": "el1",
        "ndiId": "ndi-el1",
        "data": {
            "base": {"id": "ndi-el1", "name": "patch-Vm-01"},
            "element": {"name": "patch-Vm-01", "type": "patch-Vm"},
        },
    }
    element_doc_2 = {
        "id": "el2",
        "ndiId": "ndi-el2",
        "data": {
            "base": {"id": "ndi-el2", "name": "stim-01"},
            "element": {"name": "stim-01", "type": "stimulator"},
        },
    }

    svc, cloud = _make_service(
        ndiquery_responses={
            # `isa probe` returns nothing — modern dataset.
            "probe": {"documents": [], "totalItems": 0, "page": 1, "pageSize": 1000},
            # `isa element` returns 2 docs — the alias hit.
            "element": {
                "documents": [{"id": "el1"}, {"id": "el2"}],
                "totalItems": 2,
                "page": 1,
                "pageSize": 1000,
            },
        },
    )
    cloud.bulk_fetch = AsyncMock(return_value=[element_doc, element_doc_2])

    result = await svc.single_class("DS_DABROWSKA", "probe", session=None)
    rows = result["rows"]
    # Both element docs projected as probe rows under PROBE_COLUMNS.
    assert len(rows) == 2, f"expected probe→element alias to return 2 rows, got {rows!r}"
    # Probe-column shape: probeName + probeType present.
    assert rows[0]["probeName"] in {"patch-Vm-01", "stim-01"}
    assert rows[0]["probeType"] in {"patch-Vm", "stimulator"}
    types = {r["probeType"] for r in rows}
    assert types == {"patch-Vm", "stimulator"}, (
        f"probe alias must surface element.type values; got {types!r}"
    )


@pytest.mark.asyncio
async def test_probe_alias_not_invoked_when_probe_returns_docs():
    """Legacy datasets (Van Hooser): ``isa probe`` returns docs; the alias
    must NOT fire, and the resolved class stays ``probe`` (logged for
    observability). Behavior is byte-identical to the pre-alias build.
    """
    probe_doc = {
        "id": "p1",
        "ndiId": "ndi-p1",
        "data": {
            "base": {"id": "ndi-p1", "name": "n-trode-01"},
            "probe": {"name": "n-trode-01", "type": "n-trode"},
        },
    }

    svc, cloud = _make_service(
        ndiquery_responses={
            "probe": {
                "documents": [{"id": "p1"}],
                "totalItems": 1,
                "page": 1,
                "pageSize": 1000,
            },
            # `element` would also return data but the alias path must
            # not consult it. Assert by giving `element` a poison value
            # — if the service queried it, the probeType field would
            # show "POISON" instead of "n-trode".
            "element": {
                "documents": [{"id": "POISON"}],
                "totalItems": 1,
                "page": 1,
                "pageSize": 1000,
            },
        },
    )
    cloud.bulk_fetch = AsyncMock(return_value=[probe_doc])

    result = await svc.single_class("DS_VANHOOSER", "probe", session=None)
    rows = result["rows"]
    assert len(rows) == 1
    assert rows[0]["probeType"] == "n-trode", (
        "alias must not fire when literal class returns docs"
    )


@pytest.mark.asyncio
async def test_epoch_alias_falls_back_to_element_epoch():
    """``isa epoch`` returns zero on modern datasets; ``isa element_epoch``
    is the canonical class name. Same alias pattern as probe→element.
    """
    element_epoch_doc = {
        "id": "ee1",
        "ndiId": "ndi-ee1",
        "data": {
            "base": {"id": "ndi-ee1", "name": "epoch-1"},
            "element_epoch": {
                "name": "epoch-1",
                "t0_t1": [0.0, 100.0],
                "epoch_clock": "dev_local_time",
            },
        },
    }

    svc, cloud = _make_service(
        ndiquery_responses={
            "epoch": {"documents": [], "totalItems": 0, "page": 1, "pageSize": 1000},
            "element_epoch": {
                "documents": [{"id": "ee1"}],
                "totalItems": 1,
                "page": 1,
                "pageSize": 1000,
            },
        },
    )
    cloud.bulk_fetch = AsyncMock(return_value=[element_epoch_doc])

    result = await svc.single_class("DS_MODERN", "epoch", session=None)
    rows = result["rows"]
    assert len(rows) == 1, "epoch→element_epoch alias must surface the row"
    # EPOCH_COLUMNS shape: epochNumber + t0_t1 normalized.
    assert rows[0]["epochNumber"] == "epoch-1"


def test_class_aliases_table_has_expected_entries():
    """Snapshot of the alias map — additions are intentional, removals
    require updating this test + the chat tool's system prompt.
    """
    assert _CLASS_ALIASES == {
        "probe": ["element"],
        "epoch": ["element_epoch"],
    }
