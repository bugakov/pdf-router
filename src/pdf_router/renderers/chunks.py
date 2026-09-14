"""Chunks renderer (map v0.7, ADR-0006): hybrid RAG chunks from IR 0.5.

One JSONL envelope for every chunk (``chunk_version: 2``)::

    {"chunk_id": "13-t1", "chunk_version": 2, "type": "table|table_part|text",
     "embed": "...", "markdown": "...",
     "metadata": {"doc_id", "doc_title", "source_format", "page", "section",
                  "src_tool", "error", ["table": {...table chunks only}]},
     "images": [...]}

Contract decisions (tickets TKT-RC-03/04/05, ADR-0006):

- **Hybrid tables**: a table chunk carries context (breadcrumbs + caption),
  the pipe table (``markdown`` — the grid verbatim for a whole table), a
  deterministic facts summary, and a linear key-value ``embed`` text — the
  form embedding models bind to ("Квартал: Q1, Выручка: 150, ...").
- **Context walk-back**: HeadingBlocks are walked in document order (per
  page: geometry-sorted on OCR pages — raster pixels, ascending y0,
  bbox-less blocks keep IR order — and IR order on native pages, where
  blocks are already in reading order). A chunk inherits the nearest
  previous heading across page boundaries. No heading anywhere → first
  ``##``–``####`` heading of the page markdown → doc_title.
- **Caption demotion**: ``rows[0]`` demotes to caption when it holds a
  single content cell (the rest empty), or any of its content cells
  starts with a strong caption prefix (Таблица/Табл./Table — the live
  Рыбы p.14 anchor merges "Таблица 2" INTO a header cell of the
  caption band); key headers then come from ``rows[1]``. "№" is NOT a
  demotion prefix — it leads real headers ("№ п/п") — while a lone
  caption cell demotes via the single-cell rule anyway.
- **Facts summary** (``summary = "facts"``): deterministic grid stats —
  numeric column detection (spaces as thousand separators, comma decimals,
  trailing %), min/max/sum per numeric column, first/last row mention.
  ``"none"`` emits nothing; ``"glm"`` is a reserved seam (config rejects
  it until implemented).
- **Row-split**: the whole table rides as one chunk while its embed fits
  ``max_tokens`` (the budget metric is the embed text); over budget it
  splits by rows, every part repeating the context, the header band and a
  LOCAL facts summary, sharing ``table_id`` (``{page}-t{n}``) with
  ``part``/``parts`` numbering. Rows are atomic — never split mid-row.
- **Subtraction**: text chunks come from ``page.markdown`` (IR doctrine:
  markdown is authoritative). Pipe-table regions are removed from the text
  where the page has TableBlocks: exact-match on native/pipe pages, ANY
  pipe region on OCR pages (the markdown twin there is always the
  scrambled flattening of a block). Pipe tables with no block stay in the
  text verbatim — honest degradation for old IRs / failed structure
  passes. A page whose markdown empties out yields table chunks only, no
  empty text chunk.
- **embed_context**: when on, a preamble line ``doc_title › heading``
  opens the embed text of every chunk and counts against ``max_tokens``.
- **Identity**: ``doc_id`` = 12-hex SHA-256 of the resolved
  ``Metadata.source_path`` (the TKT-RT-12 sidecar schema — chunks and
  image folders share one id); ``doc_title`` = ``Metadata.title`` →
  basename of the source path.

Token counting stays ``len(text) // 4`` — dependency-free, deterministic.
Same IR in, byte-identical JSONL out.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

from pdf_router.config import RendererConfig
from pdf_router.extract_images import doc_id_from_path
from pdf_router.ir import IR, HeadingBlock, PageResult, TableBlock

#: Section headings in page markdown (H2-H4; H1 never opens a section —
#: the PDF-pipeline rule the md renderer and the docx adapter share).
_HEADING_RE = re.compile(r"^(#{2,4})\s+(.+)$", re.MULTILINE)

#: Markdown pipe-table line shapes (mirrors the native adapter's scanner;
#: renderers must not import adapters, so the two regexes live here too).
_TABLE_ROW_RE = re.compile(r"^\s*\|.*\|\s*$")
_TABLE_SEP_RE = re.compile(r"^\s*\|?\s*:?-+:?\s*(\|\s*:?-+:?\s*)+\|?\s*$")

#: Strong caption-row prefixes (case-insensitive): "Таблица 2. ...",
#: "Табл. 5", "Table 3". "№" is deliberately absent: it fronts real
#: headers ("№ п/п"), and a lone caption cell ("№ 4-2") demotes via the
#: single-content-cell rule anyway.
_CAPTION_PREFIXES = ("таблица", "табл.", "табл", "table")

#: Numeric cell after cleanup: optional sign, digits, optional decimals.
_NUM_RE = re.compile(r"^-?\d+(?:\.\d+)?$")

#: Floor for the text budget when a huge preamble would zero it out.
_MIN_TEXT_CHARS = 64


def _tokens(text: str) -> int:
    return len(text) // 4


# ---------------------------------------------------------------------------
# Identity + context walk-back
# ---------------------------------------------------------------------------


def _doc_identity(ir: IR) -> tuple[str | None, str | None]:
    """(doc_id, doc_title) from Metadata (ADR-0006 identity decision)."""
    src = ir.metadata.source_path
    doc_id = doc_id_from_path(src) if src else None
    doc_title = ir.metadata.title or (Path(src).name if src else None)
    return doc_id, doc_title


def _reading_order(page: PageResult) -> list:
    """Blocks in reading order for the context walk.

    OCR-sourced blocks (``source`` starts with ``ocr_``) carry raster-pixel
    bboxes — sort by (y0, x0), top-left origin; blocks without a bbox keep
    their IR order (stable sort). Native pages keep IR order: the pipe
    scanner and the structure tree already emit reading order.
    """
    blocks = list(page.blocks)
    if any(str(getattr(b, "source", "")).startswith("ocr_") for b in blocks):

        def key(b: object) -> tuple[float, float]:
            bb = getattr(b, "bbox", None)
            if bb is None:
                return (float("inf"), float("inf"))
            return (bb.y0, bb.x0)

        blocks.sort(key=key)
    return blocks


class _HeadingStack:
    """Open headings across page boundaries; deepest is ``stack[-1]``."""

    def __init__(self) -> None:
        self._levels: list[tuple[int, str]] = []

    def push(self, level: int, text: str) -> None:
        while self._levels and self._levels[-1][0] >= level:
            self._levels.pop()
        self._levels.append((level, text))

    def deepest(self) -> str | None:
        return self._levels[-1][1] if self._levels else None


# ---------------------------------------------------------------------------
# Table machinery: caption, keys, KV lines, facts summary, pipe rebuild
# ---------------------------------------------------------------------------


def _looks_like_caption_row(row: list[str]) -> bool:
    """RC-04 predicate: single content cell, or a strong caption prefix on
    any content cell (live anchors merge "Таблица 2" into a band cell)."""
    content = [c.strip() for c in row if c and c.strip()]
    if not content:
        return False
    if len(content) == 1:
        return True
    return any(c.lower().startswith(_CAPTION_PREFIXES) for c in content)


def _column_keys(band: list[list[str]], n_cols: int) -> list[str]:
    """Key labels per column from the header band (multi-row joined "/");
    positional fallback when the grid has no usable header."""
    keys: list[str] = []
    for ci in range(n_cols):
        parts = [r[ci].strip() for r in band if ci < len(r) and r[ci].strip()]
        keys.append(" / ".join(parts))
    return [k if k else f"Колонка {i + 1}" for i, k in enumerate(keys)]


def _kv_lines(keys: list[str], rows: list[list[str]]) -> list[str]:
    """Linear key-value lines, one per data row ("Ключ: значение, ...")."""
    out: list[str] = []
    for row in rows:
        pairs = []
        for ci, cell in enumerate(row):
            key = keys[ci] if ci < len(keys) else ""
            val = cell.strip()
            if key and val:
                pairs.append(f"{key}: {val}")
        if pairs:
            out.append(", ".join(pairs))
    return out


def _parse_num(cell: str) -> float | None:
    """Cyrillic-formatted number: "1 234,56" / "45%" / "-3,1" → float."""
    s = cell.strip().replace("\u00a0", "").replace(" ", "")
    if s.endswith("%"):
        s = s[:-1]
    s = s.replace(",", ".")
    if not s or not _NUM_RE.match(s):
        return None
    return float(s)


def _fmt_num(x: float) -> str:
    if abs(x - round(x)) < 1e-9:
        return str(round(x))
    return f"{x:.2f}".rstrip("0").rstrip(".")


def _facts_summary(keys: list[str], rows: list[list[str]], n_cols: int) -> str:
    """Deterministic grid stats (summary="facts"). Pure — no randomness."""
    if not rows:
        return f"Сводка: {n_cols} кол., 0 строк данных."

    def cell(r: list[str], ci: int) -> str:
        return r[ci].strip() if ci < len(r) else ""

    stats: dict[int, list[float]] = {}
    for ci in range(n_cols):
        cells = [cell(r, ci) for r in rows]
        parsed = [v for c in cells if c and (v := _parse_num(c)) is not None]
        if parsed and len(parsed) * 2 >= len(cells):
            stats[ci] = parsed

    parts = [f"Сводка: {n_cols} кол., {len(rows)} строк данных."]
    for ci, vals in stats.items():
        label = keys[ci] if ci < len(keys) else f"Колонка {ci + 1}"
        parts.append(
            f"{label}: мин {_fmt_num(min(vals))}, "
            f"макс {_fmt_num(max(vals))}, сумма {_fmt_num(sum(vals))}."
        )
    first = ", ".join(c for c in (cell(rows[0], ci) for ci in range(n_cols)) if c)
    if first:
        parts.append(f"Первая строка: {first}.")
    if len(rows) > 1:
        last = ", ".join(c for c in (cell(rows[-1], ci) for ci in range(n_cols)) if c)
        if last:
            parts.append(f"Последняя строка: {last}.")
    return " ".join(parts)


def _rows_to_pipe(band: list[list[str]], data: list[list[str]]) -> str:
    """Rebuild a pipe table for one split part: header band, separator, rows."""
    width = max([len(r) for r in band + data] or [1])

    def _cell(text: str) -> str:
        return text.replace("|", "\\|").replace("\n", " ").strip()

    def _line(row: list[str]) -> str:
        norm = [_cell(c) for c in row] + [""] * (width - len(row))
        return "| " + " | ".join(norm) + " |"

    lines = [_line(r) for r in band]
    lines.append("| " + " | ".join("---" for _ in range(width)) + " |")
    lines.extend(_line(r) for r in data)
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Pipe-table subtraction (text half)
# ---------------------------------------------------------------------------


def _pipe_regions(markdown: str) -> list[tuple[int, int, str]]:
    """(start_line, end_line_excl, text) of markdown pipe-table regions."""
    lines = markdown.splitlines()
    regions: list[tuple[int, int, str]] = []
    i, n = 0, len(lines)
    while i < n:
        if _TABLE_ROW_RE.match(lines[i] or "") and i + 1 < n and _TABLE_SEP_RE.match(
            lines[i + 1] or ""
        ):
            j = i + 2
            while j < n and _TABLE_ROW_RE.match(lines[j] or ""):
                j += 1
            regions.append((i, j, "\n".join(lines[i:j])))
            i = j
        else:
            i += 1
    return regions


def _norm_block(text: str) -> str:
    return "\n".join(ln.strip() for ln in text.strip().splitlines())


def _strip_tables(markdown: str, page: PageResult) -> str:
    """Remove pipe-table regions the blocks already cover (RC-05).

    OCR pages: ANY pipe region is the scrambled twin of a block — drop all.
    Native/pipe pages: exact (whitespace-normalized) match against
    ``TableBlock.markdown``. Unmatched regions stay — honest degradation.
    """
    if not markdown.strip():
        return markdown
    tables = [b for b in page.blocks if isinstance(b, TableBlock)]
    if not tables:
        return markdown
    ocr_page = any(str(getattr(b, "source", "")).startswith("ocr_") for b in tables)
    known = {_norm_block(b.markdown) for b in tables}

    lines = markdown.splitlines()
    drop: set[int] = set()
    for start, end, text in _pipe_regions(markdown):
        if ocr_page or _norm_block(text) in known:
            drop.update(range(start, end))
    if not drop:
        return markdown
    kept = [ln for idx, ln in enumerate(lines) if idx not in drop]
    return "\n".join(kept)


def _split_blocks(markdown: str) -> list[tuple[str | None, str]]:
    """Split page markdown into (section, text) blocks.

    Section = the most recent ``##``–``####`` heading on the page, or None.
    Headings start a new block and stay as its first line.
    """
    if not markdown.strip():
        return []

    lines = markdown.splitlines()
    blocks: list[tuple[str | None, str]] = []
    current_section: str | None = None
    current_lines: list[str] = []

    def flush() -> None:
        text = "\n".join(current_lines).strip()
        if text:
            blocks.append((current_section, text))
        current_lines.clear()

    for line in lines:
        m = _HEADING_RE.match(line)
        if m:
            flush()
            current_section = m.group(2).strip()
            current_lines.append(line)
        elif not line.strip():
            flush()
        else:
            current_lines.append(line)
    flush()
    return blocks


def _split_long(text: str, max_chars: int, overlap_chars: int) -> list[str]:
    """Hard-split an over-budget block; overlap re-includes the tail."""
    if len(text) <= max_chars:
        return [text]
    overlap_chars = min(overlap_chars, max_chars - 1)
    pieces: list[str] = []
    start = 0
    while start < len(text):
        piece = text[start : start + max_chars].strip()
        if piece:
            pieces.append(piece)
        if start + max_chars >= len(text):
            break
        start += max_chars - overlap_chars
    return pieces or [text]


# ---------------------------------------------------------------------------
# Table chunks
# ---------------------------------------------------------------------------


def _table_parts(
    page: PageResult,
    block: TableBlock,
    table_no: int,
    *,
    doc_id: str | None,
    doc_title: str | None,
    heading: str | None,
    section: str | None,
    source_format: str,
    cfg: RendererConfig,
) -> list[dict]:
    """Hybrid chunks for one TableBlock (whole table or row-split parts)."""
    ccfg = cfg.chunks
    rows = block.rows
    n_cols = max((len(r) for r in rows), default=0)
    if not rows or not n_cols:
        return []

    # --- caption demotion + key header band (RC-04 rule) ----------------
    caption: str | None = None
    if len(rows) >= 2 and _looks_like_caption_row(rows[0]):
        caption = " ".join(c.strip() for c in rows[0] if c.strip())
        band = rows[1:2]  # the real header: first row after the caption
        data = rows[2:]
    else:
        band = rows[: max(0, min(block.header_rows, len(rows)))]
        data = rows[len(band):]
    keys = _column_keys(band, n_cols)

    # --- shared context lines (repeated verbatim in every part) ---------
    crumbs = " › ".join(p for p in (doc_title, heading) if p)
    head: list[str] = []
    if ccfg.embed_context and crumbs:
        head.append(crumbs)
    if caption:
        head.append(caption)

    def embed_of(part_rows: list[list[str]]) -> str:
        pieces = list(head)
        pieces.extend(_kv_lines(keys, part_rows))
        if ccfg.summary == "facts":
            pieces.append(_facts_summary(keys, part_rows, n_cols))
        return "\n".join(pieces)

    def md_of(part_rows: list[list[str]]) -> str:
        title_line = heading or caption
        out: list[str] = []
        if title_line:
            out.append(f"# {title_line}")
        if caption and caption != title_line:
            out.append(caption)
        if caption:
            out.append(_rows_to_pipe(band, part_rows))
        else:
            out.append(block.markdown)  # verbatim grid, IR doctrine
        if ccfg.summary == "facts":
            out.append(_facts_summary(keys, part_rows, n_cols))
        return "\n".join(ln for ln in out if ln)

    def meta(part: int, parts: int) -> dict:
        return {
            "doc_id": doc_id,
            "doc_title": doc_title,
            "source_format": source_format,
            "page": page.page,
            "section": section,
            "src_tool": page.src_tool.value,
            "error": page.error,
            "table": {
                "table_id": f"{page.page}-t{table_no}",
                "part": part,
                "parts": parts,
                "n_rows": len(rows),
                "n_cols": n_cols,
                "header": keys,
                "source": block.source,
            },
        }

    # --- split policy: whole while the embed fits, else row parts -------
    whole_embed = embed_of(data)
    groups: list[list[list[str]]]
    if not data or _tokens(whole_embed) <= ccfg.max_tokens:
        groups = [data]
    else:
        groups = []
        current: list[list[str]] = []
        for row in data:
            if current and _tokens(embed_of([*current, row])) > ccfg.max_tokens:
                groups.append(current)
                current = [row]
            else:
                current = [*current, row]
        if current:
            groups.append(current)

    table_id = f"{page.page}-t{table_no}"
    single = len(groups) == 1
    out: list[dict] = []
    for k, part_rows in enumerate(groups, start=1):
        out.append(
            {
                "chunk_id": table_id if single else f"{table_id}-{k:02d}",
                "chunk_version": 2,
                "type": "table" if single else "table_part",
                "embed": embed_of(part_rows),
                "markdown": md_of(part_rows),
                "metadata": meta(1 if single else k, len(groups)),
                "images": list(page.images),
            }
        )
    return out


# ---------------------------------------------------------------------------
# Renderer
# ---------------------------------------------------------------------------


def render(ir: IR, *, config: RendererConfig) -> str:
    """Render the IR to JSONL: one hybrid chunk per line (envelope v2)."""
    doc_id, doc_title = _doc_identity(ir)
    stack = _HeadingStack()
    lines: list[str] = []
    source_format = ir.metadata.source_format

    for page in ir.pages:
        ordered = _reading_order(page)
        md_headings = _HEADING_RE.findall(page.markdown or "")
        fallback_section = md_headings[0][1].strip() if md_headings else None

        # --- walk blocks: heading stack + snapshot table contexts --------
        table_jobs: list[tuple[TableBlock, str | None, str | None, int]] = []
        table_no = 0
        for block in ordered:
            if isinstance(block, HeadingBlock):
                stack.push(block.level, block.text)
            elif isinstance(block, TableBlock):
                table_no += 1
                snap = stack.deepest()
                table_jobs.append(
                    (block, snap, snap or fallback_section, table_no)
                )

        for block, heading, section, no in table_jobs:
            for chunk in _table_parts(
                page,
                block,
                no,
                doc_id=doc_id,
                doc_title=doc_title,
                heading=heading,
                section=section,
                source_format=source_format,
                cfg=config,
            ):
                lines.append(json.dumps(chunk, ensure_ascii=False))

        # --- text half: markdown minus subtracted pipe regions -----------
        remaining = _strip_tables(page.markdown or "", page)
        max_chars_full = config.chunks.max_tokens * 4
        overlap_chars = config.chunks.overlap_tokens * 4

        seq = 0
        for section, text in _split_blocks(remaining):
            # preamble per block: doc_title › (block section, else the
            # walk-back stack) — the fallback chain of the contract
            preamble = ""
            if config.chunks.embed_context:
                preamble = " › ".join(
                    p for p in (doc_title, section or stack.deepest()) if p
                )
            max_chars = max_chars_full
            if preamble:
                max_chars = max(max_chars - len(preamble) - 1, _MIN_TEXT_CHARS)
            for piece in _split_long(text, max_chars, overlap_chars):
                seq += 1
                embed = f"{preamble}\n{piece}" if preamble else piece
                lines.append(
                    json.dumps(
                        {
                            "chunk_id": f"{page.page}-{seq:03d}",
                            "chunk_version": 2,
                            "type": "text",
                            "embed": embed,
                            "markdown": piece,
                            "metadata": {
                                "doc_id": doc_id,
                                "doc_title": doc_title,
                                "source_format": source_format,
                                "page": page.page,
                                "section": section,
                                "src_tool": page.src_tool.value,
                                "error": page.error,
                            },
                            "images": list(page.images),
                        },
                        ensure_ascii=False,
                    )
                )

    return "\n".join(lines) + ("\n" if lines else "")
