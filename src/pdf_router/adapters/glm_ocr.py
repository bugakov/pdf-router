"""GLM-OCR adapter (Z.ai dedicated ``glm-ocr`` model via ``layout_parsing``). Opt-in, HTTP.

Renders the requested page to a **single-page PDF** (PyMuPDF ``insert_pdf``)
and POSTs it inline as a base64 data-URL to ``{base_url}/layout_parsing``.
The dedicated ``glm-ocr`` model returns ready markdown in ``md_results``
plus ``layout_details`` (bbox blocks) and HTML tables — cheap and
deterministic, unlike general vision models.

Empirical notes (live-tested 2026-08-27):
- Endpoint: standard ``https://api.z.ai/api/paas/v4/layout_parsing``.
  A **coding-plan key** gets 429 "Insufficient balance" there — document
  parsing is billed separately. Use a key with the document/resource
  package (e.g. ``ZAI_API_KEY`` from the project env).
- ``md_results`` is a plain string (whole-document markdown); for a
  single-page PDF it maps 1:1 to the requested page.
- Whole-file PDF and single-page PDF both work; per-page keeps the
  router's ``ocr_page`` interface intact and costs ~1.8k tokens/page.
- Known model limitation: Cyrillic in dense stamps gets garbled
  («Страптельство» instead of «Строительство») — documented in
  parser_choose README; fine for body text, verify stamps.

``requests`` lives in the ``[cloud]`` extra; if missing, the module still
imports and :meth:`AdapterGlmOcr.available` returns ``False``.
"""

from __future__ import annotations

import base64
import logging
from typing import Any, ClassVar

logger = logging.getLogger("pdf_router")

try:  # module-level guard: [cloud] extra not installed → clean unavailability
    import requests as _requests

    _REQUESTS_IMPORT_ERROR: Exception | None = None
except Exception as _exc:  # pragma: no cover - depends on extras
    _requests = None  # type: ignore[assignment]
    _REQUESTS_IMPORT_ERROR = _exc


class GlmOcrError(RuntimeError):
    """Typed failure of the GLM-OCR path."""


class AdapterGlmOcr:
    name: ClassVar[str] = "glm_ocr"

    DEFAULT_BASE_URL = "https://api.z.ai/api/paas/v4"
    DEFAULT_MODEL = "glm-ocr"
    DEFAULT_TIMEOUT = 300.0

    def __init__(self, params: dict[str, Any] | None = None) -> None:
        self.params = dict(params or {})
        self.api_key: str | None = self.params.get("api_key")
        self.base_url: str = self.params.get("base_url") or self.DEFAULT_BASE_URL
        self.model: str = self.params.get("model") or self.DEFAULT_MODEL
        self.timeout: float = float(self.params.get("timeout", self.DEFAULT_TIMEOUT))

    def available(self) -> bool:
        if not self.api_key:
            return False
        if _requests is None:
            logger.debug("requests not importable: %s", _REQUESTS_IMPORT_ERROR)
            return False
        return True

    def ocr_page(self, pdf_path: str, page_0indexed: int) -> str:
        if not self.available():
            raise GlmOcrError("GLM-OCR unavailable (api_key missing or requests not importable)")
        single_pdf = self._extract_single_page_pdf(pdf_path, page_0indexed)
        data_url = "data:application/pdf;base64," + base64.b64encode(single_pdf).decode("ascii")
        return self._layout_parsing(data_url)

    def _extract_single_page_pdf(self, pdf_path: str, page_0indexed: int) -> bytes:
        import pymupdf

        src = pymupdf.open(pdf_path)
        try:
            if not 0 <= page_0indexed < src.page_count:
                raise GlmOcrError(f"page index {page_0indexed} out of range (0..{src.page_count - 1})")
            single = pymupdf.open()
            try:
                single.insert_pdf(src, from_page=page_0indexed, to_page=page_0indexed)
                return single.tobytes()
            finally:
                single.close()
        finally:
            src.close()

    def _layout_parsing(self, data_url: str) -> str:
        try:
            r = _requests.post(
                f"{self.base_url.rstrip('/')}/layout_parsing",
                json={"model": self.model, "file": data_url},
                headers={
                    "Content-Type": "application/json",
                    "Authorization": f"Bearer {self.api_key}",
                },
                timeout=self.timeout,
            )
            r.raise_for_status()
        except Exception as exc:
            raise GlmOcrError(f"layout_parsing call failed: {exc}") from exc
        data = r.json()
        # md_results: whole-document markdown string; single-page input → 1:1
        md = data.get("md_results")
        if not isinstance(md, str) or not md.strip():
            raise GlmOcrError("empty md_results in GLM-OCR response")
        return md.strip()
