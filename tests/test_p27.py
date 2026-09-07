"""Ticket 04/05/06: v0.3 routing pipeline against p27.pdf (image_based scan).

v0.3 changes:
- Router class replaced by process_pdf() function.
- page.needs_ocr=True pages skip looks_like_text and go straight to ocr_page().
- Error message: engine_failed: ... instead of "no_ocr_configured".
- report is returned directly, not via router.last_report.
"""

from pathlib import Path

import pytest

from pdf_router.config import RouterConfig
from pdf_router.core import process_pdf
from pdf_router.ir import SrcTool

RUNTIME = Path("/home/scanum/pdf-router/.runtime")


def test_p27_default_config_no_ocr_engine(p27_pdf) -> None:
    """Default config: pdf_inspector_ocr is the fallback engine but not configured
    (no params). ocr_page() raises OcrError → page.error set, src_tool = PDF_INSPECTOR_OCR."""
    config = RouterConfig()
    ir, report = process_pdf(str(p27_pdf), config)

    assert ir.metadata.pdf_type == "image_based"
    assert len(ir.pages) == 1
    page = ir.pages[0]
    assert page.src_tool is SrcTool.PDF_INSPECTOR_OCR  # v0.3: set before OCR attempt
    assert page.error is not None
    assert "pdf_inspector_ocr_failed" in page.error
    assert page.markdown == ""
    assert report.pages_failed == 1
    assert report.pages_native == 0


def test_p27_with_tesseract(p27_pdf) -> None:
    """v0.3: routing.ocr_fallback_engine = "tesseract" instead of ocr_specs."""
    config = RouterConfig()
    from pdf_router.config import RoutingConfig

    config.routing = RoutingConfig(
        ocr_fallback_engine="tesseract",
        ocr_fallback_params={"languages": ["eng"]},
        # use defaults for everything else
        language=None,
        text_alpha_threshold=0.3,
        workers=1,
        extract_raster_photos=False,
        extract_vector_diagrams=False,
    )
    ir, report = process_pdf(str(p27_pdf), config)

    page = ir.pages[0]
    assert page.src_tool is SrcTool.TESSERACT
    assert page.error is None
    assert page.markdown.strip()
    assert report.pages_ocr == 1


# --- Ticket 05: pdf-inspector native OCR -------------------------------------


def test_pdf_inspector_ocr_available_with_runtime_paths() -> None:
    from pdf_router.adapters.pdf_inspector_ocr import AdapterPdfInspectorOCR

    adapter = AdapterPdfInspectorOCR(
        {
            "pdfium_lib_path": str(RUNTIME / "lib" / "libpdfium.so"),
            "ort_dylib_path": str(RUNTIME / "onnxruntime-linux-x64-1.27.0" / "lib" / "libonnxruntime.so"),
        }
    )
    assert adapter.available() is True


def test_pdf_inspector_ocr_unavailable_with_bogus_paths() -> None:
    from pdf_router.adapters.pdf_inspector_ocr import AdapterPdfInspectorOCR

    adapter = AdapterPdfInspectorOCR(
        {"pdfium_lib_path": "/nonexistent/libpdfium.so", "ort_dylib_path": "/nonexistent/libonnxruntime.so"}
    )
    assert adapter.available() is False


@pytest.mark.slow
def test_p27_with_pdf_inspector_ocr(p27_pdf) -> None:
    """Full OCR run (~20s/page): src_tool flips to PDF_INSPECTOR_OCR."""
    from pdf_router.adapters.pdf_inspector_ocr import AdapterPdfInspectorOCR
    from pdf_router.config import RoutingConfig

    params = {
        "pdfium_lib_path": str(RUNTIME / "lib" / "libpdfium.so"),
        "ort_dylib_path": str(RUNTIME / "onnxruntime-linux-x64-1.27.0" / "lib" / "libonnxruntime.so"),
    }
    adapter = AdapterPdfInspectorOCR(params)
    if not adapter.available():
        pytest.skip(".runtime binaries not present")

    config = RouterConfig()
    config.routing = RoutingConfig(
        ocr_fallback_engine="pdf_inspector_ocr",
        ocr_fallback_params=params,
        language=None,
        text_alpha_threshold=0.3,
        workers=1,
        extract_raster_photos=False,
        extract_vector_diagrams=False,
    )
    ir, report = process_pdf(str(p27_pdf), config)

    page = ir.pages[0]
    assert page.src_tool is SrcTool.PDF_INSPECTOR_OCR
    assert page.error is None
    assert page.markdown.strip()
    assert report.pages_ocr == 1


# --- Ticket 06: tesseract -----------------------------------------------------


def test_tesseract_available() -> None:
    from pdf_router.adapters.tesseract import AdapterTesseract

    adapter = AdapterTesseract({"languages": ["rus", "eng"]})
    assert adapter.available() is True  # tesseract 5.3.4 on PATH in sandbox
