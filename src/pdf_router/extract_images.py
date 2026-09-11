"""Image extraction from PDF pages.

One entry point — ``extract_page_images`` — no class hierarchy:
- raster pass: Image XObjects, filters page-background (area >= 80%).
- vector pass: vector paths rendered to PNG.

The PDF is opened once per page regardless of how many passes run
(previously up to 3 opens: 1 raster + 2 vector — measurable on large
engineering PDFs, where a single open costs ~100 ms per 20 MB).

Image paths follow the sidecar schema from TKT-RT-12:
  <output_dir>/<doc-id>/images/page{N:04d}_{kind}_{idx:02d}.png
where doc-id = SHA-256 of the absolute PDF path (12 hex chars).
Paths stored in PageResult.images are relative to output_dir.
"""

from __future__ import annotations

import hashlib
import logging
from pathlib import Path
from typing import TYPE_CHECKING

from pdf_router.ir import PageResult

if TYPE_CHECKING:
    import pymupdf

logger = logging.getLogger("pdf_router")


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


def extract_page_images(
    page: PageResult,
    pdf_path: str,
    output_dir: Path,
    *,
    raster: bool,
    vector: bool,
) -> list[str]:
    """Extract raster photos and/or vector diagrams from one page.

    Opens the PDF once and runs the requested passes against that single
    handle. Returns a list of relative paths from ``output_dir`` (e.g.
    ``abcdef123456/images/page0001_photo_00.png``); ``[]`` when both flags
    are off or the page yields nothing.
    """
    if not raster and not vector:
        return []

    import pymupdf

    did = doc_id_from_path(pdf_path)
    img_dir = page_images_dir(output_dir, did)

    saved: list[str] = []
    doc = pymupdf.open(pdf_path)
    try:
        pymupdf_page = doc[page.page]
        if pymupdf_page is None:
            return []
        if raster:
            saved.extend(
                _extract_raster_photos(doc, pymupdf_page, page, output_dir, img_dir)
            )
        if vector:
            saved.extend(
                _extract_vector_diagrams(pymupdf_page, page, output_dir, img_dir)
            )
    finally:
        doc.close()

    return saved


# ---------------------------------------------------------------------------
# Raster photos
# ---------------------------------------------------------------------------


def _extract_raster_photos(
    doc: pymupdf.Document,
    pymupdf_page: pymupdf.Page,
    page: PageResult,
    output_dir: Path,
    img_dir: Path,
) -> list[str]:
    """Extract embedded raster images (JPEG, PNG, etc.) from an open page.

    Returns ``[]`` if the page has no images, or if the only image covers
    >= 80% of the page area (page-background scan — not useful for RAG).
    The raster is decoded via PyMuPDF's ``Document.extract_image(xref)``.
    """
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

    return saved


# ---------------------------------------------------------------------------
# Vector diagrams
# ---------------------------------------------------------------------------


def _extract_vector_diagrams(
    pymupdf_page: pymupdf.Page,
    page: PageResult,
    output_dir: Path,
    img_dir: Path,
) -> list[str]:
    """Extract vector drawings from an open page, rendered as PNG.

    Each drawing whose bounding rect has area >= ``_MIN_DIAGRAM_AREA`` is
    saved as a page-scale PNG showing the vector paths on white. Small or
    empty drawings are skipped. Engineering PDFs (ТКР1) have many tiny
    decorative paths; the area threshold filters them.
    """
    import pymupdf

    drawings = pymupdf_page.get_drawings()
    if not drawings:
        return []

    def _area(r: pymupdf.Rect) -> float:
        # NB: pymupdf.Rect has no `.area` attribute — width*height is the
        # stable arithmetic (a latent AttributeError hid here while the
        # vector pass was default-off and untested with real drawings).
        return abs((r.x1 - r.x0) * (r.y1 - r.y0))

    # Filter: keep only drawings with a meaningful bounding rect.
    # Normalize first — get_drawings can emit inverted rects (x1 < x0),
    # whose abs()-area passes the threshold but breaks pixmap rendering.
    # Then clip to the page: drawings may extend past the page bounds,
    # and rendering an out-of-page clip fails (FzErrorArgument).
    page_rect = pymupdf_page.rect
    large: list[pymupdf.Rect] = []
    for d in drawings:
        rect = d.get("rect") or pymupdf.Rect()
        rect.normalize()
        rect &= page_rect
        if not rect.is_empty and _area(rect) >= _MIN_DIAGRAM_AREA:
            large.append(rect)
    if not large:
        return []

    saved: list[str] = []
    diagram_count = 0

    zoom = 200 / 72.0  # 200 DPI
    mat = pymupdf.Matrix(zoom, zoom)

    for rect in large:
        fname = f"page{page.page + 1:04d}_diagram_{diagram_count:02d}.png"
        fpath = img_dir / fname
        try:
            pix = pymupdf_page.get_pixmap(matrix=mat, clip=rect, alpha=False)
            pix.save(str(fpath))
        except Exception as exc:
            # One unrenderable clip must not fail the page's extraction
            # (core counts a raised error in pages_image_failed).
            logger.warning(
                "page %d: diagram clip %s not rendered: %s", page.page + 1, rect, exc
            )
            continue

        saved.append(str(fpath.relative_to(output_dir)))
        diagram_count += 1

    return saved
