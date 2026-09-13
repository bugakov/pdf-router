"""Shared rasterization for OCR — the format-aware raster seam (ADR-0004).

One entry point per consumer need, each dispatching on magic bytes
(never the extension):

- :func:`rasterize_page` — page as PNG bytes (tesseract, deepseek_ocr);
- :func:`page_raster_array` — page as numpy RGB array (rapidocr);
- :func:`page_single_pdf` — page wrapped as a one-page PDF (glm_ocr).

PDF pages render with pymupdf exactly as before (same code paths, moved
here). djvu pages render per-page with ``ddjvu`` (strategy C, ADR-0004):
in-memory, no temp files, ``-scale=min(dpi, stored_dpi)`` so 72-dpi pages
are never upscaled. ``pdf_inspector_ocr`` is PDF-only by nature (Rust
pdfium core) and refuses non-PDF input with a typed error.

OCR engine signatures keep ``(path, page_idx, ...)`` — the seam lives
below them, in this module.
"""

from __future__ import annotations


class RasterizeError(RuntimeError):
    """Raised when page rasterization fails."""


def _kind(path: str) -> str:
    """Sniff container kind from magic bytes: "pdf" | "djvu".

    Raises RasterizeError for anything else — by the time a renderer runs,
    the format gate (formats.route) has already accepted the file, so an
    unknown kind here is an internal invariant violation.
    """
    with open(path, "rb") as fh:
        head = fh.read(1024)
    if b"%PDF-" in head:
        return "pdf"
    if head.startswith(b"AT&TFORM"):
        return "djvu"
    raise RasterizeError(f"not a routable document (magic bytes): {path}")


# ---------------------------------------------------------------------------
# PDF rendering (pymupdf)
# ---------------------------------------------------------------------------


def _pdf_open_page(pdf_path: str, page_idx: int):
    import pymupdf

    doc = pymupdf.open(pdf_path)
    if not 0 <= page_idx < doc.page_count:
        doc.close()
        raise RasterizeError(
            f"page index {page_idx} out of range (0..{doc.page_count - 1})"
        )
    return doc, doc[page_idx]


def _pdf_render_rgb(pdf_path: str, page_idx: int, *, dpi: int):
    doc, page = _pdf_open_page(pdf_path, page_idx)
    try:
        pix = page.get_pixmap(dpi=dpi, alpha=False)
        return pix.width, pix.height, pix.n, pix.samples
    finally:
        doc.close()


# ---------------------------------------------------------------------------
# djvu rendering (ddjvu subprocess — adapter owns the tool plumbing)
# ---------------------------------------------------------------------------


def _djvu_render_rgb(path: str, page_idx: int, *, dpi: int):
    from pdf_router.adapters.djvu import render_page_rgb_djvu

    try:
        w, h, body = render_page_rgb_djvu(path, page_idx, dpi=dpi)
    except ValueError as exc:  # DjvuError
        raise RasterizeError(str(exc)) from exc
    return w, h, 3, body


# ---------------------------------------------------------------------------
# Public seam
# ---------------------------------------------------------------------------


def render_page_rgb(
    path: str, page_idx: int, *, dpi: int = 200
) -> tuple[int, int, int, bytes]:
    """Render one page to raw pixel samples: ``(width, height, channels, bytes)``.

    The single acquisition point of the PageSource seam (TKT-DJV-11):
    pdf -> pymupdf, djvu -> ddjvu. Engines never see the format.
    """
    kind = _kind(path)
    if kind == "pdf":
        return _pdf_render_rgb(path, page_idx, dpi=dpi)
    return _djvu_render_rgb(path, page_idx, dpi=dpi)


def rasterize_page(path: str, page_idx: int, *, dpi: int = 200) -> bytes:
    """Render one page to a PNG as bytes (format-aware).

    Args:
        path: path to the document (PDF or djvu).
        page_idx: 0-indexed page number.
        dpi: resolution in dots per inch (default 200).

    Returns:
        PNG image as bytes.

    Raises:
        RasterizeError: if the page index is out of range or rendering fails.
    """
    kind = _kind(path)
    if kind == "pdf":
        import pymupdf

        doc, page = _pdf_open_page(path, page_idx)
        try:
            zoom = dpi / 72.0
            return page.get_pixmap(
                matrix=pymupdf.Matrix(zoom, zoom), alpha=False
            ).tobytes(output="png")
        finally:
            doc.close()
    # djvu: raw RGB -> PNG via pymupdf's Pixmap (no new dependencies).
    w, h, _n, samples = _djvu_render_rgb(path, page_idx, dpi=dpi)
    import pymupdf

    pix = pymupdf.Pixmap(pymupdf.csRGB, w, h, samples, False)
    return pix.tobytes(output="png")


def page_raster_array(path: str, page_idx: int, *, dpi: int = 200):
    """Render one page to a numpy RGB array ``(h, w, 3)`` (rapidocr's diet)."""
    import numpy as np

    w, h, n, samples = render_page_rgb(path, page_idx, dpi=dpi)
    img = np.frombuffer(samples, dtype=np.uint8)
    if n == 3:
        return img.reshape(h, w, 3)
    # RGBA (defensive; alpha=False everywhere) -> RGB.
    return img.reshape(h, w, n)[:, :, :3]


def page_single_pdf(path: str, page_idx: int) -> bytes:
    """Return one page as a standalone single-page PDF (glm_ocr's diet).

    pdf: extracted verbatim from the source (insert_pdf) — no re-render.
    djvu: the page raster is wrapped into a fresh one-page PDF.
    """
    import pymupdf

    if _kind(path) == "pdf":
        src = pymupdf.open(path)
        try:
            if not 0 <= page_idx < src.page_count:
                raise RasterizeError(
                    f"page index {page_idx} out of range (0..{src.page_count - 1})"
                )
            single = pymupdf.open()
            single.insert_pdf(src, from_page=page_idx, to_page=page_idx)
            return single.tobytes()
        finally:
            src.close()
    w, h, _n, samples = _djvu_render_rgb(path, page_idx, dpi=150)
    pix = pymupdf.Pixmap(pymupdf.csRGB, w, h, samples, False)
    img_pdf = pymupdf.open()
    page = img_pdf.new_page(width=w, height=h)
    page.insert_image(page.rect, pixmap=pix)
    return img_pdf.tobytes()
