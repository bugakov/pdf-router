"""JSON renderer: serialized IR, optional page_bbox. Pure function. DESIGN §7."""

from __future__ import annotations

import json

from pdf_router.config import RendererConfig
from pdf_router.ir import IR, to_dict


def render(ir: IR, *, config: RendererConfig) -> str:
    d = to_dict(ir, include_page_bbox=config.json_include_page_bbox)
    return json.dumps(d, indent=2, ensure_ascii=False) + "\n"
