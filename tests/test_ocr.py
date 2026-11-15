"""Tests for OCR dispatcher — RT-06 routing to configured engine.

Covers the A1 implementation: ``pdf_inspector_ocr`` uses
``process_pdf_with_ocr(mode='auto')`` per-page, with tuning params plumbed
through from the ``[ocr.<engine>]`` section (``RouterConfig.ocr_engines``).
The two library paths are env-wired (``PDFIUM_LIB_PATH``/``ORT_DYLIB_PATH``)
and must NEVER be written by the library — tests monkeypatch them.
"""

from __future__ import annotations

import inspect
import subprocess
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from pdf_router.config import RouterConfig
from pdf_router.ocr import OcrError, _ocr_pdf_inspector, ocr_page

# Real runtime paths exist in the env (libpdfium.so + libonnxruntime.so).
# Used as monkeypatched env values so the binary-presence guard passes.
# Repo-relative so the suite runs on any checkout (dev box and CI alike).
_RUNTIME_DIR = Path(__file__).resolve().parents[1] / ".runtime"
RUNTIME_PDFIUM = str(_RUNTIME_DIR / "lib" / "libpdfium.so")
RUNTIME_ORT = str(
    _RUNTIME_DIR / "onnxruntime-linux-x64-1.27.0" / "lib" / "libonnxruntime.so"
)


def _wire_runtime_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """Point the env at the real .runtime binaries (restored at teardown)."""
    p_pdfium = Path(RUNTIME_PDFIUM)
    p_ort = Path(RUNTIME_ORT)
    if not (p_pdfium.is_file() and p_ort.is_file()):
        p_pdfium.parent.mkdir(parents=True, exist_ok=True)
        p_pdfium.touch(exist_ok=True)
        p_ort.parent.mkdir(parents=True, exist_ok=True)
        p_ort.touch(exist_ok=True)
    monkeypatch.setenv("PDFIUM_LIB_PATH", RUNTIME_PDFIUM)
    monkeypatch.setenv("ORT_DYLIB_PATH", RUNTIME_ORT)


def _tiny_pdf(tmp_path) -> str:
    """A real 1-page PDF: ``_ocr_pdf_inspector`` validates magic bytes
    before calling the Rust engine (ADR-0004 PDF-only refusal)."""
    import pymupdf

    p = tmp_path / "x.pdf"
    d = pymupdf.open()
    d.new_page()
    p.write_bytes(d.tobytes())
    d.close()
    return str(p)


def test_pdf_inspector_engine_signature_unchanged() -> None:
    """A1 must keep ``ocr_page(pdf_path, page_idx, config) -> (md, engine)``.

    Refactoring the dispatcher would touch ``core.py``; this guard
    prevents that.
    """
    sig = inspect.signature(ocr_page)
    params = list(sig.parameters.keys())
    assert params == ["pdf_path", "page_idx", "config"]


def test_pdf_inspector_ocr_calls_process_pdf_with_ocr(monkeypatch, tmp_path) -> None:
    """A1: ``_ocr_pdf_inspector`` must use ``process_pdf_with_ocr``.

    Mocks the entire pdf_inspector module to assert:
    1. mode='auto' (selective OCR: only on pages pdf-inspector routes there)
    2. page_numbers=[page_idx + 1] (1-indexed)
    3. dpi=... (default 150.0)
    4. minimum_confidence=... (default 0.0)
    5. offline=... plumbed from params (default True)
    """
    _wire_runtime_env(monkeypatch)
    fake_page = MagicMock()
    fake_page.page_number = 5
    fake_page.markdown = "ocr text"

    fake_result = MagicMock()
    fake_result.pages = [fake_page]

    fake_pdf_inspector = MagicMock()
    fake_pdf_inspector.process_pdf_with_ocr.return_value = fake_result

    pdf_path = _tiny_pdf(tmp_path)
    with patch.dict("sys.modules", {"pdf_inspector": fake_pdf_inspector}):
        result = _ocr_pdf_inspector(
            pdf_path,
            page_idx=4,
            params={
                "offline": True,
                "dpi": 200.0,
                "minimum_confidence": 0.5,
            },
        )

    assert result == "ocr text"
    fake_pdf_inspector.process_pdf_with_ocr.assert_called_once()
    call = fake_pdf_inspector.process_pdf_with_ocr.call_args
    assert call.args[0] == pdf_path
    assert call.kwargs["mode"] == "auto"
    assert call.kwargs["page_numbers"] == [5]  # 1-indexed
    assert call.kwargs["dpi"] == 200.0
    assert call.kwargs["minimum_confidence"] == 0.5
    assert call.kwargs["offline"] is True
    assert call.kwargs["model_directory"] is None  # no model_cache_dir in params

