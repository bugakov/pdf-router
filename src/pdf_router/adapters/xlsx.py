"""Native xlsx inspector — stdlib only (ADR-0008).

xlsx is a zip of XML: the structure a PDF pipeline would guess (which
cells belong together, where a table starts) is stated explicitly here —
the sheet dimension, the ``<c r="A10">`` references, the ``<mergeCell>``
ranges, the ``hidden`` col/row flags. Zero dependencies beyond the
standard library (zipfile + xml.etree): microseconds for small sheets,
measured 3.5–4.7 s warm for the 6.7 MB ЛСР workbook (10 sheets, 878
tables).

Page semantics (TKT-OI-03): a sheet is a page — ``page_count`` is the
sheet count, ``page`` the 0-based sheet index, and every page's markdown
opens with ``## <sheet name>`` (the only section signal a workbook has:
``docProps`` title is empty in 0/89 corpus files).

Sheet -> blocks = alternating runs: a row is *dense* when at least half
of the visible columns carry content; dense runs become ``TableBlock``s,
sparse runs become prose paragraphs. Empty rows do not break a run.

Merged cells: the vertical span repeats its value down every spanned row
(every row self-contained for retrieval — the docx ``vMerge`` invariant);
the horizontal span keeps the value in the first cell only (a caption
repeated across 16 columns is retrieval poison).

Honest consequences (accepted in ADR-0008, measured on the corpus):
- hidden columns and rows are **cut**: grid width is the last visible
  column with content (17.9% of corpus content cells live beyond it —
  the ЛСР "staircase" duplicate region dies with it);
- formulas publish the **cached** ``<v>`` value only — no calculation;
- no ``numFmt`` engine, no date conversion (0 Excel date serials in the
  corpus — dates are prose): numbers are ``float`` -> ``repr``, which
  kills float noise (``4.8000000000000001E-2`` -> ``0.048``);
- media/charts: none in the corpus (0/89) — no sidecar work in v1;
- a workbook with a broken rels target or unreadable sheet XML is a
  document-level failure (``ValueError`` -> exit 3), not a rejection.
"""

from __future__ import annotations

import time
import zipfile

from pdf_router.adapters.ooxml import core_title, load_rels, read_xml, rows_to_markdown
from pdf_router.ir import (
    FigureBlock,
    FormulaBlock,
    HeadingBlock,
    IR,
    Metadata,
    PageResult,
    SrcTool,
    TableBlock,
)

_S = "http://schemas.openxmlformats.org/spreadsheetml/2006/main"
_R = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"

_TAG_ROW = f"{{{_S}}}row"
_TAG_C = f"{{{_S}}}c"
_TAG_V = f"{{{_S}}}v"
_TAG_IS = f"{{{_S}}}is"
_TAG_T = f"{{{_S}}}t"
_TAG_SHEET = f"{{{_S}}}sheet"
_TAG_SHEET_DATA = f"{{{_S}}}sheetData"
_TAG_COLS = f"{{{_S}}}cols"
_TAG_COL = f"{{{_S}}}col"
_TAG_MERGE_CELLS = f"{{{_S}}}mergeCells"
_TAG_MERGE_CELL = f"{{{_S}}}mergeCell"

_DIGITS = "0123456789"
#: header_rows heuristic (TKT-OI-03 #7): a header cell is short text
#: without digits; longer than this and the row is prose, not a header.
_HEADER_CELL_MAX = 60


# ---------------------------------------------------------------------------
# Package plumbing
# ---------------------------------------------------------------------------


def _split_ref(ref: str) -> tuple[int, int] | None:
    """``"BC12"`` -> (row, col); None when the ref is malformed.

    ``rstrip`` beats a regex here — this runs once per cell (116k times on
    the biggest corpus workbook) and the letters/digits split is exact:
    Excel refs are ``A``-``Z`` letters followed by decimal digits.
    """
    letters = ref.rstrip(_DIGITS)
    if not letters or len(letters) == len(ref):
        return None
    col = 0
    for ch in letters:
        col = col * 26 + (ord(ch) - 64)
    return int(ref[len(letters) :]), col


def _load_shared_strings(zf: zipfile.ZipFile) -> list[str]:
    """``xl/sharedStrings.xml`` -> list of strings (rich text concatenated)."""
    root = read_xml(zf, "xl/sharedStrings.xml")
    if root is None:
        return []
    return ["".join(t.text or "" for t in si.iter(_TAG_T)) for si in root.iter(f"{{{_S}}}si")]


def _sheets(root, rels: dict[str, str]) -> list[tuple[str, str]]:
    """(sheet name, worksheet part) pairs in workbook order."""
    out: list[tuple[str, str]] = []
    for i, sh in enumerate(root.iter(_TAG_SHEET), start=1):
        name = sh.get("name") or f"Sheet{i}"
        rid = sh.get(f"{{{_R}}}id")
        part = rels.get(rid or "")
        out.append((name, part or ""))
    return out


