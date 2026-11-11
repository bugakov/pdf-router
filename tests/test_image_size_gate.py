"""Size gate for raster sidecars: drop icons and rules, keep figures.

Calibrated on the real S3 corpus (2026-10-01, 300+300 sampled images,
boundary cases inspected visually):
- DROP when w < 150 AND h < 150 (icons, thumbnails, bullets);
- DROP when min(w, h) < 8 (separator rules, frames);
- KEEP everything else: narrow horizontals turned out to be maps and
  phase portraits (335x149), panoramas carry chart slices with readable
  data — doubtful content is kept, only proven junk is dropped.
"""

from __future__ import annotations

import io
import tempfile
from pathlib import Path

import pytest

from pdf_router.config import RouterConfig
from pdf_router.core import process_pdf
from pdf_router.extract_images import keep_raster_photo
from pdf_router.ir import PageResult


def _png(w: int, h: int, color: tuple[int, int, int] = (200, 30, 30)) -> bytes:
    from PIL import Image

    buf = io.BytesIO()
    Image.new("RGB", (w, h), color).save(buf, format="PNG")
    return buf.getvalue()


def _pdf_with_photos(tmp: Path) -> Path:
    """One-page PDF with four embedded rasters of known dimensions."""
    import fitz

    pdf = tmp / "gate.pdf"
    doc = fitz.open()
    page = doc.new_page(width=600, height=800)
    specs = [(49, 36), (3, 562), (335, 149), (500, 400)]
    y = 10
    for w, h in specs:
        page.insert_image(
            fitz.Rect(10, y, 10 + min(w, 580), y + min(h, 180)),
            stream=_png(w, h),
        )
        y += 190
    doc.save(str(pdf))
    doc.close()
    return pdf


# --- predicate ----------------------------------------------------------------


@pytest.mark.parametrize(
    "w,h,expected,why",
    [
        (49, 36, False, "icon/thumbnail: both sides below 150"),
        (5, 5, False, "bullet"),
        (3, 562, False, "vertical rule: thin side below 8"),
        (1136, 32, True, "wide strip kept: doubtful content stays"),
        (335, 149, True, "narrow horizontal map/chart: keep"),
        (500, 400, True, "ordinary photo: keep"),
        (150, 150, True, "boundary is strict: 150 is kept"),
        (150, 149, True, "only one side below: kept"),
        (8, 1000, True, "boundary: thin side of exactly 8 is kept"),
        (7, 1000, False, "thin side of 7 drops"),
        (0, 0, False, "degenerate: both sides below 150, dropped by the size rule"),
    ],
)
def test_keep_raster_photo(w: int, h: int, expected: bool, why: str) -> None:
    assert keep_raster_photo(w, h, min_size=150, min_thin=8) is expected, why


def test_keep_raster_photo_defaults_keep_everything() -> None:
    assert keep_raster_photo(5, 5) is True
    assert keep_raster_photo(3, 562) is True
    # No degenerate-size special case: thresholds off means no gate at all.
    assert keep_raster_photo(0, 0) is True


# --- extractor ----------------------------------------------------------------


def test_size_gate_drops_junk_and_counts_it() -> None:
    from pdf_router.extract_images import extract_page_images

    with tempfile.TemporaryDirectory() as tmp:
        pdf = _pdf_with_photos(Path(tmp))
        out = Path(tmp) / "out"
        saved, dropped = extract_page_images(
            PageResult(page=0), str(pdf), out,
            raster=True, vector=False, min_size=150, min_thin=8,
        )
        assert dropped == 2, f"expected the 49x36 and 3x562 to drop, got {dropped}"
        assert len(saved) == 2, f"expected chart + photo kept, got {saved}"


def test_size_gate_off_by_default() -> None:
    from pdf_router.extract_images import extract_page_images

    with tempfile.TemporaryDirectory() as tmp:
        pdf = _pdf_with_photos(Path(tmp))
        out = Path(tmp) / "out"
        saved, dropped = extract_page_images(
            PageResult(page=0), str(pdf), out, raster=True, vector=False
        )
        assert dropped == 0
        assert len(saved) == 4


# --- report counter ------------------------------------------------------------


def test_process_pdf_counts_dropped_images() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        pdf = _pdf_with_photos(Path(tmp))
        cfg = RouterConfig()
        cfg.routing.images_min_size = 150
        cfg.routing.images_min_thin = 8
        ir, report = process_pdf(str(pdf), cfg, output_dir=Path(tmp) / "out")
        assert report.images_dropped == 2
        assert len(ir.pages[0].images) == 2


def test_aspect_ratio_gate() -> None:
    # A 2647x110 horizontal slice has AR ~24.0, should be dropped with max_aspect_ratio=4.0
    assert not keep_raster_photo(2647, 110, max_aspect_ratio=4.0)
    # A 50x1200 vertical slice has AR 24.0, should be dropped
    assert not keep_raster_photo(50, 1200, max_aspect_ratio=4.0)
    # A 800x600 photo has AR 1.33, should be kept
    assert keep_raster_photo(800, 600, max_aspect_ratio=4.0)
    # Off when 0.0
    assert keep_raster_photo(2647, 110, max_aspect_ratio=0.0)
