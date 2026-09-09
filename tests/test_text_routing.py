"""Regression tests for _process_text_for_page text aggregation.

Background (pzu p4/p5 bug): the old aggregation was `blocks_text or markdown`,
so a sparse blocks subset (16 chars of a doc-number stamp, alpha_ratio 0.125)
shadowed 2502 chars of clean Cyrillic in page.markdown and wrongly routed the
page to OCR. The fix makes markdown the primary source for the quality check
(blocks are only a fallback when markdown is empty).
"""

from types import SimpleNamespace

from pdf_router import core
from pdf_router.config import RouterConfig
from pdf_router.ir import PageResult, SrcTool


def _fake_ocr(calls):
    """Return a monkeypatch replacement for core.ocr_page recording calls."""

    def ocr_page(pdf_path, page_idx, config):
        calls.append((pdf_path, page_idx))
        return "OCR TEXT", config.routing.ocr_fallback_engine

    return ocr_page


def test_clean_markdown_not_shadowed_by_sparse_blocks(monkeypatch):
    """THE regression: sparse stamp in blocks must not shadow clean markdown."""
    page = PageResult(page=3, needs_ocr=False)
    page.blocks = [SimpleNamespace(text="00847.2024.07-СП")]
    page.markdown = "Система водоснабжения. Ведомость чертежей. " * 50
    src_before = page.src_tool

    calls: list = []
    monkeypatch.setattr(core, "ocr_page", _fake_ocr(calls))

    core._process_text_for_page(page, "/tmp/fake.pdf", RouterConfig())

    assert calls == []  # no OCR
    assert page.ocr_reason is None
    assert page.src_tool == src_before  # untouched -> still native
    assert page.markdown.startswith("Система водоснабжения")  # not replaced


def test_blocks_fallback_when_markdown_empty(monkeypatch):
    """Markdown empty -> blocks text is still used for the quality check."""
    page = PageResult(page=0, needs_ocr=False)
    page.blocks = [SimpleNamespace(text="Ведомость чертежей основного комплекта")]
    page.markdown = ""

    calls: list = []
    monkeypatch.setattr(core, "ocr_page", _fake_ocr(calls))

    core._process_text_for_page(page, "/tmp/fake.pdf", RouterConfig())

    assert calls == []
    assert page.ocr_reason is None


def test_garbled_markdown_triggers_ocr_with_reason(monkeypatch):
    """Below-threshold text (no letters) -> OCR fallback, reason recorded."""
    page = PageResult(page=11, needs_ocr=False)
    page.blocks = []
    page.markdown = "12345 --- ### ... " * 40  # alpha_ratio ~ 0.0

    calls: list = []
    monkeypatch.setattr(core, "ocr_page", _fake_ocr(calls))

    config = RouterConfig()
    core._process_text_for_page(page, "/tmp/fake.pdf", config)

    assert calls == [("/tmp/fake.pdf", 11)]
    assert page.ocr_reason == "low_text_quality"
    assert page.src_tool == SrcTool(config.routing.ocr_fallback_engine)
    assert page.markdown == "OCR TEXT"
    assert page.error is None


def test_inspector_needs_ocr_bypasses_quality_check(monkeypatch):
    """RT-03: needs_ocr=True skips looks_like_text even for clean text."""
    page = PageResult(page=12, needs_ocr=True)
    page.blocks = []
    page.markdown = "Совершенно чистый русский текст. " * 50

    calls: list = []
    monkeypatch.setattr(core, "ocr_page", _fake_ocr(calls))

    core._process_text_for_page(page, "/tmp/fake.pdf", RouterConfig())

    assert calls == [("/tmp/fake.pdf", 12)]
    assert page.ocr_reason == "inspector_needs_ocr"
    assert page.markdown == "OCR TEXT"


def test_ocr_failure_marks_src_tool_and_error(monkeypatch):
    """Failed OCR still stamps src_tool (what was attempted) and error."""
    from pdf_router.ocr import OcrError

    page = PageResult(page=4, needs_ocr=False)
    page.blocks = []
    page.markdown = "1234 --- ###" * 30

    def failing_ocr(pdf_path, page_idx, config):
        raise OcrError("engine exploded")

    monkeypatch.setattr(core, "ocr_page", failing_ocr)

    config = RouterConfig()
    core._process_text_for_page(page, "/tmp/fake.pdf", config)

    assert page.src_tool == SrcTool(config.routing.ocr_fallback_engine)
    assert page.error is not None
    assert page.error.startswith(f"{config.routing.ocr_fallback_engine}_failed")
