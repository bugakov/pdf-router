"""Loud partial-success contract (architecture candidate #4).

Invariant: ``page.error is None`` *only* when the page really succeeded.
Every failure must be visible in the IR, counted in the report, and
reflected in the CLI exit code (2 = partial success, outputs written).
"""

from __future__ import annotations

from pathlib import Path

import pdf_router.core as core_mod
from pdf_router.config import RouterConfig
from pdf_router.core import process_pdf


def test_internal_error_recorded_and_counted(p27_pdf: Path, monkeypatch) -> None:
    """A non-OcrError crash in the OCR path must not vanish: it lands in
    page.error and counts as a failed page (not pages_ocr)."""

    def boom(pdf_path, page_idx, config):
        raise ValueError("engine exploded")

    monkeypatch.setattr(core_mod, "ocr_page", boom)

    ir, report = process_pdf(str(p27_pdf), RouterConfig())
    page = ir.pages[0]
    assert page.error is not None
    assert page.error.startswith("internal_error:")
    assert "ValueError" in page.error
    assert report.pages_failed == 1
    assert report.pages_ocr == 0
    assert report.pages_native + report.pages_ocr + report.pages_failed == report.pages_total


def test_native_page_crash_counts_failed(p5_pdf: Path, monkeypatch) -> None:
    """A crash on a native (no-OCR) page must count as failed too —
    the report must not contradict the IR it describes."""

    def boom(*args, **kwargs):
        raise RuntimeError("quality check exploded")

    monkeypatch.setattr(core_mod, "looks_like_text", boom)

    ir, report = process_pdf(str(p5_pdf), RouterConfig())
    page = ir.pages[0]
    assert page.error is not None
    assert page.error.startswith("internal_error:")
    assert report.pages_failed == 1
    assert report.pages_native == 0


def test_image_failure_counted(p5_pdf: Path, monkeypatch) -> None:
    """Image extraction failure: ERROR log + report counter, text unaffected."""

    def boom(*args, **kwargs):
        raise OSError("disk full")

    monkeypatch.setattr(core_mod, "extract_raster_photos", boom)

    ir, report = process_pdf(str(p5_pdf), RouterConfig())
    assert ir.pages[0].images == []
    assert report.pages_image_failed == 1
    assert report.pages_failed == 0  # text path is fine
    assert ir.pages[0].error is None
