"""pdf-inspector text-path adapter — the only always-loaded adapter.

One ``process_pdf`` call gives detection + extraction + concatenated Markdown
(single Rust pass). ``PdfResult`` carries no per-page Markdown, so a second
cheap call ``extract_pages_markdown`` supplies the per-page ``pages[]`` array.
The adapter itself never runs OCR; the orchestrator (core.py) decides routing.

Structural blocks (IR 0.2):
- ``HeadingBlock`` — recovered from ``extract_structure_elements`` (tagged PDF)
  with mcid→text join through ``extract_text_with_positions``. Fallback for
  non-tagged PDF: font-size outlier detection (no bbox, ``source =
  "font_size_fallback"``).
- ``TableBlock`` — markdown-pipe-tables parsed from per-page ``PageMarkdown``.
  No region-routing bbox (we don't ask pdf-inspector for region boundaries —
  the input bbox is what we *gave* it, and we'd need a structural-detector
  pass to find the right region, which is not in 1.17 Python bindings).
- ``FigureBlock`` / ``FormulaBlock`` — always empty lists in 0.2 (no first-class
  upstream API in pdf-inspector 1.17 Python bindings; reserved for future).
"""

from __future__ import annotations

import logging
import os
import re
from collections import Counter
from pathlib import Path
from typing import Any, Iterable

from pdf_router.ir import (
    BBox,
    HeadingBlock,
    IR,
    Metadata,
    OcrReasonEntry,
    PageResult,
    SrcTool,
    TableBlock,
)

logger = logging.getLogger("pdf_router")

# Match a markdown table row: | ... | ... |
_TABLE_ROW_RE = re.compile(r"^\s*\|.*\|\s*$")
# A header separator row: |---|:---:|---:|
_TABLE_SEP_RE = re.compile(r"^\s*\|?\s*:?-+:?\s*(\|\s*:?-+:?\s*)+\|?\s*$")
# Heading level from a structure-tree role string, e.g. "H2" -> 2.
_H_ROLE_RE = re.compile(r"^H([1-6])$")


def _parse_markdown_table(md: str) -> tuple[list[list[str]], int]:
    """Parse a markdown-pipe-table block into (rows, header_rows).

    Returns ``([], 0)`` if ``md`` is not a table. ``header_rows`` is the number
    of leading rows above the separator; for our corpus that's usually 1.

    Heuristic for ``header_rows == 2``: markdown allows only one header row,
    so a two-line header surfaces its second line as the first *body* row.
    When that row's every cell is short and digit-free, it is counted as the
    header's continuation (it stays in ``rows`` in order — the count only
    drives the header band in RAG chunk embeds). Conservative: fires only
    when at least two candidate rows follow the separator.
    """
    lines = [ln for ln in md.splitlines() if ln.strip()]
    if (
        len(lines) < 2
        or not _TABLE_ROW_RE.match(lines[0])
        or not _TABLE_SEP_RE.match(lines[1])
    ):
        return [], 0
    # Header + data rows in original order; the separator itself and any
    # stray separator-like lines are not rows.
    row_lines = [
        lines[0],
        *(
            ln
            for ln in lines[2:]
            if _TABLE_ROW_RE.match(ln) and not _TABLE_SEP_RE.match(ln)
        ),
    ]
    header_rows = 1
    if len(row_lines) >= 3:
        second_cells = [c.strip() for c in row_lines[1].strip("|").split("|")]
        if all(c and not any(ch.isdigit() for ch in c) and len(c) < 40 for c in second_cells):
            header_rows = 2  # wrapped second header line (heuristic)
    rows = [[c.strip() for c in ln.strip().strip("|").split("|")] for ln in row_lines]
    return rows, header_rows


def _extract_tables_from_markdown(page_md: str) -> list[TableBlock]:
    """Find consecutive markdown-pipe-table blocks in a page's markdown."""
    blocks: list[TableBlock] = []
    lines = page_md.splitlines()
    i = 0
    n = len(lines)
    while i < n:
        if _TABLE_ROW_RE.match(lines[i] or "") and i + 1 < n and _TABLE_SEP_RE.match(lines[i + 1] or ""):
            j = i + 2
            while j < n and _TABLE_ROW_RE.match(lines[j] or ""):
                j += 1
            md = "\n".join(lines[i:j])
            rows, hdr = _parse_markdown_table(md)
            blocks.append(TableBlock(markdown=md, rows=rows, header_rows=hdr, bbox=None))
            i = j
        else:
            i += 1
    return blocks


def _mode(values: Iterable[float]) -> float:
    """Return the most common value (bin to 0.5pt to avoid float noise)."""
    c = Counter(round(v * 2) / 2 for v in values)
    return c.most_common(1)[0][0] if c else 0.0


