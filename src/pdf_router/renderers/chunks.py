"""Chunks renderer: JSONL of heading/paragraph chunks with token budget.

Pure function — no I/O. DESIGN §7.

Token counting uses ``len(text) // 4`` — a deliberately rough heuristic
(documented here) that keeps the renderer dependency-free and deterministic.
Chunks longer than ``max_tokens`` are hard-split with ``overlap_tokens``
worth of tail text re-included at the start of the continuation.
"""

from __future__ import annotations

import json
import re

from pdf_router.config import RendererConfig
from pdf_router.ir import IR, PageResult

_HEADING_RE = re.compile(r"^(#{2,4})\s+(.+)$", re.MULTILINE)


def _split_blocks(markdown: str) -> list[tuple[str | None, str]]:
    """Split page markdown into (section, text) blocks.

    Section = the most recent ``##``–``####`` heading on the page, or None.
    Headings themselves start a new block and are kept as its first line.
    """
    if not markdown.strip():
        return []

    lines = markdown.splitlines()
    blocks: list[tuple[str | None, list[str]]] = []
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


def _tokens(text: str) -> int:
    return len(text) // 4


def _split_long(text: str, max_tokens: int, overlap_tokens: int) -> list[str]:
    """Hard-split an over-budget block; overlap re-includes the previous tail."""
    if _tokens(text) <= max_tokens:
        return [text]
    max_chars = max_tokens * 4
    overlap_chars = min(overlap_tokens * 4, max_chars - 1)
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


def render(ir: IR, *, config: RendererConfig) -> str:
    """Render the IR to JSONL: one chunk per line."""
    lines: list[str] = []
    chunk_counter = 0
    for page in ir.pages:
        for section, text in _split_blocks(page.markdown):
            for piece in _split_long(text, config.chunks.max_tokens, config.chunks.overlap_tokens):
                chunk_counter += 1
                lines.append(
                    json.dumps(
                        {
                            "chunk_id": f"{page.page}-{chunk_counter}",
                            "text": piece,
                            "page": page.page,
                            "section": section,
                            "src_tool": page.src_tool.value,
                            "images": list(page.images),  # relative paths from output_dir
                        },
                        ensure_ascii=False,
                    )
                )
    return "\n".join(lines) + ("\n" if lines else "")
