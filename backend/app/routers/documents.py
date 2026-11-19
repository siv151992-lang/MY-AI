"""Document intelligence API. Every route resolves access through documents.access: a document you don't own and
that isn't shared with you returns 404, and only owners can rename, reindex, share or delete."""
from __future__ import annotations

import csv
import io
import json
from typing import Literal
from urllib.parse import quote

from fastapi import APIRouter, Depends, HTTPException, Query, UploadFile
from fastapi.responses import FileResponse, Response
from pydantic import BaseModel, EmailStr, Field

from ..config import get_settings
from ..documents import access, service, store
from ..documents.extract import UnsupportedFile
from ..llm.formats import ProviderError
from ..security import chat_rate_limit, current_user

router = APIRouter(prefix="/api/documents", tags=["documents"])


class AskIn(BaseModel):
    question: str = Field(min_length=2, max_length=4000)
    document_ids: list[str] = Field(default_factory=list, max_length=50)


class CompareIn(BaseModel):
    document_ids: list[str] = Field(min_length=2, max_length=6)
    focus: str = Field(default="", max_length=1000)


class FieldIn(BaseModel):
    name: str = Field(min_length=1, max_length=80)
    description: str = Field(default="", max_length=300)


class ExtractIn(BaseModel):
    fields: list[FieldIn] = Field(default_factory=list, max_length=40)
    preset: str | None = None


class AnalyzeIn(BaseModel):
    question: str = Field(min_length=2, max_length=2000)
    table_ids: list[str] = Field(default_factory=list, max_length=20)


class GenerateIn(BaseModel):
    prompt: str = Field(min_length=5, max_length=8000)
    format: Literal["docx", "xlsx", "pptx", "pdf", "md"] = "docx"
    source_ids: list[str] = Field(default_factory=list, max_length=20)
    filename: str | None = Field(default=None, max_length=120)


class SummarizeIn(BaseModel):
    style: Literal["brief", "bullets", "detailed"] = "brief"
    force: bool = False


class RenameIn(BaseModel):
    filename: str = Field(min_length=1, max_length=200)


class ShareIn(BaseModel):
    email: EmailStr


def _http(e: Exception) -> HTTPException:
    if isinstance(e, access.NotFound):
        return HTTPException(404, "Document not found")
    if isinstance(e, access.Forbidden):
        return HTTPException(403, str(e))
    if isinstance(e, UnsupportedFile):
        return HTTPException(415, str(e))
    if isinstance(e, ProviderError):
        return HTTPException(502, f"The AI model failed: {e}")
    if isinstance(e, RuntimeError):
        return HTTPException(503, str(e))
    return HTTPException(422, str(e))


async def _doc(user: dict, doc_id: str, action: str = access.READ) -> dict:
    try:
        await access.require(user["id"], doc_id, action)
    except (access.NotFound, access.Forbidden) as e:
        raise _http(e)
    doc = await store.get_doc(user["id"], doc_id)
    doc.pop("storage_path", None)
    return doc


ERRORS = (access.NotFound, access.Forbidden, UnsupportedFile, ValueError, ProviderError, RuntimeError)


# ---------------------------------------------------------------- library
@router.get("")
async def list_documents(user: dict = Depends(current_user)):
    return await store.list_docs(user["id"])


@router.post("")
async def upload(file: UploadFile, user: dict = Depends(current_user)):
    s = get_settings()
    data = await file.read(s.max_upload_mb * 1024 * 1024 + 1)
    if len(data) > s.max_upload_mb * 1024 * 1024:
        raise HTTPException(413, f"File is larger than {s.max_upload_mb} MB.")
    if not data:
        raise HTTPException(422, "File is empty.")
    try:
        return await service.ingest(user["id"], file.filename or "upload", data)
    except UnsupportedFile as e:
        raise _http(e)


@router.get("/presets")
async def presets(user: dict = Depends(current_user)):
    return {k: [{"name": n, "description": d} for n, d in v] for k, v in service.PRESETS.items()}


@router.post("/ask")
async def ask(body: AskIn, user: dict = Depends(chat_rate_limit)):
    try:
        return await service.ask(user["id"], body.question, body.document_ids or None)
    except ERRORS as e:
        raise _http(e)


@router.post("/compare")
async def compare(body: CompareIn, user: dict = Depends(chat_rate_limit)):
    try:
        return await service.compare(user["id"], body.document_ids, body.focus)
    except ERRORS as e:
        raise _http(e)


@router.post("/generate")
async def generate_doc(body: GenerateIn, user: dict = Depends(chat_rate_limit)):
    try:
        return await service.generate_document(user["id"], body.prompt, body.format, body.source_ids, body.filename)
    except ERRORS as e:
        raise _http(e)


# ---------------------------------------------------------------- one document
@router.get("/{doc_id}")
async def get_document(doc_id: str, user: dict = Depends(current_user)):
    doc = await _doc(user, doc_id)
    doc.pop("text_content", None)
    doc["tables_list"] = await store.tables(doc_id)
    doc["access"] = await access.grants(user["id"], doc_id) if doc["owned"] else None
    return doc


@router.patch("/{doc_id}")
async def rename(doc_id: str, body: RenameIn, user: dict = Depends(current_user)):
    await _doc(user, doc_id, access.MANAGE)
    await store.update_doc(doc_id, filename=body.filename.strip())
    return {"ok": True}


