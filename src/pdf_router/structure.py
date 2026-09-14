"""Post-OCR structure pass — scanned pages grow IR blocks (ADR-0005).

One engine-agnostic seam: ``(raster, ocr_result) -> blocks``. The pass
consumes the raster the OCR stage already rendered (rasterize-once,
TKT-RT-10) and the geometry of the OCR lines (rapidocr
``boxes + txts + scores``, which v0.5 flattened away). Engines that carry
their own structure short-circuit the local model: glm_ocr's
``layout_details`` becomes blocks directly (:func:`blocks_from_glm`).
Boxless engines (tesseract / deepseek_ocr / pdf_inspector_ocr) get the
cheapest honest layer, :func:`blocks_from_pipe_markdown` — pipe-table
parsing of their markdown, no fake geometry, no headings.

Table recovery: RapidAI TableStructureRec — ``lineless_table_rec``
(default; handles mixed ruled/unruled tables, the corpus norm) or
``wired_table_rec`` (fully ruled tables only, much smaller model).
Apache-2.0 pure-python wheels on CPU onnxruntime (already a project
dependency); weights auto-download from ModelScope on first use, same
pattern as rapidocr. The cell grid comes from the engine's
``pred_html`` via rowspan/colspan slot-filling — the emitted HTML *is*
the grid. The engines' ``logic_points`` are NOT used for assembly:
they cover only anchor cells (53 points for the 21x7 p14 anchor) and
arrive unordered, so zipping them against ``<td>`` texts silently
scrambles the table (found live on the p14 anchor).

Headings: box-height heuristic against the page-median line height (no
credible local layout model exists — see the wayfinder research note).
Levels are restricted to {1, 2}: the heuristic cannot honestly support
1-6. Table zones are masked so cells cannot pose as headings.

Frame contract: bboxes on OCR-sourced blocks stay in **raster pixels**
(top-left origin) — they are hints, not PDF points; no rescaling is
performed (ADR-0005: coordinates only when resource-cheap).

Provenance ``source`` values produced here: ``ocr_table_lineless``,
``ocr_table_wired``, ``ocr_pipe_fallback``, ``ocr_glm_layout``,
``ocr_box_height``.
"""

from __future__ import annotations

import logging
import threading
from dataclasses import replace
from html.parser import HTMLParser
from typing import Any

from pdf_router.config import RouterConfig
from pdf_router.ir import BBox, HeadingBlock, TableBlock

logger = logging.getLogger("pdf_router")

#: Sentence terminators that disqualify a line from being a heading —
#: body prose ends with punctuation, headings (in the anchor corpus) do not.
_SENTENCE_TERMINATORS = ".!?…"

#: A heading candidate longer than this is body text (a wrapped paragraph
#: line can be tall if the scan is skewed); anchors gate the exact value.
_MAX_HEADING_CHARS = 120

#: Valid values for ``[structure].table_engine``. ``auto`` (table_cls
#: routing) is reserved — the classifier wheel is deliberately not vendored
#: yet (ADR-0005 §7); config load rejects it until it is.
_TABLE_ENGINES = ("lineless", "wired")


class StructureError(RuntimeError):
    """The structure pass could not run (engine init or inference failed)."""


# ---------------------------------------------------------------------------
# Lazy table-engine singleton (pattern: ocr._rapidocr_get_engine)
# ---------------------------------------------------------------------------

_STRUCTURE_ENGINE_CACHE: dict[str, Any] = {}
_STRUCTURE_ENGINE_LOCK = threading.Lock()


def _get_table_engine(kind: str):
    """Construct (or fetch cached) the wired/lineless recognition engine.

    Module-level cache keyed by engine kind: model init loads ~8-88 MB of
    ONNX sessions, so it must happen once per process, not once per page.
    """
    cached = _STRUCTURE_ENGINE_CACHE.get(kind)
    if cached is not None:
        return cached

    with _STRUCTURE_ENGINE_LOCK:
        cached = _STRUCTURE_ENGINE_CACHE.get(kind)
        if cached is not None:
            return cached

        # numpy before any onnxruntime consumer — same native-init order
        # pin as ocr._rapidocr_get_engine (OpenMP futex hang insurance).
        import numpy  # noqa: F401  (imported for native init order)

        if kind == "lineless":
            from lineless_table_rec.main import (
                LinelessTableInput,
                LinelessTableRecognition,
            )

            engine = LinelessTableRecognition(LinelessTableInput())
        elif kind == "wired":
            from wired_table_rec.main import (
                WiredTableInput,
                WiredTableRecognition,
            )

            engine = WiredTableRecognition(WiredTableInput(model_type="unet"))
        else:
            raise StructureError(
                f"structure.table_engine = {kind!r} not available "
                f"(expected one of {_TABLE_ENGINES}; 'auto' awaits table_cls)"
            )

        _STRUCTURE_ENGINE_CACHE[kind] = engine
        return engine


