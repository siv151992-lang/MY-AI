"""OCR with Tesseract (local: document images never leave the server for OCR)."""
from __future__ import annotations

import io
import shutil
import subprocess
import tempfile
from functools import lru_cache
from pathlib import Path

MAX_OCR_PAGES = 40


@lru_cache
def available() -> bool:
    return shutil.which("tesseract") is not None


def ocr_image(data: bytes, lang: str = "eng") -> str:
    if not available():
        return ""
    import pytesseract
    from PIL import Image, ImageOps
    img = Image.open(io.BytesIO(data))
    img = ImageOps.exif_transpose(img).convert("L")
    if max(img.size) < 1200:  # small screenshots OCR better upscaled
        scale = 1200 / max(img.size)
        img = img.resize((int(img.width * scale), int(img.height * scale)))
    return pytesseract.image_to_string(img, lang=lang, timeout=60).strip()


def render_pdf_page(pdf: bytes, page: int, dpi: int = 200) -> bytes | None:
    """PNG of one PDF page via poppler's pdftoppm (None if poppler is missing)."""
    if not shutil.which("pdftoppm"):
        return None
    with tempfile.TemporaryDirectory() as d:
        src = Path(d) / "in.pdf"
        src.write_bytes(pdf)
        out = Path(d) / "page"
        subprocess.run(["pdftoppm", "-r", str(dpi), "-f", str(page), "-l", str(page), "-png", "-singlefile", str(src), str(out)],
                       check=True, timeout=60, capture_output=True)
        return (Path(d) / "page.png").read_bytes()
