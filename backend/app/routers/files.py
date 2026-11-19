from __future__ import annotations

import asyncio
import uuid
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, UploadFile
from fastapi.responses import FileResponse

from .. import db
from ..config import get_settings
from ..llm import registry
from ..security import current_user
from ..services.documents import UnsupportedFile, chunk_text, classify, extract_text, sniff_image_mime

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
    filename = Path(file.filename or "upload").name[:200]
    kind = classify(filename, file.content_type or "", data[:16])
    file_id = str(uuid.uuid4())
    user_dir = s.upload_dir / user["id"]
    user_dir.mkdir(parents=True, exist_ok=True)
    path = user_dir / file_id  # never use the client filename on disk

    text = None
    mime = file.content_type or "application/octet-stream"
    if kind == "image":
        mime = sniff_image_mime(data[:16])
    else:
        try:
            text = await asyncio.to_thread(extract_text, filename, data)
        except UnsupportedFile as e:
            raise HTTPException(415, str(e))
        except Exception:
            raise HTTPException(422, "Could not read this file. Is it corrupted or password-protected?")
        text = text.replace("\x00", "")

    await asyncio.to_thread(path.write_bytes, data)
    rec = await db.create_file(user["id"], id=file_id, filename=filename, mime_type=mime, size_bytes=len(data),
                               storage_path=str(path), kind=kind, text_content=text)
    if text:
        chunks = chunk_text(text)
        try:
            embs = await registry.embed(chunks)
        except Exception:
            embs = None  # keyword search still works
        await db.add_chunks(user["id"], file_id, chunks, embs)
        rec["chars"] = len(text)
        rec["chunks"] = len(chunks)
    return rec


@router.get("")
async def list_files(user: dict = Depends(current_user)):
    return await db.list_files(user["id"])


@router.get("/{file_id}/content")
async def download(file_id: str, user: dict = Depends(current_user)):
    files = await db.get_files(user["id"], [file_id])
    if not files:
        raise HTTPException(404, "File not found")
    f = files[0]
    return FileResponse(f["storage_path"], media_type=f["mime_type"], filename=f["filename"])


@router.delete("/{file_id}")
async def delete(file_id: str, user: dict = Depends(current_user)):
    rec = await db.delete_file(user["id"], file_id)
    if not rec:
        raise HTTPException(404, "File not found")
    Path(rec["storage_path"]).unlink(missing_ok=True)
    return {"ok": True}
