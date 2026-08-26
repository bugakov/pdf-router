"""GLM-OCR cloud adapter (Z.ai ``layout_parsing``). Opt-in, HTTP, never free of keys.

Renders the page to PNG (200 DPI) and sends it to the configured base_url via
the ``zai`` SDK. Written from scratch against the ``ocr_page`` interface;
the old ``parser_choose`` script is reference only. If ``zai`` cannot be
imported, the module still imports and :meth:`AdapterGlmOcr.available`
returns ``False`` (module-level guard per ticket 07).
"""

from __future__ import annotations

import base64
import io
import logging
from typing import Any, ClassVar

logger = logging.getLogger("pdf_router")

try:  # module-level guard: [cloud] extra not installed → clean unavailability
    from zai import ZhipuAiClient  # type: ignore

    _ZAI_IMPORT_ERROR: Exception | None = None
except Exception as _exc:  # pragma: no cover - depends on extras
    ZhipuAiClient = None  # type: ignore[assignment]
    _ZAI_IMPORT_ERROR = _exc


class GlmOcrError(RuntimeError):
    """Typed failure of the GLM-OCR path."""


def _render_page_png(pdf_path: str, page_0indexed: int, dpi: int = 200) -> bytes:
    import pymupdf

    doc = pymupdf.open(pdf_path)
    try:
        if not 0 <= page_0indexed < doc.page_count:
            raise GlmOcrError(f"page index {page_0indexed} out of range (0..{doc.page_count - 1})")
        zoom = dpi / 72.0
        return doc[page_0indexed].get_pixmap(matrix=pymupdf.Matrix(zoom, zoom), alpha=False).tobytes(output="png")
    finally:
        doc.close()


class AdapterGlmOcr:
    name: ClassVar[str] = "glm_ocr"

    DEFAULT_BASE_URL = "https://api.z.ai/api/paas/v4"
    DEFAULT_MODEL = "glm-ocr"
    DEFAULT_TIMEOUT = 60.0

    def __init__(self, params: dict[str, Any] | None = None) -> None:
        self.params = dict(params or {})
        self.api_key: str | None = self.params.get("api_key")
        self.base_url: str = self.params.get("base_url") or self.DEFAULT_BASE_URL
        self.model: str = self.params.get("model") or self.DEFAULT_MODEL
        self.timeout: float = float(self.params.get("timeout", self.DEFAULT_TIMEOUT))
        self.dpi: int = int(self.params.get("dpi", 200))

    def available(self) -> bool:
        if not self.api_key:
            return False
        if ZhipuAiClient is None:
            logger.debug("zai SDK not importable: %s", _ZAI_IMPORT_ERROR)
            return False
        return True

    def ocr_page(self, pdf_path: str, page_0indexed: int) -> str:
        if not self.available():
            raise GlmOcrError("GLM-OCR unavailable (api_key missing or zai not importable)")
        png = _render_page_png(pdf_path, page_0indexed, self.dpi)
        data_url = "data:image/png;base64," + base64.b64encode(png).decode("ascii")
        client = ZhipuAiClient(api_key=self.api_key, base_url=self.base_url)
        try:
            resp = client.layout_parsing.create(model=self.model, file=data_url).model_dump()
        except Exception as exc:
            raise GlmOcrError(f"layout_parsing call failed: {exc}") from exc

        pages = resp.get("layout_details") or []
        if not pages:
            raise GlmOcrError("no layout_details in GLM-OCR response")
        blocks = pages[0] if isinstance(pages[0], list) else pages
        parts = [
            (b.get("content") or "").strip()
            for b in blocks
            if isinstance(b, dict) and (b.get("content") or "").strip()
        ]
        md = "\n\n".join(parts).strip()
        if not md:
            raise GlmOcrError(f"page {page_0indexed + 1}: empty GLM-OCR result")
        return md
