"""Shared fixtures: paths to the reference PDFs (read-only)."""

from __future__ import annotations

from pathlib import Path

import pytest

PDF_DIR = Path("/home/scanum/parser_choose/pdf")


def _pdf(name: str) -> Path:
    p = PDF_DIR / name
    if not p.exists():
        pytest.skip(f"reference PDF not available: {p}")
    return p


@pytest.fixture()
def p5_pdf() -> Path:
    """1 page, text_based, tables + columns, Cyrillic."""
    return _pdf("p5.pdf")


@pytest.fixture()
def p27_pdf() -> Path:
    """1 page, image_based scan."""
    return _pdf("p27.pdf")


@pytest.fixture()
def big_test_pdf() -> Path:
    """44 pages, mixed; 10 OCR pages (26, 35-43, 1-indexed). Slow for OCR."""
    return _pdf("big_test.pdf")
