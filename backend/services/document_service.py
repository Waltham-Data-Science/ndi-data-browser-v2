"""Document list + detail via cloud."""
from __future__ import annotations

from typing import Any

from ..clients.ndi_cloud import NdiCloudClient


class DocumentService:
    def __init__(self, cloud: NdiCloudClient) -> None:
        self.cloud = cloud

    async def detail(
        self, dataset_id: str, document_id: str, *, access_token: str | None,
    ) -> dict[str, Any]:
        return await self.cloud.get_document(dataset_id, document_id, access_token=access_token)

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

        Uses indexed ndiquery. Returns first `page_size * page` docs and
        paginates client-side; the cloud doesn't currently paginate ndiquery
        results, so we fetch IDs then bulk-fetch the slice needed for this page.
        """
        structure: list[dict[str, Any]] = []
        if class_name:
            structure.append({"operation": "isa", "param1": class_name})
        else:
            # Everything — fetch via explicit isa of the root "ndi_document" in future;
            # for now, the cloud supports empty searchstructure as "match all by class".
            structure = [{"operation": "isa", "param1": "ndi_document"}]

        body = await self.cloud.ndiquery(
            searchstructure=structure,
            scope=dataset_id,
            access_token=access_token,
        )
        ids: list[str] = [
            d.get("id") or d.get("ndiId") for d in body.get("documents", []) if d.get("id") or d.get("ndiId")
        ]
        total = len(ids)
        offset = (page - 1) * page_size
        slice_ids = ids[offset : offset + page_size]
        docs = await self.cloud.bulk_fetch(dataset_id, slice_ids, access_token=access_token) if slice_ids else []
        return {
            "total": total,
            "page": page,
            "pageSize": page_size,
            "documents": docs,
        }
