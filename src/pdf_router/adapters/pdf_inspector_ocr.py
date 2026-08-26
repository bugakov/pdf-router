"""pdf-inspector native OCR adapter (PP-OCRv6 Small via PDFium + ONNX). Opt-in.

Wraps ``pdf_inspector.process_pdf_with_ocr(path, mode="force", page_numbers=[N])``
for a targeted single-page call. Requires ``pdfium_lib_path`` and
``ort_dylib_path`` in the spec; the corresponding env vars are set at
instantiation (the Rust core reads them at call time). Never silently
substitutes: failures raise, emptiness raises.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any, ClassVar


class PdfInspectorOcrError(RuntimeError):
    """Typed failure of the pdf-inspector OCR path."""


class AdapterPdfInspectorOCR:
    name: ClassVar[str] = "pdf_inspector_ocr"

    def __init__(self, params: dict[str, Any] | None = None) -> None:
        self.params = dict(params or {})
        self.pdfium_lib_path: str | None = self.params.get("pdfium_lib_path")
        self.ort_dylib_path: str | None = self.params.get("ort_dylib_path")
        self.model_cache_dir: str | None = self.params.get("model_cache_dir")
        self.offline: bool = bool(self.params.get("offline", False))

    def available(self) -> bool:
        """True if both binaries exist (env wiring happens in __init__/_wire_env)."""
        if not (self.pdfium_lib_path and self.ort_dylib_path):
            return False
        if not (Path(self.pdfium_lib_path).is_file() and Path(self.ort_dylib_path).is_file()):
            return False
        try:
            import pdf_inspector  # noqa: F401

            return True
        except Exception:
            return False

    def _wire_env(self) -> None:
        """Point the Rust core at the configured binaries via env vars."""
        if self.pdfium_lib_path:
            os.environ["PDFIUM_LIB_PATH"] = str(self.pdfium_lib_path)
        if self.ort_dylib_path:
            os.environ["ORT_DYLIB_PATH"] = str(self.ort_dylib_path)
            # onnxruntime needs the dir on the loader path as well
            ort_dir = str(Path(self.ort_dylib_path).parent)
            ld = os.environ.get("LD_LIBRARY_PATH", "")
            if ort_dir not in ld.split(os.pathsep):
                os.environ["LD_LIBRARY_PATH"] = f"{ort_dir}{os.pathsep}{ld}" if ld else ort_dir
        if self.model_cache_dir:
            os.environ.setdefault("PDF_INSPECTOR_MODEL_CACHE", str(self.model_cache_dir))

    def ocr_page(self, pdf_path: str, page_0indexed: int) -> str:
        if not self.available():
            raise PdfInspectorOcrError("pdf-inspector OCR not available (missing binaries)")
        import pdf_inspector

        self._wire_env()
        result = pdf_inspector.process_pdf_with_ocr(
            pdf_path,
            mode="force",
            page_numbers=[page_0indexed + 1],  # 1-indexed
        )
        for page in result.pages:
            if page.page_number == page_0indexed + 1:
                md = page.markdown or ""
                if not md.strip():
                    raise PdfInspectorOcrError(f"page {page_0indexed + 1}: empty OCR result")
                return md
        raise PdfInspectorOcrError(f"page {page_0indexed + 1} missing from OCR result")
