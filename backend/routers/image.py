"""Image endpoint for the experimental /ask chat's ``fetch_image`` tool.

GET /api/datasets/{dataset_id}/documents/{document_id}/image
    ?frame=N     (multi-frame TIFF / animated GIF frame index; default 0)

The route fetches the document's primary image file, decodes it via
Pillow (supports TIFF/PNG/JPEG/GIF auto-detect), converts to a 2D
grayscale float array, and returns the array plus min/max for Plotly's
heatmap colorscale.

Targets the patch-encounter map / fluorescence image / cell-image use
cases for Haley accept-reject-foraging and Bhar memory datasets. PIs
asking "show me the encounter map" now get an inline heatmap instead of
"that's not currently supported".

Soft errors (decode failure, missing file, unsupported format) surface
as ``{"error", "errorKind"}`` — the chat tool inspects the envelope and
the LLM tells the user plainly rather than emitting a chart fence.

This is a NEW additive endpoint. Anonymous-readable. 60s timeout (large
TIFFs from the cloud can be slow to download).
"""
from __future__ import annotations

from typing import Annotated, Any

from fastapi import APIRouter, Depends, Query

from ..auth.dependencies import get_current_session
from ..auth.session import SessionData
from ..services.document_service import DocumentService
from ..services.image_service import ImageService
from ._deps import document_service, image_service, limit_reads
from ._validators import DatasetId, DocumentId

router = APIRouter(
    prefix="/api/datasets/{dataset_id}/documents/{document_id}",
    tags=["image"],
    dependencies=[Depends(limit_reads)],
)


@router.get("/image")
async def get_image(
    dataset_id: DatasetId,
    document_id: DocumentId,
    docs: Annotated[DocumentService, Depends(document_service)],
    svc: Annotated[ImageService, Depends(image_service)],
    session: Annotated[SessionData | None, Depends(get_current_session)],
    frame: Annotated[
        int,
        Query(
            ge=0,
            le=10_000,
            description=(
                "Frame index for multi-frame containers (TIFF stack, "
                "animated GIF). Defaults to 0 (first frame). Out-of-range "
                "values clamp to the last frame and log a warning."
            ),
        ),
    ] = 0,
) -> dict[str, Any]:
    """Return a 2D image array with provenance.

    Response shape (success)::

        {
          "width": int,
          "height": int,
          "data": [[float, ...], ...],
          "min": float,
          "max": float,
          "format": "tiff" | "png" | "jpeg" | "...",
          "downsampled": bool,
          "source": {
            "dataset_id": str,
            "document_id": str,
            "doc_class": str | None,
            "doc_name": str | None,
            "filename": str | None,
          }
        }

    Response shape (soft error)::

        {"error": "...", "errorKind": "notfound|decode|unsupported"}
    """
    document = await docs.detail(
        dataset_id,
        document_id,
        access_token=session.access_token if session else None,
    )
    return await svc.fetch_image(document, frame=frame, session=session)
