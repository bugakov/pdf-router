"""Shared rasterization for OCR and image extraction.

One function: ``rasterize_page``. Replaces the duplicated ``_render_page_png``
methods in the OCR adapters (tesseract, deepseek_ocr)."""

from __future__ import annotations


class RasterizeError(RuntimeError):
    """Raised when page rasterization fails."""


def rasterize_page(pdf_path: str, page_idx: int, *, dpi: int = 200) -> bytes:
    """Render one PDF page to a PNG as bytes.

    Args:
        pdf_path: path to the PDF file.
        page_idx: 0-indexed page number.
        dpi: resolution in dots per inch (default 200).

    Returns:
        PNG image as bytes.

    Raises:
        RasterizeError: if the page index is out of range or rendering fails.
    """
    import pymupdf

    doc = pymupdf.open(pdf_path)
    try:
        if not 0 <= page_idx < doc.page_count:
            raise RasterizeError(
                f"page index {page_idx} out of range (0..{doc.page_count - 1})"
            )
        page = doc[page_idx]
        zoom = dpi / 72.0
        return page.get_pixmap(
            matrix=pymupdf.Matrix(zoom, zoom), alpha=False
        ).tobytes(output="png")
    finally:
        doc.close()
