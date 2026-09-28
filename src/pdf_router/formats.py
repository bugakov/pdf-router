"""Format detection and dispatch — the multi-format seam.

The router's promise to an embedding app: hand the file to the facade, the
facade figures out what it *is* and who parses it. This module owns both
halves:

- :func:`detect_format` sniffs **magic bytes** (never the file extension —
  extensions lie: downloads get renamed, pipelines re-suffix).
- :func:`get_inspector` maps a recognized :class:`Format` to its
  **inspector** — the one callable that turns a document path into an
  :class:`~pdf_router.ir.IR` (:data:`Inspector`).
- :func:`route` composes both: the single gate every entry point funnels
  through, raising :class:`UnsupportedFormatError` *before any page work*.

Supported today: PDF (via the pdf-inspector adapter), DOCX (via the
native stdlib adapter, ADR-0003), DJVU (via the native djvulibre
subprocess adapter, ADR-0004), XLSX (via the native stdlib adapter,
ADR-0008), legacy DOC (via office-oxide, ADR-0008) and HTML-masquerade
files (minimal stdlib tag-strip, ADR-0008). Unrecognized content raises
:class:`UnsupportedFormatError` before any page work, so callers branch
on a typed exception instead of parsing strings. Adding a format = one
enum member + one magic-byte branch + one registry entry; nothing
downstream changes.

NB: the *sidecar image extraction* and OCR passes in core.py are still
format-aware (pymupdf pages vs docx media parts vs djvu no-op); the
dispatch on ``Metadata.source_format`` lives there, not here.
"""

from __future__ import annotations

import enum
import zipfile
from pathlib import Path
from typing import Callable

from pdf_router.ir import IR


class UnsupportedFormatError(Exception):
    """The input document cannot be routed — detected at the API boundary.

    Two distinct situations, one type (callers need a single ``except``;
    the message tells them apart):

    - recognized but no inspector registered: ``DOCX detected, but the
      router does not support it yet``;
    - not recognizable at all: ``unrecognized file format`` (with a hint
      — empty file, non-OOXML zip, first bytes).
    """


class Format(enum.Enum):
    """Document formats the router can *recognize* (not all routable yet)."""

    PDF = "pdf"
    DOCX = "docx"
    DJVU = "djvu"
    XLSX = "xlsx"
    DOC = "doc"
    HTML = "html"

#: The inspector contract: one callable per format — document path in,
#: fully-populated ``IR`` out. The pdf-inspector adapter's module-level
#: ``extract_ir`` conforms; a docx inspector plugs in the same way.
#: Flat functions by design (RT-14 deleted the adapter classes).
Inspector = Callable[[str], IR]


def _pdf_inspector(path: str) -> IR:
    # Lazy: the Rust pdf_inspector import must stay out of package import
    # (tests/test_api.py::test_public_import_pulls_no_heavy_deps).
    from pdf_router.adapters.pdf_inspector import extract_ir

    return extract_ir(path)


def _docx_inspector(path: str) -> IR:
    # Native stdlib parser (ADR-0003) — no heavy deps, but keep the lazy
    # shape symmetric with the PDF inspector anyway.
    from pdf_router.adapters.docx import extract_ir

    return extract_ir(path)


def _djvu_inspector(path: str) -> IR:
    # djvulibre subprocess adapter (ADR-0004) — lazy like the others.
    from pdf_router.adapters.djvu import extract_ir

    return extract_ir(path)


def _xlsx_inspector(path: str) -> IR:
    # Native stdlib parser (ADR-0008) — lazy like the others.
    from pdf_router.adapters.xlsx import extract_ir

    return extract_ir(path)


def _doc_inspector(path: str) -> IR:
    # office-oxide binding (ADR-0008, pinned core dep) — lazy per the
    # heavy-deps rule: the native module loads only when a .doc does.
    from pdf_router.adapters.doc import extract_ir

    return extract_ir(path)


def _html_inspector(path: str) -> IR:
    # Minimal stdlib tag-strip (ADR-0008) — lazy like the others.
    from pdf_router.adapters.html import extract_ir

    return extract_ir(path)


