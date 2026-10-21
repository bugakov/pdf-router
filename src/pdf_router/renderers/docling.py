"""Docling renderer: IR -> DoclingDocument JSON (docling-core interchange).

Docling's published document format is a ``DoclingDocument`` — a typed tree
of items (text, section headers, lists, tables, pictures, formulas) with
page provenance. It is the closest sibling of our own IR: an *interchange*
container, not an in-process contract. ADR-0002 already fixed the layering
rule ("a published format is a renderer, never the IR"); this module is
that rule applied to Docling.

Contract decisions:

- **The markdown is the spine, the blocks enrich it.** ``PageResult.markdown``
  stays the authoritative text (IR doctrine, ADR-0001): a walk over the
  markdown produces the item order, and ``HeadingBlock`` / ``TableBlock``
  upgrade the matching region (true heading level, exact table grid, bbox).
  Blocks with no markdown counterpart (OCR headings, figures, formulas) are
  appended after the markdown items, in reading order.
- **OCR pages: the blocks win.** On a page whose blocks come from the OCR
  structure pass the markdown pipe tables are the *scrambled twin* of the
  ``TableBlock``s (ADR-0005) — such regions are dropped instead of being
  re-parsed, and the block's grid is emitted instead.
- **Provenance only when the page frame is known.** A ``ProvenanceItem``
  needs a bbox, and docling clamps every bbox to the page size: on a page
  whose size is unknown (every page today — ``page_bbox`` is opt-in and no
  adapter fills it) the geometry would silently collapse to zero. So
  provenance is emitted only when the page size is known *and* the block
  carries a bbox. OCR bboxes are raster pixels, not page points — they
  never enter a ``ProvenanceItem``.
- **No file reads.** The renderer is a pure function (DESIGN §7): the
  document's ``origin`` is left unset (docling requires a sha256 of the
  *source binary* there, and only the pipeline — not a renderer — may read
  the input file) and the sidecar image paths are copied from
  ``PageResult.images`` as-is (relative to the output directory).

Dependency: ``docling-core`` is an **optional extra**
(``pip install 'pdf-router[docling]'``) — it drags in pandas/pillow/typer/
doclang, which the "ultra-minimal" core must not carry (same argument as
ADR-0002 §5 for DocLang's lxml/typer stack). The import is lazy, so
``import pdf_router`` stays cheap and a missing extra raises
:class:`DoclingUnavailableError` with the install line. :func:`ensure_available`
exposes that check for the CLI, which runs it *before* the pipeline: a
missing extra is a usage error, and the user must not pay for a full OCR
run to learn about it.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from pdf_router.config import RendererConfig
from pdf_router.ir import (
    BBox,
    FigureBlock,
    FormulaBlock,
    HeadingBlock,
    IR,
    PageResult,
    TableBlock,
)

#: Markdown shapes the walk understands. Mirrors the native adapter's
#: scanner (renderers must not import adapters, so the regexes live here).
_HEADING_RE = re.compile(r"^(#{1,6})\s+(.+?)\s*$")
_TABLE_ROW_RE = re.compile(r"^\s*\|.*\|\s*$")
_TABLE_SEP_RE = re.compile(r"^\s*\|?\s*:?-+:?\s*(\|\s*:?-+:?\s*)+\|?\s*$")
_IMAGE_RE = re.compile(r"^!\[(?P<alt>[^\]]*)\]\((?P<uri>[^)\s]+)\)\s*$")
_LIST_RE = re.compile(r"^\s*(?P<marker>(?:[-*+]|\d+[.)]))\s+(?P<text>.+?)\s*$")

#: Mime type for a sidecar image, by file extension.
_IMAGE_MIMETYPES: dict[str, str] = {
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".gif": "image/gif",
    ".webp": "image/webp",
    ".bmp": "image/bmp",
    ".tif": "image/tiff",
    ".tiff": "image/tiff",
    ".svg": "image/svg+xml",
}


class DoclingUnavailableError(RuntimeError):
    """Raised when the ``docling`` extra (docling-core) is not installed."""


# ---------------------------------------------------------------------------
# docling-core symbols (loaded lazily — the import must stay cheap)
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class _Types:
    """The docling-core symbols this renderer needs."""

    DoclingDocument: Any
    DocItemLabel: Any
    Size: Any
    BoundingBox: Any
    CoordOrigin: Any
    ProvenanceItem: Any
    ImageRef: Any
    TableData: Any
    TableCell: Any


_TYPES: _Types | None = None


def _types() -> _Types:
    """Import docling-core on demand; cached after the first call."""
    global _TYPES
    if _TYPES is not None:
        return _TYPES
    try:
        from docling_core.types.doc.base import BoundingBox, CoordOrigin, Size
        from docling_core.types.doc.common.reference import ImageRef, ProvenanceItem
        from docling_core.types.doc.document import DoclingDocument
        from docling_core.types.doc.items.table.table_data import TableCell, TableData
        from docling_core.types.doc.labels import DocItemLabel
    except ImportError as exc:
        raise DoclingUnavailableError(
            "the docling renderer needs docling-core: install the extra with "
            "`pip install 'pdf-router[docling]'`"
        ) from exc
    _TYPES = _Types(
        DoclingDocument=DoclingDocument,
        DocItemLabel=DocItemLabel,
        Size=Size,
        BoundingBox=BoundingBox,
        CoordOrigin=CoordOrigin,
        ProvenanceItem=ProvenanceItem,
        ImageRef=ImageRef,
        TableData=TableData,
        TableCell=TableCell,
    )
    return _TYPES


# ---------------------------------------------------------------------------
# Item model (renderer-internal)
# ---------------------------------------------------------------------------


@dataclass
class _Item:
    """One Docling item, before it becomes a docling-core call."""

    kind: str  # text | header | list | table | picture | formula | caption
    text: str = ""
    level: int = 1
    rows: list[list[str]] = field(default_factory=list)
    header_rows: int = 0
    uri: str | None = None
    bbox: BBox | None = None
    #: True when the bbox comes from an OCR-sourced block (raster pixels —
    #: not the page frame, so it must never become a ProvenanceItem).
    ocr: bool = False
    #: List items: ordered (``1.``) vs unordered (``-``), and the marker as
    #: it appeared in the markdown.
    enumerated: bool = False
    marker: str = ""
    #: List items: which list run they belong to. Two runs are separated by
    #: a blank line or by a change of marker kind — docling wants one
    #: ``ListGroup`` per run, not one per document.
    run: int = 0


# ---------------------------------------------------------------------------
# Markdown helpers
# ---------------------------------------------------------------------------


def _norm(text: str) -> str:
    """Whitespace/case-normalized key for matching markdown against blocks."""
    return " ".join(text.split()).casefold()


def _split_cells(line: str) -> list[str]:
    """Split one markdown pipe row into cells (``\\|`` stays inside a cell)."""
    inner = line.strip()
    if inner.startswith("|"):
        inner = inner[1:]
    if inner.endswith("|") and not inner.endswith("\\|"):
        inner = inner[:-1]
    cells: list[str] = []
    buf: list[str] = []
    escaped = False
    for ch in inner:
        if escaped:
            if ch == "|":
                buf.append("|")
            else:
                buf.append("\\")
                buf.append(ch)
            escaped = False
            continue
        if ch == "\\":
            escaped = True
            continue
        if ch == "|":
            cells.append("".join(buf).strip())
            buf = []
            continue
        buf.append(ch)
    if escaped:
        buf.append("\\")
    cells.append("".join(buf).strip())
    return cells


def _pipe_rows(text: str) -> list[list[str]]:
    """Parse a markdown pipe-table region into a 2-D grid of cell strings."""
    rows: list[list[str]] = []
    for line in text.splitlines():
        if not _TABLE_ROW_RE.match(line or ""):
            continue
        if _TABLE_SEP_RE.match(line or ""):
            continue  # the separator row is not a row of the grid
        rows.append(_split_cells(line))
    return rows


def _is_ocr(block: object) -> bool:
    """True for a block produced by the OCR structure pass (ADR-0005)."""
    return str(getattr(block, "source", "")).startswith("ocr_")


def _geometry_key(entry: tuple[int, object]) -> tuple[float, float]:
    """Reading order for OCR-sourced blocks (raster pixels, top-left origin).

    Same rule as the chunks renderer (ADR-0006): sort by (y0, x0); blocks
    without a bbox keep their IR order (stable sort).
    """
    bb = getattr(entry[1], "bbox", None)
    if bb is None:
        return (float("inf"), float("inf"))
    return (bb.y0, bb.x0)


# ---------------------------------------------------------------------------
# IR -> items
# ---------------------------------------------------------------------------


def _block_item(block: object, page: PageResult) -> _Item | None:
    """The item a block contributes when the markdown has no counterpart."""
    ocr = _is_ocr(block)
    if isinstance(block, HeadingBlock):
        return _Item(kind="header", text=block.text, level=block.level, bbox=block.bbox, ocr=ocr)
    if isinstance(block, TableBlock):
        return _Item(
            kind="table",
            rows=block.rows,
            header_rows=block.header_rows,
            bbox=block.bbox,
            ocr=ocr,
        )
    if isinstance(block, FigureBlock):
        uri = None
        if block.image_index is not None and 0 <= block.image_index < len(page.images):
            uri = page.images[block.image_index]
        if uri:
            return _Item(kind="picture", uri=uri, text=block.caption or "")
        if block.caption:
            return _Item(kind="caption", text=block.caption)
        return None
    if isinstance(block, FormulaBlock):
        if block.latex:
            return _Item(kind="formula", text=block.latex, bbox=block.bbox, ocr=ocr)
        return None
    return None


def _page_items(page: PageResult) -> list[_Item]:
    """Items for one page: the markdown spine, enriched by the blocks."""
    blocks = list(page.blocks)
    ocr_page = any(_is_ocr(b) for b in blocks)

    heading_by_text: dict[str, tuple[int, HeadingBlock]] = {}
    table_by_md: dict[str, tuple[int, TableBlock]] = {}
    figure_by_name: dict[str, int] = {}
    for i, b in enumerate(blocks):
        if isinstance(b, HeadingBlock):
            heading_by_text.setdefault(_norm(b.text), (i, b))
        elif isinstance(b, TableBlock):
            table_by_md.setdefault(_norm(b.markdown), (i, b))
        elif isinstance(b, FigureBlock):
            if b.image_index is not None and 0 <= b.image_index < len(page.images):
                figure_by_name.setdefault(Path(page.images[b.image_index]).name, i)

    items: list[_Item] = []
    used: set[int] = set()
    para: list[str] = []
    #: The list run the walker is in (see ``_Item.run``).
    list_run = 0
    #: The marker kind of the current run ("-" vs "1.").
    list_kind = ""

    def flush() -> None:
        if para:
            text = "\n".join(para).strip()
            if text:
                items.append(_Item(kind="text", text=text))
            para.clear()

    lines = (page.markdown or "").splitlines()
    i = 0
    n = len(lines)
    while i < n:
        line = lines[i]
        if not line.strip():
            flush()
            if list_kind:
                # A blank line ends the list run: the next bullet opens a
                # new one (the marker-kind check below starts the new run).
                list_kind = ""
            i += 1
            continue

        heading = _HEADING_RE.match(line)
        if heading is not None:
            flush()
            text = heading.group(2).strip()
            level = len(heading.group(1))
            bbox: BBox | None = None
            ocr = False
            heading_hit = heading_by_text.get(_norm(text))
            if heading_hit is not None:
                used.add(heading_hit[0])
                level = heading_hit[1].level
                bbox = heading_hit[1].bbox
                ocr = _is_ocr(heading_hit[1])
            items.append(_Item(kind="header", text=text, level=level, bbox=bbox, ocr=ocr))
            i += 1
            continue

        if _TABLE_ROW_RE.match(line) and i + 1 < n and _TABLE_SEP_RE.match(lines[i + 1] or ""):
            flush()
            j = i + 2
            while j < n and _TABLE_ROW_RE.match(lines[j] or ""):
                j += 1
            region = "\n".join(lines[i:j])
            table_hit = table_by_md.get(_norm(region))
            if table_hit is not None:
                used.add(table_hit[0])
                block = table_hit[1]
                rows = block.rows or _pipe_rows(region)
                items.append(
                    _Item(
                        kind="table",
                        rows=rows,
                        header_rows=block.header_rows,
                        bbox=block.bbox,
                        ocr=_is_ocr(block),
                    )
                )
            elif not ocr_page:
                # No block for this region: the pipe grid is the truth.
                rows = _pipe_rows(region)
                items.append(_Item(kind="table", rows=rows, header_rows=1 if rows else 0))
            # OCR page, no matching block: the region is the scrambled twin
            # of a TableBlock — drop it, the block carries the truth.
            i = j
            continue

        image = _IMAGE_RE.match(line.strip())
        if image is not None:
            flush()
            uri = image.group("uri")
            idx = figure_by_name.get(Path(uri).name)
            if idx is not None:
                # The block owns the figure: emit it here (in place, so the
                # reading order survives) instead of the bare markdown link.
                used.add(idx)
                item = _block_item(blocks[idx], page)
                items.append(item if item is not None else _Item(kind="picture", uri=uri))
            else:
                items.append(_Item(kind="picture", uri=uri))
            i += 1
            continue

        bullet = _LIST_RE.match(line)
        if bullet is not None:
            flush()
            marker = bullet.group("marker")
            kind = "ordered" if marker[0].isdigit() else "bullet"
            if kind != list_kind:
                # A different marker kind starts a new list (an ordered list
                # is not a continuation of an unordered one).
                list_run += 1
                list_kind = kind
            items.append(
                _Item(
                    kind="list",
                    text=bullet.group("text"),
                    enumerated=kind == "ordered",
                    marker=marker,
                    run=list_run,
                )
            )
            i += 1
            continue

        para.append(line)
        i += 1
    flush()

    # --- blocks the markdown does not carry ------------------------------
    tail = [(idx, b) for idx, b in enumerate(blocks) if idx not in used]
    if ocr_page:
        tail.sort(key=_geometry_key)
    for _, tail_block in tail:
        item = _block_item(tail_block, page)
        if item is not None:
            items.append(item)
    return items


# ---------------------------------------------------------------------------
# items -> docling-core
# ---------------------------------------------------------------------------


def _page_size(page: PageResult, T: _Types) -> Any:
    """Page size in points; 0x0 when the adapter did not report one."""
    bb = page.page_bbox
    if bb is not None and len(bb) >= 2:
        width, height = float(bb[0]), float(bb[1])
        if width > 0 and height > 0:
            return T.Size(width=width, height=height)
    return T.Size()


def _provenance(bbox: BBox | None, ocr: bool, page_no: int, size: Any, T: _Types) -> Any | None:
    """A ``ProvenanceItem``, or ``None`` when the geometry is not honest.

    Two rules, both about not lying:

    - docling clamps every bbox to the page size — on a page of unknown size
      (0x0) the box would silently collapse to zero, so no prov is emitted
      without a known size;
    - OCR bboxes live in raster pixels (ADR-0005), not in the page frame —
      they are never promoted to a prov.
    """
    if bbox is None or ocr:
        return None
    if size.width <= 0 or size.height <= 0:
        return None
    # Native IR bboxes are PDF points with y up (CropBox-relative frame);
    # docling's BOTTOMLEFT origin is the page's bottom-left corner.
    return T.ProvenanceItem(
        page_no=page_no,
        bbox=T.BoundingBox(
            l=bbox.x0,
            t=bbox.y1,
            r=bbox.x1,
            b=bbox.y0,
            coord_origin=T.CoordOrigin.BOTTOMLEFT,
        ),
        charspan=(0, 0),
    )


def _table_data(item: _Item, T: _Types) -> Any:
    """The item's grid as docling ``TableData`` (a complete, padded grid)."""
    rows = item.rows
    n_rows = len(rows)
    n_cols = max((len(r) for r in rows), default=0)
    cells: list[Any] = []
    for r, row in enumerate(rows):
        for c in range(n_cols):
            text = row[c] if c < len(row) else ""
            cells.append(
                T.TableCell(
                    start_row_offset_idx=r,
                    end_row_offset_idx=r + 1,
                    start_col_offset_idx=c,
                    end_col_offset_idx=c + 1,
                    text=text,
                    column_header=(r < item.header_rows),
                    row_header=False,
                    row_section=False,
                )
            )
    return T.TableData(num_rows=n_rows, num_cols=n_cols, table_cells=cells)