def _cell_value(c, shared: list[str]) -> str:
    """One ``<c>`` element -> display text (TKT-OI-03 #8)."""
    t = c.get("t", "")
    if t == "inlineStr":
        is_el = c.find(_TAG_IS)
        if is_el is None:
            return ""
        return "".join(x.text or "" for x in is_el.iter(_TAG_T))
    v = c.find(_TAG_V)
    if v is None or v.text is None:
        return ""  # formula without cached value, blank cell
    text = v.text
    if t == "s":
        try:
            return shared[int(text)]
        except (ValueError, IndexError):
            return ""
    if t in ("str", "e", "b"):
        return text  # cached formula string / error / boolean — as stored
    try:
        f = float(text)
    except ValueError:
        return text
    return str(int(f)) if f.is_integer() else repr(f)


# ---------------------------------------------------------------------------
# Sheet geometry: visible cells, merges -> rectangular grid
# ---------------------------------------------------------------------------


def _hidden_cols(root) -> set[int]:
    """Column indices flagged ``hidden`` (spans expanded)."""
    out: set[int] = set()
    cols = root.find(_TAG_COLS)
    if cols is None:
        return out
    for col in cols.findall(_TAG_COL):
        if (col.get("hidden") or "") not in ("1", "true"):
            continue
        try:
            lo, hi = int(col.get("min", "0")), int(col.get("max", "0"))
        except ValueError:
            continue
        if 1 <= lo <= hi:
            out.update(range(lo, hi + 1))
    return out


def _hidden_rows(root) -> set[int]:
    """Row numbers flagged ``hidden``."""
    out: set[int] = set()
    data = root.find(_TAG_SHEET_DATA)
    if data is None:
        return out
    for row in data.findall(_TAG_ROW):
        if (row.get("hidden") or "") not in ("1", "true"):
            continue
        r = row.get("r", "")
        if r.isdigit():
            out.add(int(r))
    return out


def _visible_cells(root, shared: list[str], hidden_cols: set[int], hidden_rows: set[int]) -> dict:
    """``(row, col) -> text`` for every non-blank visible cell.

    One pass over all ``<c>`` elements (the row numbers live in the cell
    refs, so the row loop buys nothing) — this is the hot loop of the
    adapter: ~116k cells on the biggest corpus workbook.
    """
    cells: dict[tuple[int, int], str] = {}
    data = root.find(_TAG_SHEET_DATA)
    if data is None:
        return cells
    for c in data.iter(_TAG_C):
        ref = c.get("r") or ""
        pos = _split_ref(ref)
        if pos is None:
            continue
        r, col = pos
        if col in hidden_cols or r in hidden_rows:
            continue
        val = _cell_value(c, shared)
        if "\n" in val or "\t" in val:
            val = val.replace("\n", " ").replace("\t", " ")
        if not val.strip():
            continue  # whitespace-only cells keep the grid rectangular as ""
        cells[(r, col)] = val
    return cells


def _merges(root) -> list[tuple[int, int, int, int]]:
    """``<mergeCell ref="A13:P13">`` ranges as (r1, c1, r2, c2)."""
    out: list[tuple[int, int, int, int]] = []
    mc = root.find(_TAG_MERGE_CELLS)
    if mc is None:
        return out
    for m in mc.findall(_TAG_MERGE_CELL):
        parts = (m.get("ref") or "").split(":")
        if len(parts) != 2:
            continue
        a, b = _split_ref(parts[0]), _split_ref(parts[1])
        if a is None or b is None:
            continue
        r1, c1 = a
        r2, c2 = b
        out.append((min(r1, r2), min(c1, c2), max(r1, r2), max(c1, c2)))
    return out


def _apply_merges(
    cells: dict[tuple[int, int], str],
    merges: list[tuple[int, int, int, int]],
    width: int,
    hidden_cols: set[int],
    hidden_rows: set[int],
) -> None:
    """Vertical span: value repeated down. Horizontal span: first cell only.

    Applied after ``width`` is known (a horizontal follower beyond the
    last visible content column is cut with the rest of the region);
    merge rows materialize even when they hold no other cell.
    """
    for r1, c1, r2, c2 in merges:
        if c1 > width or c1 in hidden_cols or r1 in hidden_rows:
            continue
        val = cells.get((r1, c1), "")
        for r in range(r1, r2 + 1):
            if r in hidden_rows:
                continue
            cells[(r, c1)] = val
            for c in range(c1 + 1, min(c2, width) + 1):
                if c in hidden_cols:
                    continue
                cells[(r, c)] = ""


def _grid(cells: dict[tuple[int, int], str], width: int) -> list[list[str]]:
    """Rectangular rows (row order, gaps and trailing empties trimmed)."""
    out: list[list[str]] = []
    for r in sorted({r for r, _ in cells}):
        row = [cells.get((r, c), "") for c in range(1, width + 1)]
        if any(row):
            out.append(row)
    return out


def _is_dense(row: list[str], visible_cols: int) -> bool:
    """Dense = at least half the visible columns carry content."""
    if visible_cols <= 0:
        return False
    content = sum(1 for c in row if c.strip())
    return content * 2 >= visible_cols


