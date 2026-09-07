"""Tests for OCR dispatcher — RT-06 routing to configured engine.

Covers the A1 implementation: ``pdf_inspector_ocr`` uses
``process_pdf_with_ocr(mode='force')`` per-page, with ``offline`` flag
plumbed through from ``ocr_fallback_params``.
"""

from __future__ import annotations

import inspect
from unittest.mock import MagicMock, patch

import pytest

from pdf_router.config import RouterConfig
from pdf_router.ocr import OcrError, _ocr_pdf_inspector, ocr_page

# Real runtime paths exist in the env (libpdfium.so + libonnxruntime.so).
# Used to bypass the early-exit guard in _ocr_pdf_inspector that checks
# binary presence on disk.
RUNTIME_PDFIUM = "/home/scanum/pdf-router/.runtime/lib/libpdfium.so"
RUNTIME_ORT = (
    "/home/scanum/pdf-router/.runtime/onnxruntime-linux-x64-1.27.0/lib/"
    "libonnxruntime.so"
)


def _params_with_runtime() -> dict:
    return {
        "pdfium_lib_path": RUNTIME_PDFIUM,
        "ort_dylib_path": RUNTIME_ORT,
    }


def test_pdf_inspector_engine_signature_unchanged() -> None:
    """A1 must keep ``ocr_page(pdf_path, page_idx, config) -> (md, engine)``.

    Refactoring the dispatcher would touch ``core.py``; this guard
    prevents that.
    """
    sig = inspect.signature(ocr_page)
    params = list(sig.parameters.keys())
    assert params == ["pdf_path", "page_idx", "config"]


def test_pdf_inspector_ocr_calls_process_pdf_with_ocr() -> None:
    """A1: ``_ocr_pdf_inspector`` must use ``process_pdf_with_ocr``.

    Mocks the entire pdf_inspector module to assert:
    1. mode='force' (orchestrator already decided OCR is needed)
    2. page_numbers=[page_idx + 1] (1-indexed)
    3. offline=... plumbed from params
    """
    fake_page = MagicMock()
    fake_page.page_number = 5
    fake_page.markdown = "ocr text"

    fake_result = MagicMock()
    fake_result.pages = [fake_page]

    fake_pdf_inspector = MagicMock()
    fake_pdf_inspector.process_pdf_with_ocr.return_value = fake_result

    with patch.dict("sys.modules", {"pdf_inspector": fake_pdf_inspector}):
        result = _ocr_pdf_inspector(
            "/tmp/x.pdf", page_idx=4, params={**_params_with_runtime(), "offline": True}
        )

    assert result == "ocr text"
    fake_pdf_inspector.process_pdf_with_ocr.assert_called_once()
    call = fake_pdf_inspector.process_pdf_with_ocr.call_args
    assert call.args[0] == "/tmp/x.pdf"
    assert call.kwargs["mode"] == "force"
    assert call.kwargs["page_numbers"] == [5]  # 1-indexed
    assert call.kwargs["offline"] is True


def test_pdf_inspector_ocr_default_offline_false() -> None:
    """If params has no 'offline' key, default to False."""
    fake_page = MagicMock()
    fake_page.page_number = 1
    fake_page.markdown = "x"

    fake_result = MagicMock()
    fake_result.pages = [fake_page]

    fake_pdf_inspector = MagicMock()
    fake_pdf_inspector.process_pdf_with_ocr.return_value = fake_result

    with patch.dict("sys.modules", {"pdf_inspector": fake_pdf_inspector}):
        _ocr_pdf_inspector("/tmp/x.pdf", page_idx=0, params=_params_with_runtime())

    call = fake_pdf_inspector.process_pdf_with_ocr.call_args
    assert call.kwargs["offline"] is False


def test_pdf_inspector_ocr_raises_on_empty_result() -> None:
    """Empty markdown from pdf-inspector must raise OcrError, not return ''."""
    fake_page = MagicMock()
    fake_page.page_number = 3
    fake_page.markdown = ""  # empty

    fake_result = MagicMock()
    fake_result.pages = [fake_page]

    fake_pdf_inspector = MagicMock()
    fake_pdf_inspector.process_pdf_with_ocr.return_value = fake_result

    with patch.dict("sys.modules", {"pdf_inspector": fake_pdf_inspector}):
        with pytest.raises(OcrError, match="empty pdf_inspector_ocr result"):
            _ocr_pdf_inspector("/tmp/x.pdf", page_idx=2, params=_params_with_runtime())


def test_pdf_inspector_ocr_raises_if_page_missing() -> None:
    """If pdf-inspector returns pages for a different number, raise OcrError."""
    fake_page = MagicMock()
    fake_page.page_number = 99  # wrong page
    fake_page.markdown = "x"

    fake_result = MagicMock()
    fake_result.pages = [fake_page]

    fake_pdf_inspector = MagicMock()
    fake_pdf_inspector.process_pdf_with_ocr.return_value = fake_result

    with patch.dict("sys.modules", {"pdf_inspector": fake_pdf_inspector}):
        with pytest.raises(OcrError, match="missing from pdf_inspector_ocr result"):
            _ocr_pdf_inspector("/tmp/x.pdf", page_idx=0, params=_params_with_runtime())


def test_ocr_page_dispatches_to_engine() -> None:
    """ocr_page routes the right engine to its implementation.

    With bogus paths, the early-exit guard fires before pdf_inspector
    is even called — so we get ``OcrError(binaries not found)``.
    """
    cfg = RouterConfig()
    cfg.routing.ocr_fallback_engine = "pdf_inspector_ocr"
    cfg.routing.ocr_fallback_params = {
        "pdfium_lib_path": "/nonexistent/libpdfium.so",
        "ort_dylib_path": "/nonexistent/libonnxruntime.so",
    }

    with pytest.raises(OcrError, match="binaries not found"):
        # paths don't exist on disk, so the early-exit triggers
        ocr_page("/tmp/x.pdf", page_idx=0, config=cfg)


def test_ocr_page_unknown_engine_raises() -> None:
    cfg = RouterConfig()
    cfg.routing.ocr_fallback_engine = "no_such_engine"
    with pytest.raises(OcrError, match="unknown ocr_fallback_engine"):
        ocr_page("/tmp/x.pdf", page_idx=0, config=cfg)