#: Format -> inspector registry. The dispatcher's single source of truth:
#: core.py never imports an adapter directly.
_INSPECTORS: dict[Format, Inspector] = {
    Format.PDF: _pdf_inspector,
    Format.DOCX: _docx_inspector,
    Format.DJVU: _djvu_inspector,
    Format.XLSX: _xlsx_inspector,
    Format.DOC: _doc_inspector,
    Format.HTML: _html_inspector,
}

# ZIP local/empty/spanned headers — any of them means "some zip package".
_ZIP_MAGICS = (b"PK\x03\x04", b"PK\x05\x06", b"PK\x07\x08")
# Legacy Word OLE2/CFB container magic (TKT-OI-05 branch 5).
_OLE2_MAGIC = b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1"
# HTML sniff: BOM/leading-WS-tolerant prefix check (TKT-OI-05 branch 6).
_HTML_PREFIXES = (b"<!doctype html", b"<html")
_BOM_WS = b"\xef\xbb\xbf \t\r\n"
# How much of the file head the sniff reads. %PDF- at offset 0 is the spec;
# real-world files sometimes carry leading junk, which pdfium tolerates too.
_SNIFF_WINDOW = 1024


def _sniff_zip(path: Path) -> Format:
    """Distinguish docx/xlsx from other ZIP packages (pptx/epub/jar...)."""
    try:
        with zipfile.ZipFile(path) as zf:
            names = set(zf.namelist())
    except zipfile.BadZipFile as exc:
        raise UnsupportedFormatError(f"corrupt zip archive: {path} ({exc})") from exc
    if "word/document.xml" in names:
        return Format.DOCX
    if "xl/workbook.xml" in names:
        return Format.XLSX
    if "ppt/presentation.xml" in names:
        raise UnsupportedFormatError(f"PowerPoint pptx is not routed (out of scope): {path}")
    if "[Content_Types].xml" in names:
        raise UnsupportedFormatError(
            f"OOXML package that is not docx/xlsx (pptx?): {path}"
        )
    raise UnsupportedFormatError(f"zip archive, not a routed document format: {path}")


def detect_format(path: str | Path) -> Format:
    """Sniff the document format from magic bytes (never the extension).

    Reads at most the first 1 KiB (plus the name check for ``~$`` locks —
    the one deliberate name-based predicate: a 162-byte lock file's content
    is not reliably detectable, TKT-OI-05 branch 0).

    Raises:
        FileNotFoundError: the path does not exist (propagated from open).
        UnsupportedFormatError: empty file, unrecognized content, a Word
            lock file, or a ZIP package that is not a docx/xlsx (the
            message carries a hint).
    """
    p = Path(path)
    if p.name.startswith("~$"):
        raise UnsupportedFormatError(f"Word lock/owner file (~$…), not a document: {p}")
    with p.open("rb") as fh:
        head = fh.read(_SNIFF_WINDOW)
    if not head:
        raise UnsupportedFormatError(f"empty file: {p}")
    if b"%PDF-" in head:
        return Format.PDF
    if head.startswith(b"AT&TFORM"):
        return Format.DJVU
    if head.startswith(_ZIP_MAGICS):
        return _sniff_zip(p)
    if head.startswith(_OLE2_MAGIC):
        return Format.DOC
    if head.lstrip(_BOM_WS).lower().startswith(_HTML_PREFIXES):
        return Format.HTML
    raise UnsupportedFormatError(
        f"unrecognized file format: {p} (starts with {head[:8].hex(' ')})"
    )


def get_inspector(fmt: Format) -> Inspector:
    """Return the registered inspector for a recognized format.

    Raises:
        UnsupportedFormatError: the format is recognized but has no
            inspector yet — with a forward-looking message.
    """
    try:
        return _INSPECTORS[fmt]
    except KeyError:
        supported = sorted(f.name for f in _INSPECTORS)
        raise UnsupportedFormatError(
            f"{fmt.name} detected, but the router does not support it yet "
            f"(supported: {supported})"
        ) from None


def route(path: str | Path) -> Inspector:
    """Detect the format and return its inspector — the API-boundary gate.

    This is what every entry point funnels through (facade, CLI, core).
    Raises :class:`UnsupportedFormatError` before any page work for
    unrecognized content and for recognized-but-unsupported formats.
    """
    return get_inspector(detect_format(path))