# ---------------------------------------------------------------------------
# Table pass (rapidocr path: raster + line geometry -> TableBlock)
# ---------------------------------------------------------------------------


def _table_pass(raster: Any, ocr_result: list, config: RouterConfig) -> tuple[list[TableBlock], BBox | None]:
    """Run the configured table engine over the OCR lines.

    Returns ``([TableBlock], table_bbox_or_None)``. Raises
    :class:`StructureError` on engine failure — the caller (ocr.py) logs
    and keeps the flat text; a structure failure must never kill a page
    whose OCR text is already good.
    """
    kind = config.structure.table_engine
    try:
        engine = _get_table_engine(kind)
    except Exception as exc:
        raise StructureError(f"table engine {kind!r} init failed: {exc}") from exc

    try:
        out = engine(raster, ocr_result=ocr_result)
    except Exception as exc:
        raise StructureError(f"{kind} table inference failed: {exc}") from exc

    grid = _html_table_grid(getattr(out, "pred_html", "") or "")
    cell_bboxes = getattr(out, "cell_bboxes", None)
    cell_bboxes = [] if cell_bboxes is None else list(cell_bboxes)
    if not grid or not any(any(row) for row in grid):
        return [], None

    source = "ocr_table_lineless" if kind == "lineless" else "ocr_table_wired"
    block = TableBlock(
        markdown=_rows_to_pipe_markdown(grid),
        rows=grid,
        header_rows=1,
        bbox=_union_bbox(cell_bboxes),
        source=source,
    )
    return [block], block.bbox


def _heading_pass(ocr_result: list, table_bboxes: list[BBox], config: RouterConfig) -> list[HeadingBlock]:
    """Recover headings from line geometry: height vs page median.

    A line is a heading candidate when its box is at least
    ``heading_small_ratio`` × the page-median line height, the text is
    short and has no sentence terminator. Level 1 additionally requires
    ``heading_big_ratio``. Table zones are masked first so cells cannot
    pose as headings. Levels are honest {1, 2} only (ADR-0005 §3).
    """
    import numpy as np

    items: list[tuple[np.ndarray, str]] = []
    for box, text, _score in ocr_result:
        t = str(text).strip()
        if t and any(ch.isalpha() for ch in t):
            try:
                items.append((np.asarray(box, dtype=float).reshape(4, 2), t))
            except (TypeError, ValueError):
                continue
    if len(items) < 4:
        return []  # too few lines for a meaningful median

    heights = np.array([b[:, 1].max() - b[:, 1].min() for b, _ in items])
    median = float(np.median(heights))
    if median <= 0:
        return []

    big = float(config.structure.heading_big_ratio)
    small = float(config.structure.heading_small_ratio)

    out: list[HeadingBlock] = []
    for (box, text), h in zip(items, heights):
        if h < median * small:
            continue
        if len(text) > _MAX_HEADING_CHARS:
            continue
        if text.rstrip().endswith(_SENTENCE_TERMINATORS):
            continue
        x0, x1 = float(box[:, 0].min()), float(box[:, 0].max())
        y0, y1 = float(box[:, 1].min()), float(box[:, 1].max())
        if any(
            not (x1 < tb.x0 or x0 > tb.x1 or y1 < tb.y0 or y0 > tb.y1)
            for tb in table_bboxes
        ):
            continue  # intersects a table zone — a cell, not a heading
        level = 1 if h >= median * big else 2
        out.append(
            HeadingBlock(
                level=level,
                text=text,
                bbox=BBox(x0=x0, y0=y0, x1=x1, y1=y1),
                source="ocr_box_height",
            )
        )
    out.sort(key=lambda hb: hb.bbox.y0)  # raster top-left origin: top first
    return out