def _headings_from_textitems(text_items: list[Any], page_0indexed: int) -> list[HeadingBlock]:
    """Detect headings by font-size outlier (1.3x body mode), per page.

    No bbox per-heading here — these come from font-size alone. We keep the
    *full-line* text by concatenating items on the same line (same y, same
    font_size) within the page.
    """
    if not text_items:
        return []
    page_items = [it for it in text_items if it.page - 1 == page_0indexed]
    if not page_items:
        return []
    body = _mode(it.font_size for it in page_items)
    if body <= 0:
        return []
    threshold = body * 1.3

    # Group by (font_size, y) so we don't merge items on different visual lines.
    by_line: dict[tuple[float, float], list[Any]] = {}
    for it in page_items:
        if it.font_size >= threshold and it.text and it.text.strip():
            key = (round(it.font_size, 2), round(it.y, 1))
            by_line.setdefault(key, []).append(it)
    out: list[HeadingBlock] = []
    # sort by y descending (PDF y goes up)
    for (sz, _y), items in sorted(by_line.items(), key=lambda kv: -kv[0][1]):
        items.sort(key=lambda it: it.x)
        text = " ".join(it.text.strip() for it in items).strip()
        if not text:
            continue
        level = 1 if sz >= body * 1.8 else (2 if sz >= body * 1.5 else 3)
        x0 = min(it.x for it in items)
        x1 = max(it.x + it.width for it in items)
        y0 = min(it.y for it in items)
        y1 = max(it.y + it.height for it in items)
        out.append(
            HeadingBlock(
                level=level,
                text=text,
                bbox=BBox(x0=x0, y0=y0, x1=x1, y1=y1),
                source="font_size_fallback",
            )
        )
    return out


def _headings_from_structure_tree(
    structure_elements: list[Any],
    text_items_by_mcid: dict[tuple[int, int], list[Any]],
) -> dict[int, list[HeadingBlock]]:
    """Recover H1..H6 blocks from structure-tree + text items joined by (page, mcid).

    Returns a mapping of 0-indexed page → list of HeadingBlock.
    """
    by_page: dict[int, list[HeadingBlock]] = {}
    for el in structure_elements:
        m = _H_ROLE_RE.match(el.role or "")
        if not m:
            continue
        level = int(m.group(1))
        # mcid may be None; skip if so (cannot join text)
        if el.mcid is None:
            continue
        items = text_items_by_mcid.get((el.page, el.mcid), [])
        if not items:
            continue
        items.sort(key=lambda it: it.x)
        text = " ".join(it.text.strip() for it in items if it.text).strip()
        if not text:
            continue
        x0 = min(it.x for it in items)
        x1 = max(it.x + it.width for it in items)
        y0 = min(it.y for it in items)
        y1 = max(it.y + it.height for it in items)
        by_page.setdefault(el.page - 1, []).append(
            HeadingBlock(
                level=level,
                text=text,
                bbox=BBox(x0=x0, y0=y0, x1=x1, y1=y1),
                source="structure_tree",
            )
        )
    return by_page


# ---------------------------------------------------------------------------
# Top-level API (v0.3: replaces AdapterPdfInspector().process())
# ---------------------------------------------------------------------------


def _extract_ir_pymupdf_fallback(
    path: str,
    result: Any = None,
    exc: Exception | None = None,
) -> IR:
    """Fallback when pdf_inspector Rust calls fail (corrupt xref, invalid structure).

    Opens the PDF via PyMuPDF (pymupdf), which is very forgiving with broken streams.
    Marks pages as needing OCR if they have little or no digital text.
    """
    import pymupdf

    doc = pymupdf.open(path)
    try:
        page_count = len(doc)
        pages_needing_ocr: list[int] = (
            list(result.pages_needing_ocr)
            if result and hasattr(result, "pages_needing_ocr") and result.pages_needing_ocr
            else []
        )
        pdf_type = getattr(result, "pdf_type", None) or "scanned"

        reasons_by_page: dict[int, Any] = {}
        if result and getattr(result, "ocr_reasons_by_page", None):
            reasons_by_page = {int(e.page): e for e in result.ocr_reasons_by_page}

        pages: list[PageResult] = []
        for i, pm_page in enumerate(doc):
            one_indexed = i + 1
            txt = pm_page.get_text() or ""
            is_scan = (len(txt.strip()) < 50) or (one_indexed in pages_needing_ocr)
            if is_scan and one_indexed not in pages_needing_ocr:
                pages_needing_ocr.append(one_indexed)

            reason_entry = reasons_by_page.get(one_indexed)
            ocr_reason = (
                ", ".join(reason_entry.reasons)
                if reason_entry and reason_entry.reasons
                else ("scanned_page" if is_scan else None)
            )

            pages.append(
                PageResult(
                    page=i,
                    markdown=txt if not is_scan else "",
                    needs_ocr=is_scan,
                    ocr_reason=ocr_reason,
                    src_tool=SrcTool.PDF_INSPECTOR,
                    blocks=[],
                )
            )

        metadata = Metadata(
            pdf_type=pdf_type if pdf_type != "none" else ("scanned" if len(pages_needing_ocr) == page_count else "image_based"),
            page_count=page_count,
            title=getattr(result, "title", None) or doc.metadata.get("title") or None,
            pages_needing_ocr=pages_needing_ocr,
        )
        return IR(metadata=metadata, pages=pages)
    finally:
        doc.close()


