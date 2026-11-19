"""Chat attachments. Uploads go through the document intelligence pipeline (extraction, OCR, indexing), and
downloads through its access checks."""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, UploadFile

from ..config import get_settings
from ..documents import access, service, store
from ..documents.extract import UnsupportedFile
from ..security import current_user
from .documents import _file_response

router = APIRouter(prefix="/api/files", tags=["files"])


@router.post("")
async def upload(file: UploadFile, user: dict = Depends(current_user)):
    s = get_settings()
    limit = s.max_upload_mb * 1024 * 1024
    data = await file.read(limit + 1)
    if len(data) > limit:
        raise HTTPException(413, f"File is larger than {s.max_upload_mb} MB.")
    if not data:
        raise HTTPException(422, "File is empty.")
    try:
        return await service.ingest(user["id"], file.filename or "upload", data)
    except UnsupportedFile as e:
        raise HTTPException(415, str(e))


@router.get("")
async def list_files(user: dict = Depends(current_user)):
    return await store.list_docs(user["id"])


@router.get("/{file_id}/content")
async def download(file_id: str, user: dict = Depends(current_user)):
    try:
        await access.require(user["id"], file_id)
    except access.NotFound:
        raise HTTPException(404, "File not found")
    return _file_response(await store.get_doc(user["id"], file_id))


@router.delete("/{file_id}")
async def delete(file_id: str, user: dict = Depends(current_user)):
    try:
        await service.delete(user["id"], file_id)
    except access.NotFound:
        raise HTTPException(404, "File not found")
    except access.Forbidden as e:
        raise HTTPException(403, str(e))
    return {"ok": True}
