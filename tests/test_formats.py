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


def test_detect_xlsx_by_magic(tmp_path: Path) -> None:
    """A zip with xl/workbook.xml is XLSX — routable (ADR-0008 / TKT-OI-05).

    The flip of the old "OOXML but not docx" hint: xlsx stopped being an
    error the moment its inspector shipped (no advertised-but-dead route).
    """
    p = tmp_path / "book.xlsx"
    _write_zip(p, {"[Content_Types].xml": "<Types/>", "xl/workbook.xml": "<wb/>"})
    assert detect_format(p) is Format.XLSX
    assert callable(route(p))


def test_detect_pptx_rejected_precisely(tmp_path: Path) -> None:
    """pptx keeps a typed, precise rejection — not the generic OOXML hint."""
    p = tmp_path / "deck.pptx"
    _write_zip(
        p,
        {
            "[Content_Types].xml": "<Types/>",
            "ppt/presentation.xml": "<p/>",
        },
    )
    with pytest.raises(UnsupportedFormatError, match="PowerPoint pptx is not routed"):
        detect_format(p)


def test_detect_ooxml_not_docx_xlsx_hint(tmp_path: Path) -> None:
    """An OOXML package that is neither docx nor xlsx says exactly that."""
    p = tmp_path / "book.pptx"
    _write_zip(p, {"[Content_Types].xml": "<Types/>"})
    with pytest.raises(UnsupportedFormatError, match="not docx/xlsx"):
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


def test_extension_ignored_xlsx_named_pdf(tmp_path: Path) -> None:
    """An xlsx renamed to .pdf is XLSX by content — routable (TKT-OI-05)."""
    disguised = tmp_path / "scan.pdf"
    _write_zip(disguised, {"[Content_Types].xml": "<Types/>", "xl/workbook.xml": "<wb/>"})
    assert detect_format(disguised) is Format.XLSX
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


def test_get_inspector_for_xlsx_is_callable() -> None:
    """XLSX registered since ADR-0008 — the native stdlib inspector."""
    inspector = get_inspector(Format.XLSX)
    assert callable(inspector)


def test_get_inspector_for_doc_is_callable() -> None:
    """DOC registered since ADR-0008 — the office-oxide inspector."""
    inspector = get_inspector(Format.DOC)
    assert callable(inspector)


def test_get_inspector_for_html_is_callable() -> None:
    """HTML registered since ADR-0008 — the stdlib tag-strip inspector."""
    inspector = get_inspector(Format.HTML)
    assert callable(inspector)


# ---------------------------------------------------------------------------
# Legacy DOC / HTML / Word lock files (TKT-OI-05 branches 0/5/6)
# ---------------------------------------------------------------------------

_OLE2_MAGIC = b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1"


def test_detect_word_lock_file_rejected_by_name(tmp_path: Path) -> None:
    """``~$`` lock files are the one deliberate name-based predicate: their
    162-byte content is not reliably detectable, so the name wins —
    rejected before any parse attempt (exit 1)."""
    p = tmp_path / "~$смета.doc"
    p.write_bytes(b"\x04user\x00" + b"\x00" * 154)
    with pytest.raises(UnsupportedFormatError, match="Word lock/owner file"):
        detect_format(p)


def test_detect_ole2_is_doc_and_routable(tmp_path: Path) -> None:
    """OLE2 magic routes to DOC and the registry answers (no advertised-
    but-dead window — TKT-OI-05 landing rule)."""
    p = tmp_path / "приложение.doc"
    p.write_bytes(_OLE2_MAGIC + b"\x00" * 64)
    assert detect_format(p) is Format.DOC
    assert callable(route(p))


def test_extension_ignored_ole2_named_pdf(tmp_path: Path) -> None:
    """An OLE2 document renamed to .pdf stays DOC by content (TKT-OI-05)."""
    disguised = tmp_path / "scan.pdf"
    disguised.write_bytes(_OLE2_MAGIC + b"\x00" * 64)
    assert detect_format(disguised) is Format.DOC
    assert callable(route(disguised))


def test_detect_html_masquerade_named_doc(tmp_path: Path) -> None:
    """HTML wearing a .doc extension routes as HTML and never reaches the
    OLE2 branch (TKT-OI-05: the 7 corpus law files)."""
    p = tmp_path / "190-ФЗ.doc"
    p.write_bytes("<!DOCTYPE html>\r\n<html><body><p>Закон</p></body></html>".encode())
    assert detect_format(p) is Format.HTML
    assert callable(route(p))


def test_detect_html_with_bom_and_leading_ws(tmp_path: Path) -> None:
    """BOM/whitespace before ``<html`` still detects (predicate lstrip)."""
    p = tmp_path / "page.html"
    p.write_bytes(b"\xef\xbb\xbf \r\n<html><body>hi</body></html>")
    assert detect_format(p) is Format.HTML


def test_detect_html_prefix_variants(tmp_path: Path) -> None:
    """Both sniffed prefixes work: ``<!doctype html`` and ``<html``."""
    a = tmp_path / "a.doc"
    a.write_bytes(b"<!DOCTYPE HTML>\n<html></html>")
    b = tmp_path / "b.doc"
    b.write_bytes(b"<html><body></body></html>")
    assert detect_format(a) is Format.HTML
    assert detect_format(b) is Format.HTML


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


# ---------------------------------------------------------------------------
# Golden fixtures: the suite is self-sufficient on a clean machine (П6)
# ---------------------------------------------------------------------------


def test_golden_fixtures_committed() -> None:
    """Hardening П6: the two single-page golden PDFs must be committed in
    tests/fixtures so that CI (and any clone) runs the full fast+slow
    matrix without an external corpus. big_test.pdf (21 MB) stays
    external by design — it degrades to a graceful skip elsewhere."""
    fixtures = Path(__file__).parent / "fixtures"
    for rel in ("pdf/p5.pdf", "pdf/p27.pdf", "djvu/plain.djvu", "djvu/text_layer.djvu"):
        p = fixtures / rel
        assert p.exists(), f"golden fixture missing: {p} (suite is not self-sufficient)"
        assert p.stat().st_size > 500, f"golden fixture looks truncated: {p}"