def _image_ref(uri: str, T: _Types) -> Any:
    """A docling ``ImageRef`` for a sidecar path (size/dpi unknown → 0/72)."""
    mimetype = _IMAGE_MIMETYPES.get(Path(uri).suffix.lower(), "image/png")
    return T.ImageRef(mimetype=mimetype, dpi=72, size=T.Size(), uri=uri)


def _emit_page(doc: Any, items: list[_Item], page_no: int, size: Any, T: _Types) -> None:
    """Add one page's items to the document, in reading order.

    Consecutive list items share one ``ListGroup`` — docling expects a
    list group per list run, and letting ``add_list_item`` create one per
    item (its deprecated fallback) would scatter the list.
    """
    list_group: Any | None = None
    list_run = -1
    for item in items:
        prov = _provenance(item.bbox, item.ocr, page_no, size, T)
        if item.kind == "text":
            doc.add_text(label=T.DocItemLabel.TEXT, text=item.text, prov=prov)
        elif item.kind == "header":
            doc.add_text(
                label=T.DocItemLabel.SECTION_HEADER,
                text=item.text,
                level=item.level,
                prov=prov,
            )
        elif item.kind == "list":
            if list_group is None or item.run != list_run:
                list_group = doc.add_list_group(name=None)
                list_run = item.run
            doc.add_list_item(
                text=item.text,
                enumerated=item.enumerated,
                marker=item.marker,
                parent=list_group,
                prov=prov,
            )
        elif item.kind == "formula":
            doc.add_text(label=T.DocItemLabel.FORMULA, text=item.text, prov=prov)
        elif item.kind == "caption":
            doc.add_text(label=T.DocItemLabel.CAPTION, text=item.text, prov=prov)
        elif item.kind == "table":
            doc.add_table(data=_table_data(item, T), prov=prov)
        elif item.kind == "picture":
            caption = None
            if item.text:
                caption = doc.add_text(label=T.DocItemLabel.CAPTION, text=item.text)
            if item.uri:
                doc.add_picture(
                    image=_image_ref(item.uri, T),
                    caption=caption,
                    prov=prov,
                )
        else:  # pragma: no cover — kinds are closed
            raise ValueError(f"unknown item kind: {item.kind!r}")
        if item.kind != "list":
            list_group = None


