"""Tests for extract_images — RT-12 sidecar layout + race-condition guard.

Covers:
- Path schema (doc-id, page, kind, ext)
- bg-filter (>= 80% page area skipped)
- race condition fix in process_pdf: each page gets its own images list
"""

from __future__ import annotations

import subprocess
import tempfile
from pathlib import Path

import pytest

from pdf_router.config import RouterConfig
from pdf_router.core import process_pdf
from pdf_router.extract_images import (
    _DEFAULT_VECTOR_MIN_SHARE,
    doc_id_from_path,
    page_images_dir,
)
from pdf_router.ir import PageResult


PZU = Path("/tmp/pzu_test.pdf")


@pytest.fixture(scope="session", autouse=True)
def ensure_pzu() -> None:
    """Make sure the test PDF is available; copy from kotel archive if missing."""
    if PZU.exists():
        return
    src = Path(
        "/home/scanum/kotel-zamechanna/archive/ВЫДАЧА_БМК Лехово/2. ПД/"
        "Проектная документация/Раздел 2. Схема планировочной организации земельного у~"
        "/Раздел ПД 2-ПЗУ.pdf"
    )
    if not src.exists():
        pytest.skip(f"pzu_test.pdf not found at {PZU} and source {src} missing")
    PZU.parent.mkdir(parents=True, exist_ok=True)
    subprocess.run(["cp", str(src), str(PZU)], check=True)


# --- path schema -------------------------------------------------------------


def test_doc_id_is_sha256_12hex() -> None:
    did = doc_id_from_path("/tmp/foo/bar.pdf")
    assert len(did) == 12
    assert all(c in "0123456789abcdef" for c in did)


def test_page_images_dir_layout() -> None:
    img_dir = page_images_dir(Path("/tmp/out"), "abc123def456")
    assert img_dir == Path("/tmp/out/abc123def456/images")


# --- bg filter ----------------------------------------------------------------


def test_extract_page_images_raster_writes_sidecar(monkeypatch) -> None:
    """Smoke: extraction produces files under <output_dir>/<doc-id>/images/."""
    from pdf_router.extract_images import extract_page_images

    with tempfile.TemporaryDirectory() as tmp:
        out_dir = Path(tmp)
        saved, _dropped = extract_page_images(
            PageResult(page=0), str(PZU), out_dir, raster=True, vector=False
        )
        assert saved, "expected at least one image on pzu page 1"
        for rel in saved:
            fpath = out_dir / rel
            assert fpath.exists(), f"file not written: {fpath}"
            # Schema: <doc-id>/images/page{N:04d}_{kind}_{idx:02d}.<ext>
            parts = rel.split("/")
            assert len(parts) == 3
            assert parts[1] == "images"
            assert parts[2].startswith("page0001_photo_")
            assert parts[2].endswith(".png") or parts[2].endswith(".jpeg")


def test_extract_page_images_no_flags_noop() -> None:
    """Both flags off: no open, no dirs, no files — plain []."""
    from pdf_router.extract_images import extract_page_images

    with tempfile.TemporaryDirectory() as tmp:
        out_dir = Path(tmp)
        saved, dropped = extract_page_images(
            PageResult(page=0), str(PZU), out_dir, raster=False, vector=False
        )
        assert (saved, dropped) == ([], 0)
        assert list(out_dir.iterdir()) == [], "no-op must not create sidecar dirs"


def test_extract_page_images_opens_pdf_once(monkeypatch) -> None:
    """REGRESSION: one PDF open per page regardless of enabled passes.

    Previously raster opened the doc once and vector twice (drawings pass +
    pixmap re-open) — 3 opens/page, ~100 ms each on large engineering PDFs.
    """
    import pymupdf

    from pdf_router.extract_images import extract_page_images

    real_open = pymupdf.open
    open_count = {"n": 0}

    def counting_open(*args, **kwargs):
        open_count["n"] += 1
        return real_open(*args, **kwargs)

    monkeypatch.setattr(pymupdf, "open", counting_open)

    with tempfile.TemporaryDirectory() as tmp:
        saved, dropped = extract_page_images(
            PageResult(page=0), str(PZU), Path(tmp), raster=True, vector=True
        )
    assert open_count["n"] == 1, f"expected 1 open, got {open_count['n']}"
    assert isinstance(saved, list)
    assert isinstance(dropped, int)


def test_extract_vector_diagrams_threshold() -> None:
    """Порог вектора — доля площади страницы, а не абсолютная площадь.

    0,001 — это прежние 500 pt² на странице A4, то есть поведение по
    умолчанию сохранено. Реальное различение порогов проверяет
    test_full_page_rasters.py::test_vector_threshold_is_share_of_page.
    """
    assert 0.0 < _DEFAULT_VECTOR_MIN_SHARE < 0.01


# --- race-condition guard (regression test) ----------------------------------


@pytest.mark.slow
def test_process_pdf_distributes_images_across_pages() -> None:
    """REGRESSION: every page with images must see its own images list.

    Previously, process_pdf iterated futures by `page.page` int keys, but the
    surrounding `for page in doc.pages` loop bound `page` to the last iteration
    only. Result: only the last page received `page.images`, all others got [].
    """
    with tempfile.TemporaryDirectory() as tmp:
        cfg = RouterConfig()
        ir, _ = process_pdf(str(PZU), cfg, output_dir=Path(tmp))
        pages_with_files_on_disk = set()
        pages_with_images_in_ir = set()
        for img_dir in (Path(tmp)).glob("*/images"):
            for f in img_dir.glob("page*_photo_*.png"):
                # page0001_photo_00.png → page 0 (zero-based)
                num = int(f.name[4:8]) - 1
                pages_with_files_on_disk.add(num)
        for p in ir.pages:
            if p.images:
                pages_with_images_in_ir.add(p.page)
        assert pages_with_files_on_disk, "no images written to disk"
        assert pages_with_files_on_disk == pages_with_images_in_ir, (
            f"divergence: files on disk for {sorted(pages_with_files_on_disk)} "
            f"but IR.images populated only for {sorted(pages_with_images_in_ir)}"
        )


@pytest.mark.slow
def test_process_pdf_ir_json_has_images_per_page() -> None:
    """End-to-end CLI smoke: write the IR.json and confirm `images` appears on
    multiple pages, not just the last one."""
    with tempfile.TemporaryDirectory() as tmp:
        cfg = RouterConfig()
        ir, _ = process_pdf(str(PZU), cfg, output_dir=Path(tmp))
        pages_with_imgs = [p for p in ir.pages if p.images]
        assert len(pages_with_imgs) >= 2, (
            f"expected at least 2 pages with images, got {len(pages_with_imgs)}: "
            f"{[p.page for p in pages_with_imgs]}"
        )