def test_pdf_inspector_ocr_default_params(monkeypatch, tmp_path) -> None:
    """Default dpi=150.0, minimum_confidence=0.0, offline=True when params are empty."""
    _wire_runtime_env(monkeypatch)
    fake_page = MagicMock()
    fake_page.page_number = 1
    fake_page.markdown = "x"

    fake_result = MagicMock()
    fake_result.pages = [fake_page]

    fake_pdf_inspector = MagicMock()
    fake_pdf_inspector.process_pdf_with_ocr.return_value = fake_result

    with patch.dict("sys.modules", {"pdf_inspector": fake_pdf_inspector}):
        _ocr_pdf_inspector(_tiny_pdf(tmp_path), page_idx=0, params={})

    call = fake_pdf_inspector.process_pdf_with_ocr.call_args
    assert call.kwargs["dpi"] == 150.0
    assert call.kwargs["minimum_confidence"] == 0.0
    assert call.kwargs["offline"] is True


def test_pdf_inspector_ocr_passes_model_directory(monkeypatch, tmp_path) -> None:
    """``model_cache_dir`` goes to the ``model_directory`` kwarg — not to env."""
    _wire_runtime_env(monkeypatch)
    fake_page = MagicMock()
    fake_page.page_number = 1
    fake_page.markdown = "x"

    fake_result = MagicMock()
    fake_result.pages = [fake_page]

    fake_pdf_inspector = MagicMock()
    fake_pdf_inspector.process_pdf_with_ocr.return_value = fake_result

    with patch.dict("sys.modules", {"pdf_inspector": fake_pdf_inspector}):
        _ocr_pdf_inspector(
            _tiny_pdf(tmp_path), page_idx=0, params={"model_cache_dir": "/tmp/models"}
        )

    call = fake_pdf_inspector.process_pdf_with_ocr.call_args
    assert call.kwargs["model_directory"] == "/tmp/models"


def test_pdf_inspector_ocr_does_not_write_environ(monkeypatch, tmp_path) -> None:
    """Library contract: ``_ocr_pdf_inspector`` reads env, never writes it.

    Regression: the pre-facade code set PDFIUM_LIB_PATH/ORT_DYLIB_PATH (and
    a dead LD_LIBRARY_PATH prepend) on every page call from a worker thread.
    """
    _wire_runtime_env(monkeypatch)
    before = dict(__import__("os").environ)

    fake_page = MagicMock()
    fake_page.page_number = 1
    fake_page.markdown = "x"
    fake_result = MagicMock()
    fake_result.pages = [fake_page]
    fake_pdf_inspector = MagicMock()
    fake_pdf_inspector.process_pdf_with_ocr.return_value = fake_result

    with patch.dict("sys.modules", {"pdf_inspector": fake_pdf_inspector}):
        _ocr_pdf_inspector(_tiny_pdf(tmp_path), page_idx=0, params={})

    assert dict(__import__("os").environ) == before


def test_pdf_inspector_ocr_env_not_wired_raises(monkeypatch, tmp_path) -> None:
    """No env → hard error with the wiring instruction, no silent fallback."""
    monkeypatch.delenv("PDFIUM_LIB_PATH", raising=False)
    monkeypatch.delenv("ORT_DYLIB_PATH", raising=False)
    with pytest.raises(OcrError, match="env not wired"):
        _ocr_pdf_inspector(_tiny_pdf(tmp_path), page_idx=0, params={})


def test_pdf_inspector_ocr_raises_on_empty_result(monkeypatch, tmp_path) -> None:
    """Empty markdown from pdf-inspector must raise OcrError, not return ''."""
    _wire_runtime_env(monkeypatch)
    fake_page = MagicMock()
    fake_page.page_number = 3
    fake_page.markdown = ""  # empty

    fake_result = MagicMock()
    fake_result.pages = [fake_page]

    fake_pdf_inspector = MagicMock()
    fake_pdf_inspector.process_pdf_with_ocr.return_value = fake_result

    with patch.dict("sys.modules", {"pdf_inspector": fake_pdf_inspector}):
        with pytest.raises(OcrError, match="empty pdf_inspector_ocr result"):
            _ocr_pdf_inspector(_tiny_pdf(tmp_path), page_idx=2, params={})