def _runs(rows: list[list[str]], visible_cols: int) -> list[tuple[bool, list[list[str]]]]:
    """Split rows into maximal dense/sparse runs (empty rows already gone)."""
    runs: list[tuple[bool, list[list[str]]]] = []
    for row in rows:
        dense = _is_dense(row, visible_cols)
        if runs and runs[-1][0] == dense:
            runs[-1][1].append(row)
        else:
            runs.append((dense, [row]))
    return runs


def _header_rows(rows: list[list[str]]) -> int:
    """Leading rows where every cell is short text without digits (TKT-OI-03 #7).

    Never the whole table: with ``len(rows) - 1`` at most, so a dense run
    of short text keeps at least one body row for the chunks renderer.
    """
    n = 0
    for row in rows:
        cells = [c.strip() for c in row if c.strip()]
        if not cells:
            break
        if not all(len(c) <= _HEADER_CELL_MAX and not any(ch.isdigit() for ch in c) for c in cells):
            break
        n += 1
    return min(n, max(0, len(rows) - 1))


# ---------------------------------------------------------------------------
# The inspector: path -> IR
# ---------------------------------------------------------------------------


def _sheet_page(index: int, name: str, root, shared: list[str]) -> PageResult:
    """One sheet -> one PageResult (``## name`` + prose/table runs)."""
    md_parts: list[str] = [f"## {name}"]
    blocks: list[HeadingBlock | TableBlock | FigureBlock | FormulaBlock] = [
        HeadingBlock(level=2, text=name, bbox=None, source="xlsx_sheet")
    ]
    hidden_cols = _hidden_cols(root)
    hidden_rows = _hidden_rows(root)
    cells = _visible_cells(root, shared, hidden_cols, hidden_rows)
    width = max((c for _, c in cells), default=0)
    _apply_merges(cells, _merges(root), width, hidden_cols, hidden_rows)
    visible_cols = width - sum(1 for c in hidden_cols if c <= width)
    for dense, rows in _runs(_grid(cells, width), visible_cols):
        if dense:
            table = TableBlock(
                markdown=rows_to_markdown(rows),
                rows=rows,
                header_rows=_header_rows(rows),
                bbox=None,
            )
            blocks.append(table)
            md_parts.append(table.markdown)
        else:
            for row in rows:
                text = " ".join(c.strip() for c in row if c.strip())
                if text:
                    md_parts.append(text)
    return PageResult(
        page=index,
        markdown="\n\n".join(md_parts).strip(),
        needs_ocr=False,
        src_tool=SrcTool.XLSX_ADAPTER,
        blocks=blocks,
        images=[],
    )


def extract_ir(path: str) -> IR:
    """Native xlsx -> IR (one page per sheet; ADR-0008).

    Pure: reads the package, writes nothing.

    Raises:
        ValueError: the package is a zip with ``xl/workbook.xml`` but the
            workbook or a worksheet inside is unreadable — a document-level
            failure (exit 3), not a format rejection.
    """
    t0 = time.perf_counter()
    try:
        zf = zipfile.ZipFile(path)
    except zipfile.BadZipFile as exc:
        raise ValueError(f"malformed xlsx (bad zip): {path}: {exc}") from exc
    with zf:
        wb_root = read_xml(zf, "xl/workbook.xml")
        if wb_root is None:
            raise ValueError(f"malformed xlsx (unreadable xl/workbook.xml): {path}")
        rels = load_rels(zf, "xl/workbook.xml")
        shared = _load_shared_strings(zf)
        pages: list[PageResult] = []
        pages_with_tables: list[int] = []
        for index, (name, part) in enumerate(_sheets(wb_root, rels)):
            root = read_xml(zf, part)
            if root is None:
                raise ValueError(f"malformed xlsx (unreadable {part}): {path}")
            page = _sheet_page(index, name, root, shared)
            pages.append(page)
            if any(isinstance(b, TableBlock) for b in page.blocks):
                pages_with_tables.append(index + 1)
        title = core_title(zf)

    metadata = Metadata(
        pdf_type="text_based",
        page_count=len(pages),
        confidence=1.0,
        processing_time_ms=round((time.perf_counter() - t0) * 1000.0, 1),
        title=title,
        is_complex_layout=False,
        pages_with_tables=pages_with_tables,
        source_format="xlsx",
    )
    return IR(metadata=metadata, pages=pages)


# ---------------------------------------------------------------------------
# --info payload (one door: PDF classifies, xlsx inspects — TKT-OI-05)
# ---------------------------------------------------------------------------


def xlsx_info(path: str) -> dict:
    """Metadata summary for ``--info`` — derived from the same inspector."""
    ir = extract_ir(path)
    return {
        "source_format": ir.metadata.source_format,
        "pdf_type": ir.metadata.pdf_type,
        "page_count": ir.metadata.page_count,
        "confidence": ir.metadata.confidence,
        "title": ir.metadata.title,
        "tables": sum(isinstance(b, TableBlock) for p in ir.pages for b in p.blocks),
        "images": sum(isinstance(b, FigureBlock) for p in ir.pages for b in p.blocks),
    }
