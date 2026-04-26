"""Document list + detail via cloud."""
from __future__ import annotations

import re
from typing import Any

from ..clients.ndi_cloud import NdiCloudClient
from ..errors import NotFound
from ..observability.logging import get_logger

log = get_logger(__name__)

# Keys that live OUTSIDE `data` on either detail or bulk-fetch responses.
_DOC_METADATA_KEYS = {"id", "_id", "ndiId", "name", "className", "datasetId"}

# MongoDB ObjectId — 24 hex chars. NDI ndiIds use an underscore-separated
# hex format (e.g., `41269345148e40b8_40ddef57dbdd8c93`) which fails this
# check, triggering ndiquery fallback in `detail()`.
_MONGO_OBJECT_ID = re.compile(r"^[0-9a-fA-F]{24}$")


class DocumentService:
    def __init__(self, cloud: NdiCloudClient) -> None:
        self.cloud = cloud

    async def detail(
        self, dataset_id: str, document_id: str, *, access_token: str | None,
    ) -> dict[str, Any]:
        """Fetch a single document's full body.

        Accepts EITHER a 24-char Mongo `_id` OR an NDI ndiId (the
        `base.id` field used by `depends_on` references and summary
        table row identifiers). ndi-cloud-node's
        `GET /datasets/:id/documents/:docId` only accepts Mongo `_id`
        (Mongoose `findById` throws CastError on ndiIds), so when the
        caller passes an ndiId we do an `ndiquery exact_string
        base.id=<ndiId>` first to resolve it, then fetch by `_id`.

        This is the same resolution DependencyGraphService uses; it
        keeps the frontend's table-row → document-detail links working
        regardless of whether the row exposed a Mongo id or an ndiId.
        """
        resolved_id = document_id
        if not _MONGO_OBJECT_ID.match(document_id):
            resolved_id = await self._resolve_ndi_id(
                dataset_id, document_id, access_token=access_token,
            )
        raw = await self.cloud.get_document(
            dataset_id, resolved_id, access_token=access_token,
        )
        return _normalize_document(raw)

    async def _resolve_ndi_id(
        self,
        dataset_id: str,
        ndi_id: str,
        *,
        access_token: str | None,
    ) -> str:
        """Resolve an ndiId (`base.id`) to its Mongo `_id` via ndiquery.

        Raises :class:`NotFound` if no document in `dataset_id` matches
        the ndiId, so the route returns a clean 404 instead of a
        `CLOUD_INTERNAL_ERROR` from Mongoose's CastError.
        """
        try:
            body = await self.cloud.ndiquery(
                searchstructure=[
                    {"operation": "exact_string", "field": "base.id", "param1": ndi_id},
                ],
                scope=dataset_id,
                access_token=access_token,
                page_size=5,
                fetch_all=False,
            )
        except Exception as e:
            # Re-raise so the caller's circuit breaker / error handling
            # sees the real upstream failure. Only map the empty-match
            # case to NotFound below.
            log.warning("document_service.ndi_id_resolve_failed", ndi=ndi_id, error=str(e))
            raise
        docs = body.get("documents") or []
        if not docs:
            raise NotFound(f"No document with ndiId {ndi_id} in this dataset.")
        # Defensive: don't trust the first hit blindly. The cloud's
        # `exact_string` on `base.id` should return at most one match
        # per dataset, but a cloud-side indexer bug or a substring
        # aliasing issue could return a wrong-but-similar doc. Prefer
        # the match whose top-level `ndiId` or nested `data.base.id`
        # equals our input; fall back to the first hit only if no
        # doc's metadata makes the ndiId explicit.
        match = next(
            (
                d for d in docs
                if d.get("ndiId") == ndi_id
                or (d.get("data") or {}).get("base", {}).get("id") == ndi_id
            ),
            docs[0],
        )
        resolved = match.get("id") or match.get("_id")
        if not resolved:
            raise NotFound(f"Document {ndi_id} found but has no Mongo _id.")
        return str(resolved)

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

        ## Anonymous fallback (2026-04-26)

        Production smoke surfaced: cloud's ndiquery returns 0 for
        anonymous callers on published datasets that DO have
        documents (e.g. dataset 682e7772... has 78,687 docs but
        anonymous ndiquery returns `number_matches: 0`). The
        per-dataset detail (`GET /datasets/:id`) carries the full
        document-id array inline and works anonymously, so we use
        that as a fallback whenever ndiquery comes back empty.

        Class-filtered requests stay on ndiquery (the inline
        document-id array doesn't carry className, so we'd need a
        bulk-fetch + post-filter pass which is expensive at scale).
        For unfiltered "All documents" — the most-trafficked view —
        the fallback gives anonymous users the correct results.
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

        # Anonymous-fallback: ndiquery returned nothing AND the user
        # didn't ask for a class filter. Pull the inline document-id
        # array from the dataset detail (which works anonymously for
        # published datasets) and slice it into a page.
        if not slice_ids and not class_name:
            slice_ids, total = await self._inline_id_fallback(
                dataset_id=dataset_id,
                page=page,
                page_size=page_size,
                access_token=access_token,
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

    async def _inline_id_fallback(
        self,
        *,
        dataset_id: str,
        page: int,
        page_size: int,
        access_token: str | None,
    ) -> tuple[list[str], int]:
        """Slice the dataset's inline document-id array.

        `GET /datasets/:id` returns `documents: [<id>, <id>, ...]` —
        an array of bare Mongo `_id` strings (verified 2026-04-26 on
        cloud production). We use this as the source-of-truth for
        anonymous unfiltered listing where ndiquery silently returns
        empty.

        Returns `(slice_ids, total)`. `total` is the inline array's
        full length so pagination controls render correctly.
        """
        try:
            dataset = await self.cloud.get_dataset(
                dataset_id, access_token=access_token,
            )
        except Exception as exc:  # pragma: no cover — network/cloud errors
            log.warning(
                "documents.inline_fallback.failed",
                dataset_id=dataset_id,
                error=str(exc),
            )
            return ([], 0)

        ids_raw = dataset.get("documents") or []
        # Defensive: only keep string entries (the cloud occasionally ships
        # full doc objects in older responses; here we want bare ids).
        ids: list[str] = [x for x in ids_raw if isinstance(x, str)]
        total = len(ids)
        start = max(0, (page - 1) * page_size)
        end = start + page_size
        return (ids[start:end], total)


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