def extract_ir(path: str | Path) -> IR:
    """Extract full IR from a PDF via pdf-inspector.

    ``path`` accepts str or Path (coerced here — the Rust pyo3 boundary
    requires str and raises an opaque TypeError otherwise).

    Two Rust calls:
    - ``process_pdf(path)`` — document-level metadata.
    - ``extract_pages_markdown(path)`` — per-page markdown + OCR flags.

    Plus two optional structural passes:
    - ``extract_structure_elements(path)`` — tagged PDF structure tree.
    - ``extract_text_with_positions(path)`` — per-glyph position data.

    Returns an ``IR`` with fully-populated ``Metadata`` and a ``PageResult`` list.
    """
    import pdf_inspector

    path = os.fspath(path)
    try:
        result = pdf_inspector.process_pdf(path)
    except Exception as exc:
        logger.warning("pdf_inspector.process_pdf failed (%s), falling back to PyMuPDF: %s", exc, path)
        return _extract_ir_pymupdf_fallback(path, result=None, exc=exc)

    try:
        pages_result = pdf_inspector.extract_pages_markdown(path)
    except Exception as exc:
        logger.warning("pdf_inspector.extract_pages_markdown failed (%s), falling back to PyMuPDF: %s", exc, path)
        return _extract_ir_pymupdf_fallback(path, result=result, exc=exc)

    metadata = Metadata(
        pdf_type=result.pdf_type,
        page_count=result.page_count,
        confidence=result.confidence,
        processing_time_ms=result.processing_time_ms,
        title=result.title,
        is_complex_layout=result.is_complex_layout,
        has_encoding_issues=result.has_encoding_issues,
        pages_with_tables=list(result.pages_with_tables),
        pages_with_columns=list(result.pages_with_columns),
        pages_needing_ocr=list(result.pages_needing_ocr),
        ocr_reasons_by_page=[
            OcrReasonEntry(page=int(e.page), reasons=list(e.reasons))
            for e in getattr(result, "ocr_reasons_by_page", []) or []
        ],
    )

    reasons_by_page = {e.page: e for e in metadata.ocr_reasons_by_page}

    structure_elements: list[Any] = []
    try:
        structure_elements = pdf_inspector.extract_structure_elements(path)
    except Exception as exc:
        # Headings degrade to the font-size fallback; the text path is
        # unaffected. Must stay visible: a silently missing structure tree
        # looks identical to a PDF without one.
        logger.warning("structure-tree pass failed (headings degraded): %s", exc)

    text_items: list[Any] = []
    try:
        text_items = pdf_inspector.extract_text_with_positions(path)
    except Exception as exc:
        # Kills both heading sources (structure join + font-size fallback).
        logger.warning("text-positions pass failed (headings degraded): %s", exc)

    text_items_by_mcid: dict[tuple[int, int], list[Any]] = {}
    for it in text_items:
        if it.mcid is not None:
            text_items_by_mcid.setdefault((it.page, it.mcid), []).append(it)

    headings_by_page_struct = _headings_from_structure_tree(structure_elements, text_items_by_mcid)

    pages: list[PageResult] = []
    for pm in pages_result.pages:
        one_indexed = pm.page + 1
        # Флаг «нужно распознавание» приходит ДВУМЯ источниками, и они
        # расходятся: `process_pdf` даёт список уровня документа, а
        # `extract_pages_markdown` — постраничный. Для битой кодировки
        # список документа пуст, а постраничные флаги есть: на
        # sources/4589.pdf это 11 страниц из 18 с пустым текстом.
        # Взять только список документа — значит объявить их прочитанными:
        # `looks_like_text("")` в core.py возвращает True по умыслу, и
        # текст теряется молча (тикет text-quality/02). Поэтому берём оба.
        needs_ocr = bool(pm.needs_ocr) or one_indexed in metadata.pages_needing_ocr
        reason_entry = reasons_by_page.get(one_indexed)
        blocks: list[Any] = []
        blocks.extend(_extract_tables_from_markdown(pm.markdown or ""))
        struct_h = headings_by_page_struct.get(pm.page, [])
        if struct_h:
            blocks.extend(struct_h)
        else:
            blocks.extend(_headings_from_textitems(text_items, pm.page))
        blocks.extend([])
        pages.append(
            PageResult(
                page=pm.page,
                markdown=pm.markdown or "",
                needs_ocr=needs_ocr,
                ocr_reason=(
                    ", ".join(reason_entry.reasons)
                    if reason_entry and reason_entry.reasons
                    else (pm.ocr_reason if hasattr(pm, "ocr_reason") else None)
                ),
                src_tool=SrcTool.PDF_INSPECTOR,
                blocks=blocks,
            )
        )
    return IR(metadata=metadata, pages=pages)


# ---------------------------------------------------------------------------
# Compat shim removed in v0.3 (RT-14). Use ``extract_ir()`` directly.
# ---------------------------------------------------------------------------
