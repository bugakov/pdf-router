"""Ticket 04/05/06: orchestrator + OCR adapters against p27.pdf (image_based scan)."""

from pathlib import Path

import pytest

from pdf_router.config import OCRAdapterSpec, RouterConfig
from pdf_router.core import Router
from pdf_router.ir import SrcTool

RUNTIME = Path("/home/scanum/pdf-router/.runtime")


def test_p27_no_ocr_configured(p27_pdf) -> None:
    router = Router(RouterConfig())  # no specs
    ir = router.process(str(p27_pdf))

    assert ir.metadata.pdf_type == "image_based"
    assert len(ir.pages) == 1
    page = ir.pages[0]
    assert page.src_tool is SrcTool.PDF_INSPECTOR
    assert page.error == "no_ocr_configured"
    assert page.markdown == ""  # pdf-inspector's empty markdown kept, not substituted
    assert router.last_report is not None
    assert router.last_report.pages_skipped == 1
    assert router.last_report.pages_native == 0


def test_p27_with_mocked_ocr(p27_pdf, monkeypatch) -> None:
    """A mocked OCR spec rewrites the page; error clears."""
    import pdf_router.core as core

    class FakeAdapter:
        name = "tesseract"

        def __init__(self, params):
            pass

        def available(self) -> bool:
            return True

        def ocr_page(self, pdf_path: str, page_0indexed: int) -> str:
            return "# OCR'd heading\n\nscanned text"

    monkeypatch.setattr(core, "_load_adapter_class", lambda dotted: FakeAdapter)

    router = Router(RouterConfig(ocr_specs=[OCRAdapterSpec(name="tesseract", params={"languages": ["eng"]})]))
    ir = router.process(str(p27_pdf))

    page = ir.pages[0]
    assert page.src_tool is SrcTool.TESSERACT
    assert page.error is None
    assert "OCR'd heading" in page.markdown
    assert router.last_report.pages_ocr == 1


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

    adapter = AdapterPdfInspectorOCR(
        {
            "pdfium_lib_path": str(RUNTIME / "lib" / "libpdfium.so"),
            "ort_dylib_path": str(RUNTIME / "onnxruntime-linux-x64-1.27.0" / "lib" / "libonnxruntime.so"),
        }
    )
    if not adapter.available():
        pytest.skip(".runtime binaries not present")

    router = Router(
        RouterConfig(ocr_specs=[OCRAdapterSpec(name="pdf_inspector_ocr", params=adapter.params)])
    )
    ir = router.process(str(p27_pdf))

    page = ir.pages[0]
    assert page.src_tool is SrcTool.PDF_INSPECTOR_OCR
    assert page.error is None
    assert page.markdown.strip()
    assert router.last_report.pages_ocr == 1


def test_p27_bogus_ocr_spec_falls_through_to_no_ocr_configured(p27_pdf) -> None:
    """Bogus paths → available() False → orchestrator marks no_ocr_configured."""
    router = Router(
        RouterConfig(
            ocr_specs=[
                OCRAdapterSpec(
                    name="pdf_inspector_ocr",
                    params={
                        "pdfium_lib_path": "/nonexistent/libpdfium.so",
                        "ort_dylib_path": "/nonexistent/libonnxruntime.so",
                    },
                )
            ]
        )
    )
    ir = router.process(str(p27_pdf))
    assert ir.pages[0].error == "no_ocr_configured"
    assert ir.pages[0].src_tool is SrcTool.PDF_INSPECTOR


# --- Ticket 06: tesseract -----------------------------------------------------


def test_tesseract_available() -> None:
    from pdf_router.adapters.tesseract import AdapterTesseract

    adapter = AdapterTesseract({"languages": ["rus", "eng"]})
    assert adapter.available() is True  # tesseract 5.3.4 on PATH in sandbox


def test_p27_with_tesseract(p27_pdf) -> None:
    """Real tesseract run: src_tool flips to TESSERACT, markdown non-empty."""
    router = Router(RouterConfig(ocr_specs=[OCRAdapterSpec(name="tesseract", params={"languages": ["rus", "eng"]})]))
    ir = router.process(str(p27_pdf))

    page = ir.pages[0]
    assert page.src_tool is SrcTool.TESSERACT
    assert page.error is None
    assert page.markdown.strip()
    assert router.last_report.pages_ocr == 1
