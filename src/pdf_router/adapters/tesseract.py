"""Tesseract OCR adapter (CLI subprocess). Opt-in.

Renders the page to PNG via PyMuPDF (200 DPI default) and runs the
``tesseract`` binary with the configured languages. Errors raise; the adapter
never returns empty silently.
"""

from __future__ import annotations

import shutil
import subprocess
from typing import Any, ClassVar


class TesseractError(RuntimeError):
    """Typed failure of the tesseract path."""


class AdapterTesseract:
    name: ClassVar[str] = "tesseract"

    def __init__(self, params: dict[str, Any] | None = None) -> None:
        self.params = dict(params or {})
        self.bin: str = self.params.get("bin") or "tesseract"
        languages = self.params.get("languages") or ["eng"]
        self.languages: str = "+".join(languages) if isinstance(languages, list) else str(languages)
        self.dpi: int = int(self.params.get("dpi", 200))

    def available(self) -> bool:
        return shutil.which(self.bin) is not None

    def ocr_page(self, pdf_path: str, page_0indexed: int) -> str:
        if not self.available():
            raise TesseractError(f"tesseract binary not found: {self.bin}")
        png = self._render_page_png(pdf_path, page_0indexed)
        proc = subprocess.run(
            [self.bin, "stdin", "stdout", "-l", self.languages, "--dpi", str(self.dpi)],
            input=png,
            capture_output=True,
        )
        if proc.returncode != 0:
            raise TesseractError(f"tesseract exited {proc.returncode}: {proc.stderr.decode(errors='replace')[:300]}")
        text = proc.stdout.decode("utf-8", errors="replace").strip()
        if not text:
            raise TesseractError(f"page {page_0indexed + 1}: empty tesseract result")
        return text

    def _render_page_png(self, pdf_path: str, page_0indexed: int) -> bytes:
        import pymupdf

        doc = pymupdf.open(pdf_path)
        try:
            if not 0 <= page_0indexed < doc.page_count:
                raise TesseractError(f"page index {page_0indexed} out of range (0..{doc.page_count - 1})")
            page = doc[page_0indexed]
            zoom = self.dpi / 72.0
            return page.get_pixmap(matrix=pymupdf.Matrix(zoom, zoom), alpha=False).tobytes(output="png")
        finally:
            doc.close()
