"""Binary data endpoints: type detection + typed accessors."""
from __future__ import annotations

from typing import Annotated, Any

from fastapi import APIRouter, Depends, Header, Response

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
    range_header: Annotated[str | None, Header(alias="Range")] = None,
) -> Response:
    """Stream the document's first file with magic-byte Content-Type +
    HTTP Range support.

    Companion to ``/data/image`` for raw-uint8 ``imageStack`` files. PIL's
    ``Image.open`` raises on headerless raw pixel buffers (no PNG/JPEG magic),
    so the frontend opts into this passthrough path and decodes the bytes
    itself using sidecar metadata from the matching ``imageStack_parameters``
    document fetched via ``/documents/:id``.

    **Content-Type detection** — the endpoint sniffs the first ~12 bytes of
    the file against a small magic-byte table and returns the right MIME so
    HTML5 ``<video>`` / ``<img>`` work natively:

      - ``00 00 00 ?? 66 74 79 70`` (``ftyp``) → ``video/mp4``
      - ``89 50 4E 47 0D 0A 1A 0A``           → ``image/png``
      - ``FF D8 FF``                          → ``image/jpeg``
      - ``49 49 2A 00`` / ``4D 4D 00 2A``     → ``image/tiff``
      - default                               → ``application/octet-stream``

    **Range support** — when the request carries a ``Range: bytes=START-END``
    header, the endpoint forwards it verbatim to S3 (which supports Range on
    signed-URL GETs natively) and returns ``206 Partial Content`` with the
    matching ``Content-Range`` and ``Content-Length`` headers. This is what
    makes ``<video>`` seek work — the browser asks for the byte slice
    surrounding the seek target, plays from there.

    Non-Range responses still set ``Accept-Ranges: bytes`` so the browser
    knows it can issue Range requests later.

    SSRF protection: inherited from ``cloud.download_file_range`` (URL
    allowlist + scheme check). The endpoint streams bytes blindly — it does
    NOT validate the file is actually a raw imageStack. A non-raw blob hit
    on this path returns its bytes verbatim, so frontends should only call
    this for known imageStack / movie docs.
    """
    doc = await _document(dataset_id, document_id, docs, session)
    result = await bs.get_raw_response(
        doc,
        access_token=session.access_token if session else None,
        range_header=range_header,
    )
    headers: dict[str, str] = {
        # Always advertise Range support so the browser knows to issue Range
        # follow-ups for seek even if the initial GET wasn't ranged.
        "Accept-Ranges": "bytes",
        # Optional surface for debugging / log correlation. Frontend won't
        # depend on these — partner-doc metadata is fetched separately.
        "X-NDI-Doc-Id": document_id,
    }
    class_name = _safe_class_name(doc)
    if class_name:
        headers["X-NDI-Class-Name"] = class_name

    # Content-Length: set explicitly on both 200 and 206 so clients can show
    # accurate progress / preallocate buffers. For 206, this is the SLICE
    # length, not the total file size.
    headers["Content-Length"] = str(len(result.content))

    if result.status_code == 206 and result.content_range:
        headers["Content-Range"] = result.content_range

    return Response(
        content=result.content,
        media_type=result.content_type,
        status_code=result.status_code,
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
