"""Markdown renderer: concatenated per-page markdown, optional page markers.

Pure function — no I/O. DESIGN §7.
"""

from __future__ import annotations

from pdf_router.config import RendererConfig
from pdf_router.ir import IR


def render(ir: IR, *, config: RendererConfig) -> str:
    """Concatenate ``pages[].markdown`` with blank lines; optional markers.

    Page markers are ``<!-- page N -->`` with N **1-indexed**, matching the
    ``metadata.pages_with_*`` convention (DESIGN §7).
    """
    parts: list[str] = []
    for page in ir.pages:
        if config.page_markers:
            parts.append(f"<!-- page {page.page + 1} -->")
        parts.append(page.markdown)
    return "\n\n".join(p for p in parts if p is not None).strip() + "\n"
