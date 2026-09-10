"""Ticket 04/05/06: v0.3 routing pipeline against p27.pdf (image_based scan).

v0.3 changes:
- Router class replaced by process_pdf() function.
- page.needs_ocr=True pages skip looks_like_text and go straight to ocr_page().
- Error message: engine_failed: ... instead of "no_ocr_configured".
- report is returned directly, not via router.last_report.
"""

import pytest

from pdf_router.config import RouterConfig
from pdf_router.core import process_pdf
from pdf_router.ir import SrcTool


@pytest.mark.slow
def test_p27_default_config_uses_rapidocr(p27_pdf) -> None:
    """Default config: rapidocr is the fallback engine. It should succeed on p27
    (image_based scan with Russian text)."""
    config = RouterConfig()
    ir, report = process_pdf(str(p27_pdf), config)

    assert ir.metadata.pdf_type == "image_based"
    assert len(ir.pages) == 1
    page = ir.pages[0]
    assert page.src_tool is SrcTool.RAPIDOCR
    assert page.error is None
    assert page.markdown.strip()
    assert report.pages_ocr == 1


@pytest.mark.slow
def test_p27_with_tesseract(p27_pdf) -> None:
    """routing.ocr_fallback_engine = "tesseract"; params in [ocr.tesseract]."""
    config = RouterConfig()
    from pdf_router.config import RoutingConfig

    config.routing = RoutingConfig(
        ocr_fallback_engine="tesseract",
        # use defaults for everything else
        language=None,
        text_alpha_threshold=0.3,
        workers=1,
        extract_raster_photos=False,
        extract_vector_diagrams=False,
    )
    config.ocr_engines["tesseract"] = {"languages": ["eng"]}
    ir, report = process_pdf(str(p27_pdf), config)

    page = ir.pages[0]
    assert page.src_tool is SrcTool.TESSERACT
    assert page.error is None
    assert page.markdown.strip()
    assert report.pages_ocr == 1


# --- Ticket 05: pdf-inspector native OCR -------------------------------------


@pytest.mark.slow
def test_p27_with_pdf_inspector_ocr(p27_pdf, monkeypatch) -> None:
    """p27 via the text path + pdf_inspector_ocr OCR fallback (real binaries).

    Requires the .runtime binaries; wire_pdf_inspector_env bridges the
    config section to the env the Rust core reads.
    """
    from pdf_router import wire_pdf_inspector_env

    config = RouterConfig()
    config.routing.ocr_fallback_engine = "pdf_inspector_ocr"
    config.ocr_engines["pdf_inspector_ocr"] = {
        "pdfium_lib_path": "/home/scanum/pdf-router/.runtime/lib/libpdfium.so",
        "ort_dylib_path": (
            "/home/scanum/pdf-router/.runtime/onnxruntime-linux-x64-1.27.0/"
            "lib/libonnxruntime.so"
        ),
    }
    # wire sets these; register undo first so the leak does not outlive the test.
    monkeypatch.delenv("PDFIUM_LIB_PATH", raising=False)
    monkeypatch.delenv("ORT_DYLIB_PATH", raising=False)
    wire_pdf_inspector_env(config)
    ir, report = process_pdf(p27_pdf, config=config)
    assert report.pages_total == 1
    page = ir.pages[0]
    assert page.src_tool is SrcTool.PDF_INSPECTOR_OCR
    assert page.error is None
    assert page.markdown.strip()
    assert report.pages_ocr == 1
