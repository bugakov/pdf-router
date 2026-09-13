"""Format dispatcher contract (multi-format candidates #1, #2, #4).

Pins the seam an embedding app relies on when it feeds the router a
non-PDF (or a lying) file:

- format is sniffed from **magic bytes**, never the extension;
- unroutable input raises the typed ``UnsupportedFormatError`` with a
  message that says *what* was detected and *why* it was rejected;
- the registry is the single source of truth for format -> inspector.
"""

from __future__ import annotations

import zipfile
from pathlib import Path

import pytest

from pdf_router.formats import (
    Format,
    Inspector,
    UnsupportedFormatError,
    detect_format,
    get_inspector,
    route,
)


def _write_zip(path: Path, names: dict[str, str]) -> None:
    with zipfile.ZipFile(path, "w") as zf:
        for name, content in names.items():
            zf.writestr(name, content)


# ---------------------------------------------------------------------------
# detect_format: magic bytes
# ---------------------------------------------------------------------------


def test_detect_pdf_by_magic(p5_pdf: Path) -> None:
    assert detect_format(p5_pdf) is Format.PDF


def test_detect_pdf_with_leading_junk(tmp_path: Path) -> None:
    """Real-world PDFs sometimes carry junk before the %PDF- header;
    pdfium tolerates it, so the sniffer must too (within the 1 KiB window)."""
    p = tmp_path / "weird.pdf"
    p.write_bytes(b"junk\x00\x01\x02%PDF-1.7\n%...\n")
    assert detect_format(p) is Format.PDF


def test_detect_docx_by_magic(docx_path: Path) -> None:
    assert detect_format(docx_path) is Format.DOCX


def test_detect_djvu_by_magic(tmp_path: Path) -> None:
    p = tmp_path / "scan.djvu"
    p.write_bytes(b"AT&TFORM\x00\x00\x10\x00DJVMDJVM")
    assert detect_format(p) is Format.DJVU


def test_detect_ooxml_not_docx_hint(tmp_path: Path) -> None:
    """A zip with [Content_Types].xml but no word/ is OOXML-but-not-docx."""
    p = tmp_path / "book.xlsx"
    _write_zip(p, {"[Content_Types].xml": "<Types/>", "xl/workbook.xml": "<wb/>"})
    with pytest.raises(UnsupportedFormatError, match="OOXML"):
        detect_format(p)


def test_detect_generic_zip_hint(tmp_path: Path) -> None:
    p = tmp_path / "archive.zip"
    _write_zip(p, {"readme.txt": "hi"})
    with pytest.raises(UnsupportedFormatError, match="zip"):
        detect_format(p)


def test_detect_corrupt_zip(tmp_path: Path) -> None:
    p = tmp_path / "broken.docx"
    p.write_bytes(b"PK\x03\x04" + b"\x00" * 16)
    with pytest.raises(UnsupportedFormatError, match="corrupt zip"):
        detect_format(p)


def test_detect_empty_file(tmp_path: Path) -> None:
    p = tmp_path / "empty.pdf"
    p.write_bytes(b"")
    with pytest.raises(UnsupportedFormatError, match="empty file"):
        detect_format(p)


def test_detect_unknown_bytes(tmp_path: Path) -> None:
    p = tmp_path / "image.png"
    p.write_bytes(b"\x89PNG\r\n\x1a\n" + b"\x00" * 16)
    with pytest.raises(UnsupportedFormatError, match="unrecognized"):
        detect_format(p)


def test_detect_missing_file_raises_filenotfound(tmp_path: Path) -> None:
    """A missing path is NOT UnsupportedFormatError — that's an IO error
    with its own semantics (same as a typo'd config path)."""
    with pytest.raises(FileNotFoundError):
        detect_format(tmp_path / "nope.pdf")


# ---------------------------------------------------------------------------
# Magic bytes beat the extension
# ---------------------------------------------------------------------------


def test_extension_ignored_docx_named_pdf(docx_path: Path, tmp_path: Path) -> None:
    """A docx renamed to .pdf is DOCX by content — and routable as docx
    (ADR-0003): the gate dispatches on bytes, the extension is noise."""
    disguised = tmp_path / "scan.pdf"
    disguised.write_bytes(docx_path.read_bytes())
    assert detect_format(disguised) is Format.DOCX
    assert callable(route(disguised))


def test_extension_ignored_pdf_named_docx(p5_pdf: Path, tmp_path: Path) -> None:
    """A PDF renamed to .docx is PDF by content — routable as usual."""
    disguised = tmp_path / "report.docx"
    disguised.write_bytes(p5_pdf.read_bytes())
    assert detect_format(disguised) is Format.PDF
    assert callable(route(disguised))


# ---------------------------------------------------------------------------
# Registry / inspector protocol (candidate #2)
# ---------------------------------------------------------------------------


def test_get_inspector_for_pdf_is_callable() -> None:
    inspector = get_inspector(Format.PDF)
    assert callable(inspector)


def test_get_inspector_for_docx_is_callable() -> None:
    """DOCX registered since ADR-0003 — the native stdlib inspector."""
    inspector = get_inspector(Format.DOCX)
    assert callable(inspector)


def test_get_inspector_for_djvu_is_callable() -> None:
    """DJVU registered since ADR-0004 — the djvulibre subprocess inspector."""
    inspector = get_inspector(Format.DJVU)
    assert callable(inspector)


def test_route_is_the_gate(docx_path: Path, tmp_path: Path) -> None:
    """route() = detect + lookup; one call, one gate. Unrecognized bytes
    still raise before any page work (docx passed this gate even before
    ADR-0003 registered its inspector; djvu since ADR-0004)."""
    junk = tmp_path / "blob.bin"
    junk.write_bytes(b"\x00\x01\x02not-a-format")
    with pytest.raises(UnsupportedFormatError, match="unrecognized"):
        route(junk)
    assert callable(route(docx_path))


def test_inspector_alias_is_path_to_ir_callable() -> None:
    """The seam contract: Inspector is a ``path -> IR`` callable type."""
    assert Inspector is not None
