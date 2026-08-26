"""Router orchestration. DESIGN.md §5.

One ``process_pdf`` pass (via the always-loaded pdf-inspector adapter), then
fan-out per ``needs_ocr`` page over the configured OCR specs. First available
spec wins; failures never abort the pipeline (graceful degradation).
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass
from typing import Any, Protocol

from pdf_router.config import OCRAdapterSpec, RouterConfig
from pdf_router.ir import IR, SrcTool

logger = logging.getLogger("pdf_router")


class OCRAdapter(Protocol):
    """The per-page OCR contract every adapter implements (tickets 05-07)."""

    name: str

    def available(self) -> bool: ...

    def ocr_page(self, pdf_path: str, page_0indexed: int) -> str: ...


@dataclass
class ProcessingReport:
    pages_total: int = 0
    pages_native: int = 0
    pages_ocr: int = 0
    pages_failed: int = 0
    pages_skipped: int = 0


#: Maps spec name → adapter class. Only core.py imports adapters (DESIGN §10).
ADAPTER_CLASSES: dict[str, str] = {
    "pdf_inspector_ocr": "pdf_router.adapters.pdf_inspector_ocr:AdapterPdfInspectorOCR",
    "tesseract": "pdf_router.adapters.tesseract:AdapterTesseract",
    "glm_ocr": "pdf_router.adapters.glm_ocr:AdapterGlmOcr",
    "deepseek_ocr": "pdf_router.adapters.deepseek_ocr:AdapterDeepseekOcr",
}


def _load_adapter_class(dotted: str) -> type | None:
    """Import an adapter class lazily; None if the module is not written yet."""
    module_path, _, attr = dotted.partition(":")
    try:
        module = __import__(module_path, fromlist=[attr])
        return getattr(module, attr)
    except Exception:
        return None


class Router:
    """Load a PDF once, fan OCR out per page, return a complete IR."""

    def __init__(self, config: RouterConfig | None = None) -> None:
        self.config = config or RouterConfig()
        self.last_report: ProcessingReport | None = None

    @classmethod
    def from_config(cls, config_path: str | None) -> "Router":
        return cls(RouterConfig.from_toml(config_path))

    def process(self, pdf_path: str) -> IR:
        """Full pipeline. Raises only on document-level failure (exit 3)."""
        from pdf_router.adapters.pdf_inspector import AdapterPdfInspector

        adapter = AdapterPdfInspector()
        if not adapter.available():
            raise RuntimeError("pdf-inspector is not importable")

        t0 = time.perf_counter()
        doc = adapter.process(pdf_path)
        report = ProcessingReport(pages_total=len(doc.pages))

        # Instantiate configured adapters once; drop unavailable ones.
        candidates: list[Any] = []
        for spec in self.config.ocr_specs:
            cls = _load_adapter_class(ADAPTER_CLASSES.get(spec.name, ""))
            if cls is None:
                logger.warning("ocr adapter %r not loadable; skipping", spec.name)
                continue
            instance = cls(spec.params)
            if not instance.available():
                logger.warning("ocr adapter %r unavailable; skipping", spec.name)
                continue
            candidates.append(instance)

        for page in doc.pages:
            if not page.needs_ocr:
                report.pages_native += 1
                logger.info(
                    "page %d/%d: %s (%.0fms)",
                    page.page + 1, report.pages_total, page.src_tool.value,
                    (time.perf_counter() - t0) * 1000,
                )
                continue

            ocr_done = False
            for adapter_instance in candidates:
                t_page = time.perf_counter()
                try:
                    markdown = adapter_instance.ocr_page(pdf_path, page.page)
                except Exception as exc:
                    page.error = f"{adapter_instance.name}_failed: {exc}"
                    logger.warning(
                        "page %d/%d: %s — %s", page.page + 1, report.pages_total,
                        adapter_instance.name, exc,
                    )
                    continue
                if not markdown or not markdown.strip():
                    page.error = f"{adapter_instance.name}_failed: empty result"
                    logger.warning(
                        "page %d/%d: %s — empty result", page.page + 1, report.pages_total,
                        adapter_instance.name,
                    )
                    continue
                page.markdown = markdown
                page.src_tool = SrcTool(adapter_instance.name)
                page.error = None
                report.pages_ocr += 1
                logger.info(
                    "page %d/%d: %s (%.0fms)",
                    page.page + 1, report.pages_total, page.src_tool.value,
                    (time.perf_counter() - t_page) * 1000,
                )
                ocr_done = True
                break

            if not ocr_done:
                page.error = page.error or "no_ocr_configured"
                if not candidates:
                    report.pages_skipped += 1
                else:
                    report.pages_failed += 1
                logger.warning(
                    "page %d/%d: skip — %s", page.page + 1, report.pages_total, page.error,
                )

        self.last_report = report
        return doc
