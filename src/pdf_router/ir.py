"""Intermediate Representation (IR) — the single shared contract.

Every adapter produces it, every renderer consumes it. Adapters never import
renderers and vice versa; both may import this module.

Indexing convention (locked):
- global lists in ``Metadata`` (``pages_with_*``, ``pages_needing_ocr``) are
  **1-indexed**, matching pdf-inspector's convention;
- ``PageResult.page`` is **0-indexed**, matching Python iteration;
- bbox coordinates are **[x0, y0, x1, y1]** in PDF points, in pdf-inspector's
  CropBox-relative frame (changed in 1.17.0).

Structural blocks (``PageResult.blocks``) are populated by adapters that have
access to structural data (pdf-inspector 1.17+). They are **advisory** — the
authoritative text for legacy renderers (``md``, ``chunks``) remains
``PageResult.markdown``. See docs/adr/0001-ir-structural-blocks.md.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from enum import Enum
from typing import Any

IR_VERSION = "0.2"


class SrcTool(str, Enum):
    """Provenance tag: which tool produced a page's markdown."""

    PDF_INSPECTOR = "pdf_inspector"
    PDF_INSPECTOR_OCR = "pdf_inspector_ocr"
    TESSERACT = "tesseract"
    GLM_OCR = "glm_ocr"
    DEEPSEEK_OCR = "deepseek_ocr"


@dataclass
class OcrReasonEntry:
    """One entry of ``ocr_reasons_by_page`` (1-indexed page)."""

    page: int
    reasons: list[str] = field(default_factory=list)


@dataclass
class Metadata:
    """Document-level facts, mapped from pdf-inspector's ``PdfResult``."""

    pdf_type: str = "unknown"  # text_based | scanned | image_based | mixed
    page_count: int = 0
    confidence: float = 0.0
    processing_time_ms: float = 0.0
    title: str | None = None
    is_complex_layout: bool = False
    has_encoding_issues: bool = False
    pages_with_tables: list[int] = field(default_factory=list)  # 1-indexed
    pages_with_columns: list[int] = field(default_factory=list)  # 1-indexed
    pages_needing_ocr: list[int] = field(default_factory=list)  # 1-indexed
    ocr_reasons_by_page: list[OcrReasonEntry] = field(default_factory=list)


# --- Structural blocks (IR 0.2) ----------------------------------------------


@dataclass
class BBox:
    """Axis-aligned rectangle in PDF points, CropBox-relative (1.17+ frame)."""

    x0: float
    y0: float
    x1: float
    y1: float


@dataclass
class HeadingBlock:
    """A heading on the page, recovered from structure-tree or font-size detection.

    ``level`` is 1-6. ``bbox`` is the visual span of the heading text run
    (None when only the structure-tree gave us the role, no positional data).
    ``source`` records which detector produced the block — useful for the
    DocLang renderer to decide whether to trust ``bbox`` or omit ``<location>``.
    """

    level: int
    text: str
    bbox: BBox | None = None
    source: str = "structure_tree"  # "structure_tree" | "font_size_fallback"


@dataclass
class TableBlock:
    """A table on the page.

    ``markdown`` is the markdown-pipe-table as pdf-inspector emits it (preserved
    verbatim so ``md`` renderer needs no change). ``rows`` is the same content
    parsed into a 2D list of cell strings, header row included. ``header_rows``
    counts how many leading rows are headers (heuristic: rows where every cell
    is short text, no digits). ``bbox`` is the table region from
    ``extract_text_in_regions`` — the *region* bbox, not per-cell (per-cell
    tables are not in pdf-inspector 1.17 Python bindings; only in Rust).
    """

    markdown: str
    rows: list[list[str]] = field(default_factory=list)
    header_rows: int = 0
    bbox: BBox | None = None


@dataclass
class FigureBlock:
    """A figure on the page.

    pdf-inspector 1.17 Python bindings do not expose first-class figure objects,
    so this is populated heuristically from text-position runs whose bbox is
    wider than it is tall and which contain no text content (i.e. image-only
    regions). ``caption`` is the nearest text run below the bbox matching a
    caption-like pattern (``Рис. N`` / ``Fig. N`` / ``Табл. N``).
    """

    bbox: BBox | None = None
    caption: str | None = None
    image_index: int | None = None  # xref into the page's image list, if known


@dataclass
class FormulaBlock:
    """A formula on the page (reserved).

    pdf-inspector 1.17 Python bindings do not extract formulas. This block
    exists so downstream renderers (DocLang in particular) can emit a typed
    placeholder and downstream tools do not need schema migration when
    upstream adds the API.
    """

    latex: str | None = None
    bbox: BBox | None = None
    source: str = "none"  # always "none" until upstream provides it


# --- Page + IR --------------------------------------------------------------


@dataclass
class PageResult:
    """Per-page markdown with provenance (0-indexed ``page``).

    ``markdown`` remains the authoritative concatenation for legacy renderers.
    ``blocks`` is the structural decomposition; an empty list means the adapter
    did not produce blocks (e.g. legacy adapters, OCR-only paths).
    """

    page: int
    markdown: str = ""
    needs_ocr: bool = False
    ocr_reason: str | None = None
    src_tool: SrcTool = SrcTool.PDF_INSPECTOR
    page_bbox: list[float] | None = None  # [w, h] in pt; opt-in
    error: str | None = None  # set on per-page failure
    blocks: list[HeadingBlock | TableBlock | FigureBlock | FormulaBlock] = field(
        default_factory=list
    )
    images: list[str] = field(default_factory=list)  # relative paths from output_dir


@dataclass
class IR:
    """The whole document: metadata + per-page results."""

    metadata: Metadata = field(default_factory=Metadata)
    pages: list[PageResult] = field(default_factory=list)


