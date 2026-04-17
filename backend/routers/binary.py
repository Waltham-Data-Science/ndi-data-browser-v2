"""Binary data endpoints: type detection + typed accessors."""
from __future__ import annotations

from typing import Annotated, Any

from fastapi import APIRouter, Depends

from ..auth.dependencies import get_current_session
from ..auth.session import SessionData
from ..services.binary_service import BinaryService
from ..services.document_service import DocumentService
from ._deps import binary_service, document_service, limit_reads

router = APIRouter(
    prefix="/api/datasets/{dataset_id}/documents/{document_id}/data",
    tags=["binary"],
    dependencies=[Depends(limit_reads)],
)


async def _document(
    dataset_id: str,
    document_id: str,
    svc: DocumentService,
    session: SessionData | None,
) -> dict[str, Any]:
    return await svc.detail(
        dataset_id, document_id, access_token=session.access_token if session else None,
    )


@router.get("/type")
async def detect_type(
    dataset_id: str,
    document_id: str,
    docs: Annotated[DocumentService, Depends(document_service)],
    bs: Annotated[BinaryService, Depends(binary_service)],
    session: Annotated[SessionData | None, Depends(get_current_session)],
) -> dict[str, Any]:
    doc = await _document(dataset_id, document_id, docs, session)
    return {"kind": bs.detect_kind(doc)}


@router.get("/timeseries")
async def timeseries(
    dataset_id: str,
    document_id: str,
    docs: Annotated[DocumentService, Depends(document_service)],
    bs: Annotated[BinaryService, Depends(binary_service)],
    session: Annotated[SessionData | None, Depends(get_current_session)],
) -> dict[str, Any]:
    doc = await _document(dataset_id, document_id, docs, session)
    return await bs.get_timeseries(doc, access_token=session.access_token if session else None)


@router.get("/image")
async def image(
    dataset_id: str,
    document_id: str,
    docs: Annotated[DocumentService, Depends(document_service)],
    bs: Annotated[BinaryService, Depends(binary_service)],
    session: Annotated[SessionData | None, Depends(get_current_session)],
) -> dict[str, Any]:
    doc = await _document(dataset_id, document_id, docs, session)
    return await bs.get_image(doc, access_token=session.access_token if session else None)


@router.get("/video")
async def video(
    dataset_id: str,
    document_id: str,
    docs: Annotated[DocumentService, Depends(document_service)],
    bs: Annotated[BinaryService, Depends(binary_service)],
    session: Annotated[SessionData | None, Depends(get_current_session)],
) -> dict[str, Any]:
    doc = await _document(dataset_id, document_id, docs, session)
    return await bs.get_video_url(doc)


@router.get("/fitcurve")
async def fitcurve(
    dataset_id: str,
    document_id: str,
    docs: Annotated[DocumentService, Depends(document_service)],
    bs: Annotated[BinaryService, Depends(binary_service)],
    session: Annotated[SessionData | None, Depends(get_current_session)],
) -> dict[str, Any]:
    doc = await _document(dataset_id, document_id, docs, session)
    return bs.evaluate_fitcurve(doc)
