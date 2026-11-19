"""Location-aware chunking: every chunk remembers its page/slide and section, so answers can cite them."""
from __future__ import annotations

from dataclasses import dataclass

from ..services.documents import chunk_text
from .extract import Block


@dataclass
class Chunk:
    content: str
    page: int | None
    section: str


def chunk_blocks(blocks: list[Block], size: int = 1200, overlap: int = 200, merge_below: int = 400) -> list[Chunk]:
    out: list[Chunk] = []
    pending: Block | None = None
    for b in blocks:
        # merge small neighbouring blocks that share a location (short paragraphs under one heading/page)
        if pending and pending.page == b.page and pending.section == b.section and len(pending.text) + len(b.text) < merge_below:
            pending = Block(pending.text + "\n\n" + b.text, b.page, b.section)
            continue
        if pending:
            out += [Chunk(c, pending.page, pending.section) for c in chunk_text(pending.text, size, overlap)]
        pending = b
    if pending:
        out += [Chunk(c, pending.page, pending.section) for c in chunk_text(pending.text, size, overlap)]
    return out


def location(page: int | None, section: str, doc_type: str) -> str:
    parts = []
    if page:
        parts.append(f"slide {page}" if doc_type == "pptx" else f"p. {page}")
    if section:
        parts.append(section[:80])
    return ", ".join(parts)
