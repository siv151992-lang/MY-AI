"""Document ingestion: type detection, text extraction and chunking."""
from __future__ import annotations

import io
import re

IMAGE_TYPES = {"image/png", "image/jpeg", "image/gif", "image/webp"}
TEXT_EXT = {".txt", ".md", ".csv", ".json", ".py", ".js", ".ts", ".tsx", ".jsx", ".java", ".kt", ".go", ".rs",
            ".c", ".cpp", ".h", ".cs", ".rb", ".php", ".sql", ".html", ".css", ".xml", ".yaml", ".yml", ".toml",
            ".sh", ".log", ".ini", ".properties", ".gradle", ".swift", ".r", ".scala"}
FULL_TEXT_LIMIT = 24_000  # chars; above this only retrieval excerpts go into context


class UnsupportedFile(Exception):
    pass


def classify(filename: str, mime: str, head: bytes) -> str:
    """Decide by magic bytes first (don't trust client-supplied MIME)."""
    if head.startswith(b"\x89PNG") or head.startswith(b"\xff\xd8\xff") or head[:6] in (b"GIF87a", b"GIF89a") \
            or (head[:4] == b"RIFF" and head[8:12] == b"WEBP"):
        return "image"
    return "document"


def sniff_image_mime(head: bytes) -> str:
    if head.startswith(b"\x89PNG"):
        return "image/png"
    if head.startswith(b"\xff\xd8\xff"):
        return "image/jpeg"
    if head[:6] in (b"GIF87a", b"GIF89a"):
        return "image/gif"
    return "image/webp"


def extract_text(filename: str, data: bytes) -> str:
    name = filename.lower()
    ext = name[name.rfind("."):] if "." in name else ""
    if data.startswith(b"%PDF"):
        from pypdf import PdfReader
        reader = PdfReader(io.BytesIO(data))
        pages = []
        for i, page in enumerate(reader.pages, 1):
            t = (page.extract_text() or "").strip()
            if t:
                pages.append(f"[Page {i}]\n{t}")
        if not pages:
            raise UnsupportedFile("This PDF has no extractable text (it may be scanned images).")
        return "\n\n".join(pages)
    if data.startswith(b"PK") and ext == ".docx":
        import docx
        d = docx.Document(io.BytesIO(data))
        parts = [p.text for p in d.paragraphs if p.text.strip()]
        for table in d.tables:
            for r in table.rows:
                parts.append(" | ".join(c.text.strip() for c in r.cells))
        return "\n".join(parts)
    if ext in TEXT_EXT or not ext:
        try:
            return data.decode("utf-8")
        except UnicodeDecodeError:
            return data.decode("latin-1")
    raise UnsupportedFile(f"Unsupported file type '{ext}'. Upload PDF, DOCX, text/code files or images.")


def chunk_text(text: str, size: int = 1200, overlap: int = 200) -> list[str]:
    """Split on paragraph boundaries where possible, with overlap for retrieval continuity."""
    text = re.sub(r"\n{3,}", "\n\n", text).strip()
    if not text:
        return []
    if len(text) <= size:
        return [text]
    paras = text.split("\n\n")
    chunks: list[str] = []
    cur = ""
    for p in paras:
        while len(p) > size:  # hard-split giant paragraphs
            head, p = p[:size], p[size - overlap:]
            if cur:
                chunks.append(cur)
                cur = ""
            chunks.append(head)
        if len(cur) + len(p) + 2 <= size:
            cur = f"{cur}\n\n{p}" if cur else p
        else:
            if cur:
                chunks.append(cur)
            tail = cur[-overlap:] if cur else ""
            cur = f"{tail}\n\n{p}" if tail else p
    if cur:
        chunks.append(cur)
    return chunks
