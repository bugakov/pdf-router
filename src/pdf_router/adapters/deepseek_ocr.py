"""deepseek-ocr-2 cloud adapter (Novita AI, OpenAI-compatible). Opt-in, HTTP.

Renders the page to PNG (200 DPI), uploads it (catbox.moe by default), then
POSTs a chat-completions request with the image URL. ``requests`` lives in
the ``[cloud]`` extra; if missing, the module still imports and
:meth:`AdapterDeepseekOcr.available` returns ``False``.
"""

from __future__ import annotations

import logging
from typing import Any, ClassVar

logger = logging.getLogger("pdf_router")

try:  # module-level guard: [cloud] extra not installed → clean unavailability
    import requests as _requests

    _REQUESTS_IMPORT_ERROR: Exception | None = None
except Exception as _exc:  # pragma: no cover - depends on extras
    _requests = None  # type: ignore[assignment]
    _REQUESTS_IMPORT_ERROR = _exc


class DeepseekOcrError(RuntimeError):
    """Typed failure of the deepseek-ocr path."""


class AdapterDeepseekOcr:
    name: ClassVar[str] = "deepseek_ocr"

    DEFAULT_BASE_URL = "https://api.novita.ai/openai"
    DEFAULT_MODEL = "deepseek/deepseek-ocr-2"
    DEFAULT_UPLOAD_URL = "https://catbox.moe/user/api.php"
    DEFAULT_PROMPT = "Convert the document to markdown."
    DEFAULT_TIMEOUT = 60.0
    DEFAULT_MAX_TOKENS = 4096  # reference scripts keep this < 8192

    def __init__(self, params: dict[str, Any] | None = None) -> None:
        self.params = dict(params or {})
        self.api_key: str | None = self.params.get("api_key")
        self.base_url: str = self.params.get("base_url") or self.DEFAULT_BASE_URL
        self.model: str = self.params.get("model") or self.DEFAULT_MODEL
        self.upload_url: str = self.params.get("upload_url") or self.DEFAULT_UPLOAD_URL
        self.prompt: str = self.params.get("prompt") or self.DEFAULT_PROMPT
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
            raise DeepseekOcrError("deepseek-ocr unavailable (api_key missing or requests not importable)")
        png = self._render_page_png(pdf_path, page_0indexed, self.dpi)
        image_url = self._upload(png)
        return self._complete(image_url)

    def _render_page_png(self, pdf_path: str, page_0indexed: int, dpi: int) -> bytes:
        import fitz

        doc = fitz.open(pdf_path)
        try:
            if not 0 <= page_0indexed < doc.page_count:
                raise DeepseekOcrError(f"page index {page_0indexed} out of range (0..{doc.page_count - 1})")
            zoom = dpi / 72.0
            return doc[page_0indexed].get_pixmap(matrix=fitz.Matrix(zoom, zoom), alpha=False).tobytes(output="png")
        finally:
            doc.close()

    def _upload(self, png: bytes) -> str:
        try:
            r = _requests.post(
                self.upload_url,
                data={"reqtype": "fileupload"},
                files={"fileToUpload": ("page.png", png, "image/png")},
                timeout=self.timeout,
            )
            r.raise_for_status()
        except Exception as exc:
            raise DeepseekOcrError(f"image upload failed: {exc}") from exc
        url = r.text.strip().splitlines()[-1].strip()
        if not url.startswith("http"):
            raise DeepseekOcrError(f"bad upload response: {r.text[:200]!r}")
        return url

    def _complete(self, image_url: str) -> str:
        try:
            r = _requests.post(
                f"{self.base_url.rstrip('/')}/chat/completions",
                json={
                    "model": self.model,
                    "stream": False,
                    "temperature": 0.0,
                    "max_tokens": self.max_tokens,
                    "messages": [
                        {
                            "role": "user",
                            "content": [
                                {"type": "image_url", "image_url": {"url": image_url, "detail": "high"}},
                                {"type": "text", "text": self.prompt},
                            ],
                        }
                    ],
                },
                headers={"Content-Type": "application/json", "Authorization": f"Bearer {self.api_key}"},
                timeout=self.timeout,
            )
            r.raise_for_status()
        except Exception as exc:
            raise DeepseekOcrError(f"chat/completions failed: {exc}") from exc
        choices = r.json().get("choices") or []
        if not choices:
            raise DeepseekOcrError("no choices in response")
        content = (choices[0].get("message") or {}).get("content")
        if not isinstance(content, str) or not content.strip():
            raise DeepseekOcrError("empty content in response")
        return content.strip()
