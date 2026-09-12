"""Shared fixtures: paths to the reference PDFs (read-only)."""

from __future__ import annotations

import io
import zipfile
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
