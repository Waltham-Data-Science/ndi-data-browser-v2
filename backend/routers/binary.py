"""Binary data endpoints: type detection + typed accessors."""
from __future__ import annotations

from typing import Annotated, Any

from fastapi import APIRouter, Depends, Response

from ..auth.dependencies import get_current_session
from ..auth.session import SessionData
from ..services.binary_service import BinaryService
from ..services.document_service import DocumentService
from ._deps import binary_service, document_service, limit_reads
from ._validators import DatasetId, DocumentId

router = APIRouter(
    prefix="/api/datasets/{dataset_id}/documents/{document_id}/data",
    tags=["binary"],
    dependencies=[Depends(limit_reads)],
)


async def _document(
    dataset_id: DatasetId,
    document_id: DocumentId,
    svc: DocumentService,
    session: SessionData | None,
) -> dict[str, Any]:
    return await svc.detail(
        dataset_id, document_id, access_token=session.access_token if session else None,
    )


@router.get("/type")
async def detect_type(
    dataset_id: DatasetId,
    document_id: DocumentId,
    docs: Annotated[DocumentService, Depends(document_service)],
    bs: Annotated[BinaryService, Depends(binary_service)],
    session: Annotated[SessionData | None, Depends(get_current_session)],
) -> dict[str, Any]:
    doc = await _document(dataset_id, document_id, docs, session)
    return {"kind": bs.detect_kind(doc)}


@router.get("/timeseries")
async def timeseries(
    dataset_id: DatasetId,
    document_id: DocumentId,
    docs: Annotated[DocumentService, Depends(document_service)],
    bs: Annotated[BinaryService, Depends(binary_service)],
    session: Annotated[SessionData | None, Depends(get_current_session)],
) -> dict[str, Any]:
    doc = await _document(dataset_id, document_id, docs, session)
    return await bs.get_timeseries(doc, access_token=session.access_token if session else None)


@router.get("/image")
async def image(
    dataset_id: DatasetId,
    document_id: DocumentId,
    docs: Annotated[DocumentService, Depends(document_service)],
    bs: Annotated[BinaryService, Depends(binary_service)],
    session: Annotated[SessionData | None, Depends(get_current_session)],
) -> dict[str, Any]:
    doc = await _document(dataset_id, document_id, docs, session)
    return await bs.get_image(doc, access_token=session.access_token if session else None)


@router.get("/video")
async def video(
    dataset_id: DatasetId,
    document_id: DocumentId,
    docs: Annotated[DocumentService, Depends(document_service)],
    bs: Annotated[BinaryService, Depends(binary_service)],
    session: Annotated[SessionData | None, Depends(get_current_session)],
) -> dict[str, Any]:
    doc = await _document(dataset_id, document_id, docs, session)
    return await bs.get_video_url(doc)


@router.get("/fitcurve")
async def fitcurve(
    dataset_id: DatasetId,
    document_id: DocumentId,
    docs: Annotated[DocumentService, Depends(document_service)],
    bs: Annotated[BinaryService, Depends(binary_service)],
    session: Annotated[SessionData | None, Depends(get_current_session)],
) -> dict[str, Any]:
    doc = await _document(dataset_id, document_id, docs, session)
    return bs.evaluate_fitcurve(doc)


@router.get("/raw")
async def raw(
    dataset_id: DatasetId,
    document_id: DocumentId,
    docs: Annotated[DocumentService, Depends(document_service)],
    bs: Annotated[BinaryService, Depends(binary_service)],
    session: Annotated[SessionData | None, Depends(get_current_session)],
) -> Response:
    """Stream the document's first file as raw ``application/octet-stream`` bytes.

    Companion to ``/data/image`` for raw-uint8 ``imageStack`` files. PIL's
    ``Image.open`` raises on headerless raw pixel buffers (no PNG/JPEG magic,
    just pixel bytes), so the frontend opts into this passthrough path and
    decodes the bytes itself using sidecar metadata from the matching
    ``imageStack_parameters`` document fetched via ``/documents/:id``.

    Range support: not implemented in this MVP. Full-byte fetch only. A
    future v2 follow-up can add ``Range: bytes=START-END`` forwarding to
    S3 for per-frame seeking on large stacks (500 MB+) — see PR body.

    SSRF protection: inherited from ``cloud.download_file`` (URL
    allowlist + scheme check). The endpoint streams bytes blindly — it
    does NOT validate the file is actually a raw imageStack. A non-raw
    blob hit on this path returns its bytes verbatim, so frontends should
    only call this for known imageStack docs.
    """
    doc = await _document(dataset_id, document_id, docs, session)
    payload = await bs.get_raw(doc, access_token=session.access_token if session else None)
    headers = {
        # Optional surface for debugging / log correlation. Frontend won't
        # depend on these — partner-doc metadata is fetched separately.
        "X-NDI-Doc-Id": document_id,
    }
    class_name = _safe_class_name(doc)
    if class_name:
        headers["X-NDI-Class-Name"] = class_name
    return Response(
        content=payload,
        media_type="application/octet-stream",
        headers=headers,
    )


def _safe_class_name(document: dict[str, Any]) -> str:
    """Pull a `className` off a document tolerantly. Avoids surfacing the
    full document shape on the wire — only the class string is exposed."""
    return (
        document.get("className")
        or document.get("class_name")
        or ((document.get("data") or {}).get("document_class") or {}).get("class_name")
        or ""
    )
