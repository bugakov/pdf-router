"""GLM vision OCR adapter (Z.ai ``chat/completions``, coding endpoint). Opt-in, HTTP.

Renders the page to JPEG (200 DPI) and POSTs it inline as a base64 data-URL
to the coding-plan endpoint (``https://api.z.ai/api/coding/paas/v4``) — no
third-party file host, no upload step. Vision models (``glm-4.5v`` default)
answer with the extracted text.

Empirical notes (live-tested 2026-08-27):
- The standard endpoint (``/api/paas/v4``) bills document/vision calls
  separately; a coding-plan key gets 429 "Insufficient balance" there.
  The coding endpoint accepts the same key on ``chat/completions``.
- ``layout_parsing`` is NOT available on the coding endpoint (billed as
  the standard package → 429).
- Text models (``glm-4.5``, ``glm-4.6``, ...) reject ``image_url`` content
  (400 "messages.content.type is invalid, allowed values: ['text']");
  only ``*-v`` vision models accept images.
- Thinking models burn the token budget on ``reasoning_content`` and can
  return an empty ``content``; ``thinking: {"type": "disabled"}`` fixes
  that and is accepted by vision models too.

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

    DEFAULT_BASE_URL = "https://api.z.ai/api/coding/paas/v4"
    DEFAULT_MODEL = "glm-4.5v"
    DEFAULT_TIMEOUT = 300.0
    DEFAULT_MAX_TOKENS = 8192
    # Terse prompts keep deepseek-style repetition loops away; the explicit
    # "no analysis / no translations" wording stops GLM from wrapping the
    # OCR result into an English-language summary (live-tested).
    DEFAULT_PROMPT = (
        "Extract ALL text from this image exactly as written. "
        "Output only the extracted text, preserving original language. "
        "No analysis, no translations, no commentary."
    )

    def __init__(self, params: dict[str, Any] | None = None) -> None:
        self.params = dict(params or {})
        self.api_key: str | None = self.params.get("api_key")
        self.base_url: str = self.params.get("base_url") or self.DEFAULT_BASE_URL
        self.model: str = self.params.get("model") or self.DEFAULT_MODEL
        self.timeout: float = float(self.params.get("timeout", self.DEFAULT_TIMEOUT))
        self.max_tokens: int = int(self.params.get("max_tokens", self.DEFAULT_MAX_TOKENS))
        self.dpi: int = int(self.params.get("dpi", 200))

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
        jpg = self._render_page_jpeg(pdf_path, page_0indexed, self.dpi)
        data_url = "data:image/jpeg;base64," + base64.b64encode(jpg).decode("ascii")
        return self._complete(data_url)

    def _render_page_jpeg(self, pdf_path: str, page_0indexed: int, dpi: int) -> bytes:
        import pymupdf

        doc = pymupdf.open(pdf_path)
        try:
            if not 0 <= page_0indexed < doc.page_count:
                raise GlmOcrError(f"page index {page_0indexed} out of range (0..{doc.page_count - 1})")
            zoom = dpi / 72.0
            pix = doc[page_0indexed].get_pixmap(matrix=pymupdf.Matrix(zoom, zoom), alpha=False)
            return pix.tobytes(output="jpeg", jpg_quality=90)
        finally:
            doc.close()

    def _complete(self, data_url: str) -> str:
        try:
            r = _requests.post(
                f"{self.base_url.rstrip('/')}/chat/completions",
                json={
                    "model": self.model,
                    "temperature": 0.0,
                    "max_tokens": self.max_tokens,
                    "thinking": {"type": "disabled"},
                    "messages": [
                        {
                            "role": "user",
                            "content": [
                                {"type": "image_url", "image_url": {"url": data_url}},
                                {"type": "text", "text": self._prompt()},
                            ],
                        }
                    ],
                },
                headers={
                    "Content-Type": "application/json",
                    "Authorization": f"Bearer {self.api_key}",
                },
                timeout=self.timeout,
            )
            r.raise_for_status()
        except Exception as exc:
            raise GlmOcrError(f"chat/completions failed: {exc}") from exc
        choices = r.json().get("choices") or []
        if not choices:
            raise GlmOcrError("no choices in response")
        message = choices[0].get("message") or {}
        content = message.get("content")
        if not isinstance(content, str) or not content.strip():
            raise GlmOcrError("empty content in response")
        return content.strip()

    def _prompt(self) -> str:
        return str(self.params.get("prompt") or self.DEFAULT_PROMPT)