@router.delete("/{doc_id}")
async def delete(doc_id: str, user: dict = Depends(current_user)):
    """Owners delete the document for everyone; a viewer only removes it from their own library."""
    doc = await _doc(user, doc_id)
    if doc["owned"]:
        await service.delete(user["id"], doc_id)
        return {"ok": True, "deleted": True}
    await access.leave(user["id"], doc_id)
    return {"ok": True, "deleted": False}


@router.get("/{doc_id}/content")
async def content(doc_id: str, user: dict = Depends(current_user), offset: int = Query(0, ge=0), limit: int = Query(200, ge=1, le=2000)):
    await _doc(user, doc_id)
    chunks = await store.chunks(doc_id, offset + limit)
    return {"chunks": chunks[offset:offset + limit], "total": len(chunks)}


@router.get("/{doc_id}/download")
async def download(doc_id: str, user: dict = Depends(current_user)):
    await _doc(user, doc_id)
    f = await store.get_doc(user["id"], doc_id)
    return _file_response(f)


def _file_response(f: dict) -> FileResponse:
    """Serve user files safely: never inline active content (HTML/SVG) on our origin."""
    inline = f["mime_type"] in ("image/png", "image/jpeg", "image/gif", "image/webp", "application/pdf")
    disp = "inline" if inline else "attachment"
    return FileResponse(f["storage_path"], media_type=f["mime_type"] if inline else "application/octet-stream",
                        headers={"Content-Disposition": f"{disp}; filename*=UTF-8''{quote(f['filename'])}",
                                 "X-Content-Type-Options": "nosniff", "Content-Security-Policy": "sandbox; default-src 'none'",
                                 "Cache-Control": "private, no-store"})


@router.post("/{doc_id}/reindex")
async def reindex(doc_id: str, user: dict = Depends(current_user)):
    await _doc(user, doc_id, access.MANAGE)
    try:
        doc = await service.reindex(user["id"], doc_id)
    except ERRORS as e:
        raise _http(e)
    doc.pop("storage_path", None)
    doc.pop("text_content", None)
    return doc


@router.post("/{doc_id}/summarize")
async def summarize(doc_id: str, body: SummarizeIn, user: dict = Depends(chat_rate_limit)):
    try:
        return {"summary": await service.summarize(user["id"], doc_id, body.style, force=body.force)}
    except ERRORS as e:
        raise _http(e)


@router.post("/{doc_id}/extract")
async def extract(doc_id: str, body: ExtractIn, user: dict = Depends(chat_rate_limit),
                  format: Literal["json", "csv"] = "json"):
    try:
        res = await service.extract_fields(user["id"], doc_id, [f.model_dump() for f in body.fields], body.preset)
    except ERRORS as e:
        raise _http(e)
    if format == "csv":
        buf = io.StringIO()
        w = csv.writer(buf)
        w.writerow(["field", "value", "status", "source_text"])
        for f in res["fields"]:
            v = f["value"]
            w.writerow([f["field"], json.dumps(v) if isinstance(v, (list, dict)) else ("" if v is None else v), f["status"], f["quote"] or ""])
        return Response(buf.getvalue(), media_type="text/csv", headers={"Content-Disposition": 'attachment; filename="extraction.csv"'})
    return res


@router.get("/{doc_id}/tables")
async def tables(doc_id: str, user: dict = Depends(current_user)):
    try:
        return await service.table_list(user["id"], doc_id)
    except ERRORS as e:
        raise _http(e)


@router.get("/{doc_id}/tables/{table_id}")
async def table_rows(doc_id: str, table_id: str, user: dict = Depends(current_user),
                     offset: int = Query(0, ge=0), limit: int = Query(100, ge=1, le=1000)):
    try:
        return await service.table_rows(user["id"], doc_id, table_id, offset, limit)
    except ERRORS as e:
        raise _http(e)


@router.get("/{doc_id}/tables/{table_id}/csv")
async def table_csv(doc_id: str, table_id: str, user: dict = Depends(current_user)):
    from ..documents.tables import to_csv
    await _doc(user, doc_id)
    t = await store.table(doc_id, table_id)
    if not t:
        raise HTTPException(404, "Table not found")
    name = "".join(c if c.isalnum() else "-" for c in t["name"])[:60] or "table"
    return Response(to_csv(t["columns"], t["rows"]), media_type="text/csv",
                    headers={"Content-Disposition": f'attachment; filename="{name}.csv"'})


@router.post("/{doc_id}/analyze")
async def analyze(doc_id: str, body: AnalyzeIn, user: dict = Depends(chat_rate_limit)):
    try:
        return await service.analyze_table(user["id"], doc_id, body.question, body.table_ids or None)
    except ERRORS as e:
        raise _http(e)


# ---------------------------------------------------------------- sharing (owner only)
@router.get("/{doc_id}/access")
async def list_access(doc_id: str, user: dict = Depends(current_user)):
    try:
        return await access.grants(user["id"], doc_id)
    except ERRORS as e:
        raise _http(e)


@router.post("/{doc_id}/access")
async def share(doc_id: str, body: ShareIn, user: dict = Depends(current_user)):
    try:
        return await access.share(user["id"], doc_id, body.email)
    except ERRORS as e:
        raise _http(e)


@router.delete("/{doc_id}/access/{user_id}")
async def unshare(doc_id: str, user_id: str, user: dict = Depends(current_user)):
    try:
        if not await access.unshare(user["id"], doc_id, user_id):
            raise HTTPException(404, "That user doesn't have access")
    except ERRORS as e:
        raise _http(e)
    return {"ok": True}