def test_pdf_inspector_ocr_raises_if_page_missing(monkeypatch, tmp_path) -> None:
    """If pdf-inspector returns pages for a different number, raise OcrError."""
    _wire_runtime_env(monkeypatch)
    fake_page = MagicMock()
    fake_page.page_number = 99  # wrong page
    fake_page.markdown = "x"

    fake_result = MagicMock()
    fake_result.pages = [fake_page]

    fake_pdf_inspector = MagicMock()
    fake_pdf_inspector.process_pdf_with_ocr.return_value = fake_result

    with patch.dict("sys.modules", {"pdf_inspector": fake_pdf_inspector}):
        with pytest.raises(OcrError, match="missing from pdf_inspector_ocr result"):
            _ocr_pdf_inspector(_tiny_pdf(tmp_path), page_idx=0, params={})


def test_ocr_page_dispatches_to_engine(monkeypatch, tmp_path) -> None:
    """ocr_page routes the right engine to its implementation.

    With bogus env paths, the binary-presence guard fires before
    pdf_inspector is even called — so we get ``OcrError(binaries not found)``.
    A real PDF is required: the engine validates magic bytes first.
    """
    monkeypatch.setenv("PDFIUM_LIB_PATH", "/nonexistent/libpdfium.so")
    monkeypatch.setenv("ORT_DYLIB_PATH", "/nonexistent/libonnxruntime.so")
    cfg = RouterConfig()
    cfg.routing.ocr_fallback_engine = "pdf_inspector_ocr"
    cfg.ocr_engines["pdf_inspector_ocr"] = {
        "pdfium_lib_path": "/nonexistent/libpdfium.so",
        "ort_dylib_path": "/nonexistent/libonnxruntime.so",
    }

    with pytest.raises(OcrError, match="binaries not found"):
        ocr_page(_tiny_pdf(tmp_path), page_idx=0, config=cfg)


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
    from rapidocr import LangRec, ModelType, OCRVersion

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


@pytest.mark.slow
def test_rapidocr_engine_works_in_fresh_subprocess_without_numpy() -> None:
    """OpenMP init-order regression (candidate #5 investigation).

    onnxruntime initializes its own OpenMP runtime at session build; if
    numpy/OpenBLAS is not loaded first, the runtimes collide and the
    first engine(img) call hangs forever in a fresh process (reproduced
    on 2-CPU Linux). _rapidocr_get_engine imports numpy before rapidocr
    to pin the order — this test runs the exact scenario in a clean
    subprocess: no numpy preloaded, engine built, one tiny inference.
    """
    code = (
        "import sys\n"
        "assert 'numpy' not in sys.modules, 'test premise broken: numpy preloaded'\n"
        "from pdf_router.config import RouterConfig\n"
        "from pdf_router.ocr import _rapidocr_get_engine\n"
        "engine = _rapidocr_get_engine(RouterConfig())\n"
        "import numpy as np\n"
        "img = (np.random.rand(64, 256, 3) * 255).astype('uint8')\n"
        "engine(img)\n"
        "print('INFERENCE-OK')\n"
    )
    proc = subprocess.run(
        [sys.executable, "-c", code],
        capture_output=True,
        text=True,
        timeout=300,
    )
    assert proc.returncode == 0, f"stderr:\n{proc.stderr}"
    assert "INFERENCE-OK" in proc.stdout


def test_rapidocr_missing_dependency_raises_actionable_error() -> None:
    """ADR-0011: when ocr-local extra is not installed, fail with actionable message."""
    from pdf_router.ocr import _rapidocr_language, _rapidocr_build_engine, _rapidocr_get_engine
    with patch.dict("sys.modules", {"rapidocr": None}):
        cfg = RouterConfig()
        with pytest.raises(OcrError, match="ocr-local"):
            _rapidocr_language(cfg)
        with pytest.raises(OcrError, match="ocr-local"):
            _rapidocr_build_engine(cfg)
        with pytest.raises(OcrError, match="ocr-local"):
            _rapidocr_get_engine(cfg)