# --- (De)serialization ------------------------------------------------------


_KIND_TO_CLS: dict[str, type] = {
    "HeadingBlock": HeadingBlock,
    "TableBlock": TableBlock,
    "FigureBlock": FigureBlock,
    "FormulaBlock": FormulaBlock,
}


def _bbox_from(d: Any) -> BBox | None:
    if isinstance(d, BBox):
        return d
    if isinstance(d, dict) and {"x0", "y0", "x1", "y1"} <= set(d):
        return BBox(float(d["x0"]), float(d["y0"]), float(d["x1"]), float(d["y1"]))
    return None


def _block_kind(d: dict[str, Any]) -> str:
    if "kind" in d and d["kind"] in _KIND_TO_CLS:
        return d["kind"]
    if "level" in d and "text" in d:
        return "HeadingBlock"
    if "markdown" in d and "rows" in d:
        return "TableBlock"
    if "latex" in d:
        return "FormulaBlock"
    return "FigureBlock"


def to_dict(ir: IR, *, include_page_bbox: bool = False) -> dict[str, Any]:
    """Serialize an IR to a plain dict (JSON-compatible).

    ``page_bbox`` is stripped unless ``include_page_bbox`` is set. ``blocks``
    are always serialized with a ``kind`` tag for round-trip. ``bbox`` is
    expanded to ``{"x0":..., "y0":..., "x1":..., "y1":...}``.
    """
    d = asdict(ir)
    d["ir_version"] = IR_VERSION
    d["metadata"]["ocr_reasons_by_page"] = [
        {"page": e["page"], "reasons": e["reasons"]}
        for e in d["metadata"]["ocr_reasons_by_page"]
    ]
    for p in d["pages"]:
        p["src_tool"] = p["src_tool"].value if isinstance(p["src_tool"], SrcTool) else p["src_tool"]
        if not include_page_bbox:
            p.pop("page_bbox", None)
        # asdict leaves type-tag info out; re-tag by class identity (Python only)
        tagged: list[dict[str, Any]] = []
        for i, b in enumerate(p.get("blocks", []) or []):
            if isinstance(b, dict):
                # already a dict (defensive); ensure kind present
                b.setdefault("kind", _block_kind(b))
                tagged.append(b)
                continue
            cls = type(b).__name__
            if cls not in _KIND_TO_CLS:
                raise ValueError(f"unknown block class in to_dict: {cls!r}")
            d_block: dict[str, Any] = {"kind": cls}
            d_block["level"] = b.level
            d_block["text"] = b.text
            d_block["bbox"] = (
                {"x0": b.bbox.x0, "y0": b.bbox.y0, "x1": b.bbox.x1, "y1": b.bbox.y1}
                if b.bbox is not None
                else None
            )
            d_block["source"] = b.source
            tagged.append(d_block)
        p["blocks"] = tagged
    return d


def from_dict(d: dict[str, Any]) -> IR:
    """Rebuild an IR from :func:`to_dict` output (recursive inverse)."""
    md = d.get("metadata", {})
    metadata = Metadata(
        pdf_type=md.get("pdf_type", "unknown"),
        page_count=md.get("page_count", 0),
        confidence=md.get("confidence", 0.0),
        processing_time_ms=md.get("processing_time_ms", 0.0),
        title=md.get("title"),
        is_complex_layout=md.get("is_complex_layout", False),
        has_encoding_issues=md.get("has_encoding_issues", False),
        pages_with_tables=list(md.get("pages_with_tables", [])),
        pages_with_columns=list(md.get("pages_with_columns", [])),
        pages_needing_ocr=list(md.get("pages_needing_ocr", [])),
        ocr_reasons_by_page=[
            OcrReasonEntry(page=int(e["page"]), reasons=list(e.get("reasons", [])))
            for e in md.get("ocr_reasons_by_page", [])
        ],
    )
    pages: list[PageResult] = []
    for p in d.get("pages", []):
        blocks: list[Any] = []
        for b in p.get("blocks", []) or []:
            kind = _block_kind(b)
            bbox = _bbox_from(b.get("bbox"))
            if kind == "HeadingBlock":
                blocks.append(
                    HeadingBlock(
                        level=int(b["level"]),
                        text=str(b.get("text", "")),
                        bbox=bbox,
                        source=str(b.get("source", "structure_tree")),
                    )
                )
            elif kind == "TableBlock":
                blocks.append(
                    TableBlock(
                        markdown=str(b.get("markdown", "")),
                        rows=[list(r) for r in b.get("rows", [])],
                        header_rows=int(b.get("header_rows", 0)),
                        bbox=bbox,
                    )
                )
            elif kind == "FigureBlock":
                blocks.append(
                    FigureBlock(
                        bbox=bbox,
                        caption=b.get("caption"),
                        image_index=b.get("image_index"),
                    )
                )
            elif kind == "FormulaBlock":
                blocks.append(
                    FormulaBlock(
                        latex=b.get("latex"),
                        bbox=bbox,
                        source=str(b.get("source", "none")),
                    )
                )
        pages.append(
            PageResult(
                page=int(p["page"]),
                markdown=p.get("markdown", ""),
                needs_ocr=bool(p.get("needs_ocr", False)),
                ocr_reason=p.get("ocr_reason"),
                src_tool=SrcTool(p.get("src_tool", SrcTool.PDF_INSPECTOR.value)),
                page_bbox=list(p["page_bbox"]) if p.get("page_bbox") is not None else None,
                error=p.get("error"),
                blocks=blocks,
                images=list(p.get("images", [])),
            )
        )
    return IR(metadata=metadata, pages=pages)