# ---------------------------------------------------------------------------
# Document level
# ---------------------------------------------------------------------------


def _doc_name(ir: IR) -> str:
    """Document name: the metadata title, else the source file name.

    Docling's ``name`` is the document's display name — the title is the
    honest choice when the IR carries one, the file stem otherwise.
    """
    title = (ir.metadata.title or "").strip()
    if title:
        return title
    if ir.metadata.source_path:
        return Path(ir.metadata.source_path).stem or "document"
    return "document"


def build_document(ir: IR) -> Any:
    """Build the docling ``DoclingDocument`` for an IR (no I/O).

    ``doc.origin`` stays unset: docling's ``DocumentOrigin`` demands a
    sha256 of the *source binary*, and a renderer may not read the input
    file (DESIGN §7). An invented hash would be worse than an honest
    "not computed" — the document name already carries the file stem.
    """
    T = _types()
    doc = T.DoclingDocument(name=_doc_name(ir))
    for page in ir.pages:
        page_no = page.page + 1  # docling pages are 1-indexed
        size = _page_size(page, T)
        doc.add_page(page_no=page_no, size=size)
        _emit_page(doc, _page_items(page), page_no, size, T)
    return doc


def ensure_available() -> None:
    """Fail fast when the optional ``docling`` extra is not installed.

    The CLI calls this *before* the pipeline runs: a missing extra is a
    usage error, and discovering it after the document was processed
    (OCR included) would waste the whole run on a mistake the user can
    fix in one line.
    """
    _types()


def render(ir: IR, *, config: RendererConfig) -> str:
    """Render the IR to a DoclingDocument JSON document.

    Pure function — no I/O (DESIGN §7). The renderer has no knobs of its own
    yet: ``config`` is accepted for the common renderer signature, and the
    output is fully determined by the IR.
    """
    doc = build_document(ir)
    return json.dumps(doc.export_to_dict(), indent=2, ensure_ascii=False) + "\n"
