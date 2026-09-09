"""Tests for OCR dispatcher — RT-06 routing to configured engine.

Covers the A1 implementation: ``pdf_inspector_ocr`` uses
``process_pdf_with_ocr(mode='auto')`` per-page, with ``offline`` flag
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
    1. mode='auto' (selective OCR: only on pages pdf-inspector routes there)
    2. page_numbers=[page_idx + 1] (1-indexed)
    3. dpi=... (default 150.0)
    4. minimum_confidence=... (default 0.0)
    5. offline=... plumbed from params (default True)
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
            "/tmp/x.pdf",
            page_idx=4,
            params={
                **_params_with_runtime(),
                "offline": True,
                "dpi": 200.0,
                "minimum_confidence": 0.5,
            },
        )

    assert result == "ocr text"
    fake_pdf_inspector.process_pdf_with_ocr.assert_called_once()
    call = fake_pdf_inspector.process_pdf_with_ocr.call_args
    assert call.args[0] == "/tmp/x.pdf"
    assert call.kwargs["mode"] == "auto"
    assert call.kwargs["page_numbers"] == [5]  # 1-indexed
    assert call.kwargs["dpi"] == 200.0
    assert call.kwargs["minimum_confidence"] == 0.5
    assert call.kwargs["offline"] is True

def test_pdf_inspector_ocr_default_params() -> None:
    """Default dpi=150.0, minimum_confidence=0.0, offline=True when params are empty."""
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
    assert call.kwargs["dpi"] == 150.0
    assert call.kwargs["minimum_confidence"] == 0.0
    assert call.kwargs["offline"] is True


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


def test_default_ocr_fallback_engine_is_rapidocr() -> None:
    """Default in v0.3+ is rapidocr — best Russian OCR per OC-02 probe."""
    cfg = RouterConfig()
    assert cfg.routing.ocr_fallback_engine == "rapidocr"


def test_rapidocr_build_engine_uses_v6_det_and_v5_eslav() -> None:
    """Hard contract: detection = v6 small, recognition = v5 eslav mobile.
    Server variant does not exist for eslav (validated in OC-02 probe)."""
    from pdf_router.ocr import _rapidocr_build_engine
    from rapidocr import EngineType, LangRec, ModelType, OCRVersion

    cfg = RouterConfig()
    params = _rapidocr_build_engine(cfg)

    # params is a dict with dotted keys
    assert params["Det.ocr_version"] == OCRVersion.PPOCRV6
    assert params["Det.model_type"] == ModelType.SMALL
    assert params["Rec.ocr_version"] == OCRVersion.PPOCRV5
    assert params["Rec.model_type"] == ModelType.MOBILE
    assert params["Rec.lang_type"] == LangRec.ESLAV
    assert params["Global.use_cls"] is False


def test_rapidocr_caches_engine_per_recipe() -> None:
    """Module-level cache — model load is 0.6s cold but 25MB; cache by recipe key."""
    from pdf_router.ocr import _rapidocr_get_engine

    cfg = RouterConfig()
    e1 = _rapidocr_get_engine(cfg)
    e2 = _rapidocr_get_engine(cfg)
    assert e1 is e2  # same instance, no reload


def test_rapidocr_routing_language_ru_maps_to_eslav() -> None:
    """When routing.language='ru', use eslav (Russian/East-Slavic specialist)."""
    from rapidocr import LangRec
    from pdf_router.ocr import _rapidocr_language

    cfg = RouterConfig()
    cfg.routing.language = "ru"
    assert _rapidocr_language(cfg) == LangRec.ESLAV


def test_rapidocr_engine_init_is_race_free(monkeypatch) -> None:
    """Concurrent first use must build the engine exactly once.

    Regression: on the pzu benchmark rapidocr initialized twice (one
    "Using ..." init pair per ThreadPoolExecutor worker) because
    _rapidocr_get_engine checked the cache without a lock.
    """
    import threading
    import time

    from pdf_router import ocr as ocr_mod

    # Unique recipe so the test cache key collides with nothing.
    monkeypatch.setattr(
        ocr_mod, "_rapidocr_build_engine", lambda cfg: {"__race_test__": "1"}
    )

    fake_rapidocr = MagicMock()

    def slow_build(*args, **kwargs):
        time.sleep(0.2)  # widen the race window
        return object()

    fake_rapidocr.RapidOCR.side_effect = slow_build

    cfg = RouterConfig()
    cache_key = (("__race_test__", "1"),)

    ocr_mod._RAPIDOCR_ENGINE_CACHE.pop(cache_key, None)
    try:
        with patch.dict("sys.modules", {"rapidocr": fake_rapidocr}):
            barrier = threading.Barrier(8)
            results = []

            def worker():
                barrier.wait()
                results.append(ocr_mod._rapidocr_get_engine(cfg))

            threads = [threading.Thread(target=worker) for _ in range(8)]
            for t in threads:
                t.start()
            for t in threads:
                t.join()

        assert len(results) == 8
        assert all(r is results[0] for r in results)
        assert fake_rapidocr.RapidOCR.call_count == 1
    finally:
        ocr_mod._RAPIDOCR_ENGINE_CACHE.pop(cache_key, None)

def test_rapidocr_routing_language_none_defaults_to_eslav() -> None:
    """No language hint → default to eslav (most likely Russian corpus)."""
    from rapidocr import LangRec
    from pdf_router.ocr import _rapidocr_language

    cfg = RouterConfig()
    cfg.routing.language = None
    assert _rapidocr_language(cfg) == LangRec.ESLAV


def test_rapidocr_routing_language_en_maps_to_english() -> None:
    """English corpus → PP-OCRv5 english rec."""
    from rapidocr import LangRec
    from pdf_router.ocr import _rapidocr_language

    cfg = RouterConfig()
    cfg.routing.language = "en"
    assert _rapidocr_language(cfg) == LangRec.EN
