"""zvec renderer (ADR-0010): flat, strongly-typed hybrid vector records for zvec 0.7.0+.

Produces records matching the zvec collection schema:
- Separates `embed_text` (preamble + KV text) from `content` (authoritative Markdown).
- Preserves geometry (`page_num` 1-indexed, `bbox` as JSON string `[x0, y0, x1, y1]`).
- Hierarchical context (`section_path` breadcrumbs, `section_title`).
- Structured table facts (`table_facts` as JSON string).
- Blueprints and visual illustrations (`chunk_type="drawing"` | `"image"`, `image_url`, `caption`, `image_meta`).
- Deterministic IDs: `{doc_uid}_p{page_num:02d}_{chunk_type}{seq:02d}`.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Iterator

from pdf_router.config import RendererConfig
from pdf_router.ir import FigureBlock, HeadingBlock, IR, TableBlock
from pdf_router.renderers.chunks import (
    _HEADING_RE,
    _MIN_TEXT_CHARS,
    _HeadingStack,
    _column_keys,
    _doc_identity,
    _facts_summary,
    _kv_lines,
    _looks_like_caption_row,
    _parse_num,
    _reading_order,
    _rows_to_pipe,
    _split_blocks,
    _split_long,
    _strip_tables,
    _tokens,
)

_DRAWING_KEYWORDS = (
    "чертеж", "чертёж", "схема", "план", "разрез", "узел",
    "фасад", "генплан", "профиль", "аксонометрия", "трасса",
)


class _ZvecHeadingStack(_HeadingStack):
    """Open headings across page boundaries with hierarchical path support."""

    def path(self) -> str:
        return " > ".join(text for _, text in self._levels)


def _format_bbox(bbox: Any) -> str:
    if bbox is None:
        return "[]"
    return json.dumps([round(bbox.x0, 2), round(bbox.y0, 2), round(bbox.x1, 2), round(bbox.y1, 2)])


def _table_facts_json(keys: list[str], rows: list[list[str]], n_cols: int) -> str:
    """Compute structured numeric statistics per column as JSON string."""
    if not rows:
        return json.dumps({"n_cols": n_cols, "n_rows": 0, "cols": {}}, ensure_ascii=False)

    stats: dict[str, dict[str, float]] = {}
    for ci in range(n_cols):
        cells = [r[ci].strip() for r in rows if ci < len(r)]
        parsed = [v for c in cells if c and (v := _parse_num(c)) is not None]
        if parsed and len(parsed) * 2 >= len(cells):
            label = keys[ci] if ci < len(keys) else f"Колонка {ci + 1}"
            stats[label] = {
                "min": min(parsed),
                "max": max(parsed),
                "sum": round(sum(parsed), 4),
            }

    data = {
        "n_cols": n_cols,
        "n_rows": len(rows),
        "cols": stats,
    }
    return json.dumps(data, ensure_ascii=False)


def iter_records(ir: IR, *, config: RendererConfig) -> Iterator[dict[str, Any]]:
    """Yield strongly-typed chunk dictionaries from an IR for direct zvec ingestion."""
    zcfg = getattr(config, "zvec", None)
    ccfg = getattr(config, "chunks", None)

    max_tokens = getattr(zcfg, "max_tokens", None) or getattr(ccfg, "max_tokens", 512)
    overlap_tokens = getattr(zcfg, "overlap_tokens", None) or getattr(ccfg, "overlap_tokens", 64)
    summary_mode = getattr(zcfg, "summary", None) or getattr(ccfg, "summary", "facts")
    embed_context = (
        getattr(zcfg, "embed_context", True)
        if zcfg is not None
        else getattr(ccfg, "embed_context", True)
    )

    doc_id, doc_title = _doc_identity(ir)
    doc_uid = (getattr(zcfg, "doc_uid", None) or doc_id or "doc").strip()
    project_uid = (getattr(zcfg, "project_uid", None) or "").strip()

    file_path = ir.metadata.source_path or ""
    doc_name = ir.metadata.title or (Path(file_path).name if file_path else "") or (doc_title or "")
    source_format = ir.metadata.source_format

    stack = _ZvecHeadingStack()
    chunk_index = 0

    for page in ir.pages:
        page_num = page.page + 1
        ordered = _reading_order(page)
        md_headings = _HEADING_RE.findall(page.markdown or "")
        fallback_section = md_headings[0][1].strip() if md_headings else None

        table_jobs: list[tuple[TableBlock, str | None, str, str | None, int]] = []
        figure_jobs: list[tuple[FigureBlock, str | None, str, str | None, int]] = []
        table_no = 0
        fig_no = 0

        for block in ordered:
            if isinstance(block, HeadingBlock):
                stack.push(block.level, block.text)
            elif isinstance(block, TableBlock):
                table_no += 1
                snap_title = stack.deepest()
                snap_path = stack.path()
                table_jobs.append((
                    block,
                    snap_title,
                    snap_path or fallback_section or "",
                    snap_title or fallback_section,
                    table_no,
                ))
            elif isinstance(block, FigureBlock):
                fig_no += 1
                snap_title = stack.deepest()
                snap_path = stack.path()
                figure_jobs.append((
                    block,
                    snap_title,
                    snap_path or fallback_section or "",
                    snap_title or fallback_section,
                    fig_no,
                ))

        # --- Process Table Blocks ---
        for block, heading, section_path, section_title, no in table_jobs:
            rows = block.rows
            n_cols = max((len(r) for r in rows), default=0)
            if not rows or not n_cols:
                continue

            caption: str | None = None
            if len(rows) >= 2 and _looks_like_caption_row(rows[0]):
                caption = " ".join(c.strip() for c in rows[0] if c.strip())
                band = rows[1:2]
                data = rows[2:]
            else:
                band = rows[: max(0, min(block.header_rows, len(rows)))]
                data = rows[len(band):]
            keys = _column_keys(band, n_cols)

            crumbs = " › ".join(p for p in (doc_name, section_path or section_title) if p)
            head: list[str] = []
            if embed_context and crumbs:
                head.append(crumbs)
            if caption:
                head.append(caption)

            def embed_of(part_rows: list[list[str]]) -> str:
                pieces = list(head)
                pieces.extend(_kv_lines(keys, part_rows))
                if summary_mode == "facts":
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
                    out.append(block.markdown)
                if summary_mode == "facts":
                    out.append(_facts_summary(keys, part_rows, n_cols))
                return "\n".join(ln for ln in out if ln)

            whole_embed = embed_of(data)
            groups: list[list[list[str]]]
            if not data or (max_tokens is not None and _tokens(whole_embed) <= max_tokens):
                groups = [data]
            else:
                groups = []
                current: list[list[str]] = []
                for row in data:
                    if current and max_tokens is not None and _tokens(embed_of([*current, row])) > max_tokens:
                        groups.append(current)
                        current = [row]
                    else:
                        current = [*current, row]
                if current:
                    groups.append(current)

            single = len(groups) == 1
            bbox_str = _format_bbox(block.bbox)

            for k, part_rows in enumerate(groups, start=1):
                part_type = "table" if single else "table_part"
                part_id = (
                    f"{doc_uid}_p{page_num:02d}_t{no:02d}"
                    if single
                    else f"{doc_uid}_p{page_num:02d}_t{no:02d}_pt{k:02d}"
                )
                table_facts = (
                    _table_facts_json(keys, part_rows, n_cols)
                    if summary_mode == "facts"
                    else ""
                )

                yield {
                    "id": part_id,
                    "embed_text": embed_of(part_rows),
                    "fields": {
                        "project_uid": project_uid,
                        "doc_uid": doc_uid,
                        "doc_name": doc_name,
                        "chunk_type": part_type,
                        "chunk_index": chunk_index,
                        "page_num": page_num,
                        "bbox": bbox_str,
                        "section_path": section_path,
                        "section_title": section_title or "",
                        "content": md_of(part_rows),
                        "table_facts": table_facts,
                        "file_path": file_path,
                        "image_url": "",
                        "caption": caption or "",
                        "image_meta": "",
                    },
                    "metadata": {
                        "source_format": source_format,
                        "src_tool": (
                            page.src_tool.value
                            if hasattr(page.src_tool, "value")
                            else str(page.src_tool)
                        ),
                        "error": page.error,
                        "images": list(page.images),
                    },
                }
                chunk_index += 1

        # --- Process Figure / Drawing Blocks ---
        for fig_block, snap_title, snap_path, snap_fallback_title, no in figure_jobs:
            cap = fig_block.caption or snap_title or ""
            is_draw = any(kw in cap.lower() for kw in _DRAWING_KEYWORDS) or (
                fig_block.image_index is not None
                and fig_block.image_index < len(page.images)
                and "vector" in page.images[fig_block.image_index].lower()
            )
            fig_type = "drawing" if is_draw else "image"
            prefix = "draw" if is_draw else "img"
            fig_id = f"{doc_uid}_p{page_num:02d}_{prefix}{no:02d}"

            img_path = ""
            if fig_block.image_index is not None and fig_block.image_index < len(page.images):
                img_path = page.images[fig_block.image_index]
            elif page.images:
                img_path = page.images[0]

            crumbs = " › ".join(p for p in (doc_name, snap_path or snap_fallback_title) if p)
            tag = "Чертёж / Схема" if is_draw else "Иллюстрация"
            embed_lines: list[str] = []
            if embed_context and crumbs:
                embed_lines.append(crumbs)
            if cap:
                embed_lines.append(f"[{tag}]: {cap}")
            else:
                embed_lines.append(f"[{tag}] (Стр. {page_num})")

            content_lines: list[str] = []
            if img_path:
                content_lines.append(f"![{cap or tag}]({img_path})")
            if cap:
                content_lines.append(f"**{cap}**")

            img_meta = json.dumps({
                "kind": fig_type,
                "page": page_num,
                "has_bbox": fig_block.bbox is not None,
            }, ensure_ascii=False)

            yield {
                "id": fig_id,
                "embed_text": "\n".join(embed_lines),
                "fields": {
                    "project_uid": project_uid,
                    "doc_uid": doc_uid,
                    "doc_name": doc_name,
                    "chunk_type": fig_type,
                    "chunk_index": chunk_index,
                    "page_num": page_num,
                    "bbox": _format_bbox(fig_block.bbox),
                    "section_path": snap_path,
                    "section_title": snap_fallback_title or "",
                    "content": "\n\n".join(content_lines) or f"[{tag}]",
                    "table_facts": "",
                    "file_path": file_path,
                    "image_url": img_path,
                    "caption": cap,
                    "image_meta": img_meta,
                },
                "metadata": {
                    "source_format": source_format,
                    "src_tool": (
                        page.src_tool.value
                        if hasattr(page.src_tool, "value")
                        else str(page.src_tool)
                    ),
                    "error": page.error,
                    "images": list(page.images),
                },
            }
            chunk_index += 1

        # If no FigureBlocks were found, but page.images has items (e.g. extracted sidecars):
        if not figure_jobs and page.images:
            for img_idx, img_p in enumerate(page.images, start=1):
                is_draw = "vector" in img_p.lower()
                fig_type = "drawing" if is_draw else "image"
                tag = "Чертёж / Схема" if is_draw else "Иллюстрация"
                cap = stack.deepest() or fallback_section or ""
                crumbs = " › ".join(p for p in (doc_name, stack.path() or cap) if p)
                embed_lines = []
                if embed_context and crumbs:
                    embed_lines.append(crumbs)
                if cap:
                    embed_lines.append(f"[{tag}]: {cap} (Стр. {page_num})")
                else:
                    embed_lines.append(f"[{tag}] (Стр. {page_num})")

                prefix = "draw" if is_draw else "img"
                fig_id = f"{doc_uid}_p{page_num:02d}_{prefix}{img_idx:02d}"
                img_meta = json.dumps({
                    "kind": fig_type,
                    "page": page_num,
                    "has_bbox": False,
                }, ensure_ascii=False)

                yield {
                    "id": fig_id,
                    "embed_text": "\n".join(embed_lines),
                    "fields": {
                        "project_uid": project_uid,
                        "doc_uid": doc_uid,
                        "doc_name": doc_name,
                        "chunk_type": fig_type,
                        "chunk_index": chunk_index,
                        "page_num": page_num,
                        "bbox": "[]",
                        "section_path": stack.path() or fallback_section or "",
                        "section_title": cap,
                        "content": f"![{cap or tag}]({img_p})\n\n**{cap}**" if cap else f"![{tag}]({img_p})",
                        "table_facts": "",
                        "file_path": file_path,
                        "image_url": img_p,
                        "caption": cap,
                        "image_meta": img_meta,
                    },
                    "metadata": {
                        "source_format": source_format,
                        "src_tool": (
                            page.src_tool.value
                            if hasattr(page.src_tool, "value")
                            else str(page.src_tool)
                        ),
                        "error": page.error,
                        "images": list(page.images),
                    },
                }
                chunk_index += 1

        # --- Process Text Half (Markdown minus subtracted tables) ---
        remaining = _strip_tables(page.markdown or "", page)
        max_chars_full = (max_tokens or 512) * 4
        overlap_chars = (overlap_tokens or 64) * 4

        seq = 0
        for section, text in _split_blocks(remaining):
            effective_section_path = stack.path() or section or fallback_section or ""
            effective_section_title = section or stack.deepest() or fallback_section or ""
            preamble = ""
            if embed_context:
                preamble = " › ".join(
                    p
                    for p in (doc_name, effective_section_path or effective_section_title)
                    if p
                )
            max_chars = max_chars_full
            if preamble:
                max_chars = max(max_chars - len(preamble) - 1, _MIN_TEXT_CHARS)

            for piece in _split_long(text, max_chars, overlap_chars):
                seq += 1
                embed_text = f"{preamble}\n{piece}" if preamble else piece
                chunk_id = f"{doc_uid}_p{page_num:02d}_text{seq:02d}"

                yield {
                    "id": chunk_id,
                    "embed_text": embed_text,
                    "fields": {
                        "project_uid": project_uid,
                        "doc_uid": doc_uid,
                        "doc_name": doc_name,
                        "chunk_type": "text",
                        "chunk_index": chunk_index,
                        "page_num": page_num,
                        "bbox": "[]",
                        "section_path": effective_section_path,
                        "section_title": effective_section_title,
                        "content": piece,
                        "table_facts": "",
                        "file_path": file_path,
                        "image_url": "",
                        "caption": "",
                        "image_meta": "",
                    },
                    "metadata": {
                        "source_format": source_format,
                        "src_tool": (
                            page.src_tool.value
                            if hasattr(page.src_tool, "value")
                            else str(page.src_tool)
                        ),
                        "error": page.error,
                        "images": list(page.images),
                    },
                }
                chunk_index += 1


def render(ir: IR, *, config: RendererConfig) -> str:
    """Render the IR to JSONL for zvec ingestion (one JSON record per line)."""
    lines = [
        json.dumps(record, ensure_ascii=False)
        for record in iter_records(ir, config=config)
    ]
    return "\n".join(lines) + ("\n" if lines else "")