def structure_page(raster: Any, ocr_result: list, config: RouterConfig) -> list:
    """One structure pass over one OCRed page → IR blocks.

    Args:
        raster: the page raster the OCR stage already rendered (consumed
            as-is; never re-rendered — TKT-RT-10).
        ocr_result: ``[(box(4,2), text, score), ...]`` — the rapidocr
            lines with their geometry, in reading-box order.
        config: full RouterConfig (``[structure]`` knobs live there).

    Returns:
        ``[TableBlock, ...HeadingBlock]`` in reading order — tables first
        (they mask heading candidates), then headings top-to-bottom.

    Raises:
        StructureError: engine init or inference failed. Callers may
            degrade to flat text; the page's OCR markdown stays good.
    """
    blocks: list = []
    if not ocr_result:
        return blocks
    table_blocks, table_bbox = _table_pass(raster, ocr_result, config)
    blocks.extend(table_blocks)
    table_bboxes = [table_bbox] if table_bbox is not None else []
    blocks.extend(_heading_pass(ocr_result, table_bboxes, config))
    return blocks


# ---------------------------------------------------------------------------
# glm_ocr path: layout_details -> blocks (cloud opt-in, ADR-0005 §1)
# ---------------------------------------------------------------------------


def blocks_from_glm(layout_details: Any, page_idx: int = 0) -> list:
    """Build blocks from glm_ocr's ``layout_details`` response field.

    Contract (wayfinder research §4): ``layout_details`` is a list of
    per-page block lists; each block carries ``bbox_2d [x0,y0,x1,y1]``
    (pixels, top-left origin), ``label`` (text/table/image/figure/...),
    ``native_label`` (``vision_title`` etc.), ``content`` (markdown /
    HTML table / LaTeX; ``None`` or empty for images), ``index``.

    ``vision_title`` blocks become level-1 headings (glm gives no deeper
    hierarchy); ``label="table"`` blocks with HTML content become
    TableBlocks (source ``ocr_glm_layout``). Everything else is skipped.
    """
    pages = list(layout_details or [])
    if not pages:
        return []
    page_blocks = pages[min(max(page_idx, 0), len(pages) - 1)]
    ordered = sorted(
        (b for b in page_blocks if isinstance(b, dict)),
        key=lambda b: b.get("index", 0),
    )

    blocks: list = []
    for b in ordered:
        native = str(b.get("native_label") or "")
        label = str(b.get("label") or "")
        content = b.get("content") or ""
        bbox = _glm_bbox(b.get("bbox_2d"))

        if native == "vision_title" or label == "title":
            text = str(content).strip().lstrip("#").strip()
            if text:
                blocks.append(
                    HeadingBlock(
                        level=1, text=text, bbox=bbox, source="ocr_glm_layout"
                    )
                )
        elif label == "table" or native == "table":
            if "<table" not in str(content).lower():
                continue
            grid = _html_table_grid(str(content))
            if grid:
                blocks.append(
                    TableBlock(
                        markdown=_rows_to_pipe_markdown(grid),
                        rows=grid,
                        header_rows=1,
                        bbox=bbox,
                        source="ocr_glm_layout",
                    )
                )
    return blocks


def _glm_bbox(bbox_2d: Any) -> BBox | None:
    """glm ``bbox_2d [x0,y0,x1,y1]`` → BBox, raster pixels kept verbatim."""
    try:
        x0, y0, x1, y1 = (float(v) for v in bbox_2d)
    except (TypeError, ValueError):
        return None
    return BBox(x0=x0, y0=y0, x1=x1, y1=y1)


