"""Shared fixtures: golden fixtures committed in tests/fixtures.

Committed fixtures (TKT hardening П6) make the suite self-sufficient —
the whole fast+slow CI runs on a clean machine with no external corpus.
``big_test_pdf`` (21 MB, mixed 44 pp) is too heavy to commit and keeps
its external-corpus path with a graceful skip.
"""

from __future__ import annotations

import io
import zipfile
from pathlib import Path

import pytest

PDF_DIR = Path("/home/scanum/parser_choose/pdf")  # external corpus, optional
FIXTURE_DIR = Path(__file__).parent / "fixtures"


def _pdf(name: str) -> Path:
    # Committed golden fixture first; fall back to the external corpus
    # (dev machines may carry newer/rounder copies); skip if neither.
    committed = FIXTURE_DIR / "pdf" / name
    if committed.exists():
        return committed
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


@pytest.fixture()
def docx_path(tmp_path: Path) -> Path:
    """Minimal structurally-valid docx (OOXML zip with word/document.xml).

    Fully routable since ADR-0003: the native stdlib inspector parses it
    end-to-end. Kept tiny on purpose — the synthetic control for fast
    unit tests (golden corpus volumes live in tests/test_docx.py).
    """
    content_types = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
        '<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>'
        '<Default Extension="xml" ContentType="application/xml"/>'
        '<Override PartName="/word/document.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml"/>'
        "</Types>"
    )
    rels = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
        '<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="word/document.xml"/>'
        "</Relationships>"
    )
    document = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">'
        "<w:body><w:p><w:r><w:t>smoke docx</w:t></w:r></w:p></w:body></w:document>"
    )
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("[Content_Types].xml", content_types)
        zf.writestr("_rels/.rels", rels)
        zf.writestr("word/document.xml", document)
    p = tmp_path / "doc.docx"
    p.write_bytes(buf.getvalue())
    return p


def _djvu_fixture(name: str) -> Path:
    """Committed tiny djvu (2 pages, 200x300 @300 dpi, built once with
    c44+djvm — see docs/wayfinder/tickets/TKT-DJV-08)."""
    p = FIXTURE_DIR / "djvu" / name
    if not p.exists():
        pytest.skip(f"djvu fixture not available: {p}")
    return p


@pytest.fixture()
def plain_djvu() -> Path:
    """2 pages, NO hidden text layer — the layerless OCR path."""
    return _djvu_fixture("plain.djvu")


@pytest.fixture()
def text_layer_djvu() -> Path:
    """2 pages, page 1 carries a hidden text layer ("Hello")."""
    return _djvu_fixture("text_layer.djvu")
