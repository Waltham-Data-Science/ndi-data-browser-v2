"""Document list + detail via cloud."""
from __future__ import annotations

from typing import Any

from ..clients.ndi_cloud import NdiCloudClient

# Keys that live OUTSIDE `data` on either detail or bulk-fetch responses.
_DOC_METADATA_KEYS = {"id", "_id", "ndiId", "name", "className", "datasetId"}


class DocumentService:
    def __init__(self, cloud: NdiCloudClient) -> None:
        self.cloud = cloud

    async def detail(
        self, dataset_id: str, document_id: str, *, access_token: str | None,
    ) -> dict[str, Any]:
        raw = await self.cloud.get_document(
            dataset_id, document_id, access_token=access_token,
        )
        return _normalize_document(raw)

    async def list_by_class(
        self,
        dataset_id: str,
        class_name: str | None,
        *,
        page: int,
        page_size: int,
        access_token: str | None,
    ) -> dict[str, Any]:
        """List documents in a dataset, optionally filtered by class.

        Paginates at the cloud layer. We ask ndiquery for just the page we
        need (plus a cheap second pass on page 1 to learn the total for
        pagination controls). Previously this fetched every matching ID,
        which on Haley's 9,032 openminds_subject path meant a 9k-ID round
        trip for a 50-doc page. Now we only transfer what we render.

        The cloud's ndiquery returns `number_matches` (or `totalItems`)
        in the body so we don't need a separate count call.
        """
        structure: list[dict[str, Any]] = []
        if class_name:
            structure.append({"operation": "isa", "param1": class_name})
        else:
            structure = [{"operation": "isa", "param1": "ndi_document"}]

        # Single-page ndiquery. fetch_all=False stops the auto-paginator.
        body = await self.cloud.ndiquery(
            searchstructure=structure,
            scope=dataset_id,
            access_token=access_token,
            page=page,
            page_size=page_size,
            fetch_all=False,
        )
        raw_docs = body.get("documents", [])
        slice_ids: list[str] = [
            d.get("id") or d.get("ndiId")
            for d in raw_docs
            if d.get("id") or d.get("ndiId")
        ]
        total = int(
            body.get("number_matches") or body.get("totalItems") or len(slice_ids),
        )
        docs = (
            await self.cloud.bulk_fetch(
                dataset_id, slice_ids, access_token=access_token,
            )
            if slice_ids
            else []
        )
        return {
            "total": total,
            "page": page,
            "pageSize": page_size,
            "documents": docs,
        }


def _normalize_document(raw: dict[str, Any]) -> dict[str, Any]:
    """Reconcile the cloud's two document shapes.

    `POST /bulk-fetch` wraps doc body under `data.*`:
        {id, ndiId, name, className, datasetId, data: {base, ...}}

    `GET /datasets/:id/documents/:docId` HOISTS body fields to top-level:
        {id, base, depends_on, document_class, element_epoch, files, ...}

    Downstream services (binary_service, dependency_graph_service,
    summary_table_service) all read via `doc.data.*`. This helper
    materializes the bulk-fetch shape from a single-doc response so the
    rest of the codebase stays on one shape.
    """
    if not isinstance(raw, dict):
        return raw
    if "data" in raw and isinstance(raw["data"], dict) and raw["data"]:
        # Already bulk-fetch shape.
        return raw
    # Extract metadata keys; everything else goes under `data`.
    metadata = {k: raw[k] for k in _DOC_METADATA_KEYS if k in raw}
    data = {k: v for k, v in raw.items() if k not in _DOC_METADATA_KEYS and k != "data"}
    # id may live under _id on the cloud response.
    if "id" not in metadata and "_id" in metadata:
        metadata["id"] = metadata["_id"]
    return {**metadata, "data": data}