class _HTMLTableParser(HTMLParser):
    """First ``<table>`` -> rectangular grid of cell strings.

    Slot-filling with ``rowspan``/``colspan`` (the HTML table model):
    every cell takes the next free slot in its row, spans occupy the
    covered slots. This parser is the single grid source for BOTH
    consumers — the local engines' ``pred_html`` (sparse unordered
    ``logic_points`` cannot rebuild the grid) and glm cloud HTML
    (flat today, span-correct tomorrow). Nested tables are flattened
    into the outer one; engines and glm do not emit them.
    """

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.grid: list[list[str]] = []  # filled by build()
        self._depth = 0
        self._n_rows = 0
        self._row: int | None = None
        self._occupied: set[tuple[int, int]] = set()
        self._placed: dict[tuple[int, int], str] = {}
        self._slot: tuple[int, int] | None = None
        self._buf: list[str] | None = None

    @staticmethod
    def _span_of(attrs: Any) -> tuple[int, int]:
        raw = dict(attrs or {})

        def _int(key: str) -> int:
            try:
                return max(1, int(raw.get(key) or 1))
            except (TypeError, ValueError):
                return 1

        return _int("rowspan"), _int("colspan")

    def handle_starttag(self, tag: str, attrs: Any) -> None:
        if tag == "table":
            self._depth += 1
        elif self._depth != 1:
            return  # outside / nested: ignore
        if tag == "tr":
            self._row = self._n_rows
            self._n_rows += 1
        elif tag in ("td", "th") and self._row is not None:
            rowspan, colspan = self._span_of(attrs)
            r = self._row
            c = 0
            while (r, c) in self._occupied:
                c += 1
            for i in range(rowspan):
                for j in range(colspan):
                    self._occupied.add((r + i, c + j))
            self._slot = (r, c)
            self._buf = []

    def handle_endtag(self, tag: str) -> None:
        if tag in ("td", "th") and self._slot is not None:
            text = " ".join("".join(self._buf or []).split())
            self._placed[self._slot] = text
            self._slot = None
            self._buf = None
        elif tag == "tr":
            self._row = None
        elif tag == "table":
            self._depth = max(0, self._depth - 1)

    def handle_data(self, data: str) -> None:
        if self._buf is not None:
            self._buf.append(data)

    def build(self) -> list[list[str]]:
        if not self._placed:
            return []
        height = max(
            self._n_rows, max(r for r, _c in self._placed) + 1
        )
        width = max(c for _r, c in self._placed) + 1
        return [
            [self._placed.get((r, c), "") for c in range(width)]
            for r in range(height)
        ]


def _html_table_grid(html: str) -> list[list[str]]:
    """First ``<table>`` in ``html`` -> rectangular grid ([] on failure).

    Span-aware: rows/cols follow rowspan/colspan slot-filling, so the
    engine HTML grid (e.g. 21x7 with 52 rowspans on the p14 anchor)
    comes out rectangular and correctly aligned. Malformed cloud HTML
    must not kill the page — parse errors degrade to ``[]``.
    """
    parser = _HTMLTableParser()
    try:
        parser.feed(html or "")
        parser.close()
    except Exception as exc:
        logger.warning("table HTML parse failed: %s", exc)
        return []
    return parser.build()


# ---------------------------------------------------------------------------
# Boxless-engine path: pipe-table parsing of the OCR markdown
# ---------------------------------------------------------------------------


def blocks_from_pipe_markdown(markdown: str) -> list[TableBlock]:
    """Cheapest honest layer for engines without geometry.

    Reuses the native path's pipe-table parser; every hit is re-tagged
    ``ocr_pipe_fallback`` so provenance stays truthful about where the
    markdown came from.
    """
    from pdf_router.adapters.pdf_inspector import _extract_tables_from_markdown

    return [
        replace(tb, source="ocr_pipe_fallback")
        for tb in _extract_tables_from_markdown(markdown or "")
    ]


# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------


def _rows_to_pipe_markdown(rows: list[list[str]]) -> str:
    """Render a 2-D grid as a markdown pipe table (header = first row)."""
    if not rows:
        return ""
    width = max(len(r) for r in rows)

    def _cell(text: str) -> str:
        return str(text).replace("|", "\\|").replace("\n", " ").strip()

    norm = [[_cell(c) for c in r] + [""] * (width - len(r)) for r in rows]
    lines = [
        "| " + " | ".join(norm[0]) + " |",
        "| " + " | ".join("---" for _ in norm[0]) + " |",
    ]
    lines.extend("| " + " | ".join(r) + " |" for r in norm[1:])
    return "\n".join(lines)


def _union_bbox(cell_bboxes: list) -> BBox | None:
    """Union of per-cell boxes (raster pixels), None when unusable.

    The engines return per-cell boxes in varying layouts — flat rects
    ``[x0, y0, x1, y1]``, flat quads ``[x0, y0, ..., x3, y3]`` or point
    lists ``[[x, y], ...]``. All are even-length and interleaved
    x, y, x, y, ..., so even flat indices are x's, odd are y's.
    """
    from numbers import Real

    xs: list[float] = []
    ys: list[float] = []

    def _flat(box: Any) -> list[float]:
        out: list[float] = []
        stack = [box]
        while stack:
            item = stack.pop()
            if isinstance(item, Real):
                out.append(float(item))
            else:
                stack.extend(reversed(list(item)))
        return out

    for b in cell_bboxes or []:
        try:
            flat = _flat(b)
        except (TypeError, ValueError):
            continue
        if not flat or len(flat) % 2:
            continue
        xs.extend(flat[0::2])
        ys.extend(flat[1::2])
    if not xs:
        return None
    return BBox(x0=min(xs), y0=min(ys), x1=max(xs), y1=max(ys))
