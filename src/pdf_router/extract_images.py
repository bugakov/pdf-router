"""Image extraction from PDF pages.

Two flat functions — no classify_page, no class hierarchy:
- ``extract_raster_photos``: Image XObjects, filters page-background (area >= 80%).
- ``extract_vector_diagrams``: vector paths rendered to PNG.

Image paths follow the sidecar schema from TKT-RT-12:
  <output_dir>/<doc-id>/images/page{N:04d}_{kind}_{idx:02d}.png
where doc-id = SHA-256 of the absolute PDF path (12 hex chars).
Paths stored in PageResult.images are relative to output_dir.
"""

from __future__ import annotations

import hashlib
import shutil
from pathlib import Path

from pdf_router.ir import PageResult


# Minimum bounding-rect area (pt²) for a vector diagram to be saved.
# Engineering drawings (ТКР1) have many tiny decorative paths; this
# threshold filters them out. Calibrated against big_test.pdf pages 26-44.
_MIN_DIAGRAM_AREA = 500.0  # ~22×22 pt


def doc_id_from_path(pdf_path: str) -> str:
    """12-char hex SHA-256 of the absolute PDF path — stable across runs."""
    abs_path = str(Path(pdf_path).resolve())
    return hashlib.sha256(abs_path.encode()).hexdigest()[:12]


def page_images_dir(output_dir: Path, doc_id: str) -> Path:
    """Sidecar images directory for one document."""
    d = output_dir / doc_id / "images"
    d.mkdir(parents=True, exist_ok=True)
    return d


# ---------------------------------------------------------------------------
# Raster photos
# ---------------------------------------------------------------------------


def extract_raster_photos(
    page: PageResult,
    pdf_path: str,
    output_dir: Path,
) -> list[str]:
    """Extract embedded raster images (JPEG, PNG, etc.) from one page.

    Returns a list of relative paths from ``output_dir`` (e.g.
    ``abcdef123456/images/page0001_photo_00.png``). Returns ``[]`` if
    the page has no images, or if the only image covers >= 80% of the
    page area (page-background scan — not useful for RAG).

    The raster is decoded via PyMuPDF's ``Page.extract_image(xref)``.
    """
    import pymupdf

    did = doc_id_from_path(pdf_path)
    img_dir = page_images_dir(output_dir, did)

    doc = pymupdf.open(pdf_path)
    try:
        pymupdf_page = doc[page.page]
        if pymupdf_page is None:
            return []
        raw_images = pymupdf_page.get_images(full=True)
        if not raw_images:
            return []

        page_area = pymupdf_page.rect.width * pymupdf_page.rect.height

        saved: list[str] = []
        photo_count = 0

        for xref, *rest in raw_images:
            # rest: width, height, bpc, colorspace, xref_name, smask, fname, compress
            img_width = rest[0]
            img_height = rest[1]

            # Filter: page-background scan (>= 80% page area).
            if img_width * img_height >= 0.80 * page_area:
                continue

            try:
                pix = doc.extract_image(xref)
            except Exception:
                continue

            if not pix:
                continue

            ext = pix.get("ext", "png")
            ext = "png" if ext.lower() in {"jpeg", "jpg", "jpx", "jp2"} else ext
            fname = f"page{page.page + 1:04d}_photo_{photo_count:02d}.{ext}"
            fpath = img_dir / fname
            fpath.write_bytes(pix["image"])  # pix["image"] is bytes

            # Store relative path from output_dir.
            saved.append(str(fpath.relative_to(output_dir)))
            photo_count += 1
    finally:
        doc.close()

    return saved


# ---------------------------------------------------------------------------
# Vector diagrams
# ---------------------------------------------------------------------------


def extract_vector_diagrams(
    page: PageResult,
    pdf_path: str,
    output_dir: Path,
) -> list[str]:
    """Extract vector drawings from one page, rendered as PNG.

    Returns a list of relative paths from ``output_dir``. Each drawing whose
    bounding rect has area >= ``_MIN_DIAGRAM_AREA`` is saved as a page-scale
    PNG showing the vector paths on white. Small or empty drawings are skipped.

    Engineering PDFs (ТКР1) have many tiny decorative paths; the area
    threshold filters them. Diagrams are rendered via PyMuPDF pixmap.
    """
    import pymupdf

    did = doc_id_from_path(pdf_path)
    img_dir = page_images_dir(output_dir, did)

    doc = pymupdf.open(pdf_path)
    try:
        pymupdf_page = doc[page.page]
        if pymupdf_page is None:
            return []
        drawings = pymupdf_page.get_drawings()
    finally:
        doc.close()

    if not drawings:
        return []

    # Filter: keep only drawings with a meaningful bounding rect.
    large = [d for d in drawings if (d.get("rect") or pymupdf.Rect()).area >= _MIN_DIAGRAM_AREA]
    if not large:
        return []

    saved: list[str] = []
    diagram_count = 0

    # Re-open doc once for all pixmap renders (vector drawing extraction).
    doc2 = pymupdf.open(pdf_path)
    try:
        pymupdf_page2 = doc2[page.page]
        if pymupdf_page2 is None:
            return saved
        zoom = 200 / 72.0  # 200 DPI
        mat = pymupdf.Matrix(zoom, zoom)

        for drawing in large:
            rect: pymupdf.Rect = drawing.get("rect") or pymupdf.Rect()
            pix = pymupdf_page2.get_pixmap(matrix=mat, clip=rect, alpha=False)

            fname = f"page{page.page + 1:04d}_diagram_{diagram_count:02d}.png"
            fpath = img_dir / fname
            pix.save(str(fpath))

            saved.append(str(fpath.relative_to(output_dir)))
            diagram_count += 1
    finally:
        doc2.close()

    return saved
