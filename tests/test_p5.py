"""Ticket 03: pdf-inspector text-path adapter against p5.pdf (golden anchors)."""

from pdf_router.adapters.pdf_inspector import extract_ir
from pdf_router.ir import SrcTool


def test_adapter_available() -> None:
    try:
        import pdf_inspector  # noqa: F401
        assert True
    except ImportError:
        assert False, "pdf_inspector not importable"


def test_p5_properties_and_anchors(p5_pdf) -> None:
    ir = extract_ir(str(p5_pdf))

    assert ir.metadata.pdf_type == "text_based"
    assert ir.metadata.is_complex_layout is True
    assert ir.metadata.page_count == 1
    assert len(ir.pages) == 1

    page = ir.pages[0]
    assert page.page == 0
    assert page.src_tool is SrcTool.PDF_INSPECTOR
    assert page.needs_ocr is False
    assert page.error is None
    assert page.markdown  # non-empty
    # golden anchors
    assert "Обозначение" in page.markdown
    assert "ИЛО9.1" in page.markdown
