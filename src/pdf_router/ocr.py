"""OCR dispatcher — flat if/elif on engine name.

One function: ``ocr_page``. No adapter registry, no lazy imports, no Protocol.

Engine-specific params come from the matching ``OCRAdapterSpec`` in
``RouterConfig.ocr_specs`` (the ``[ocr]`` TOML section) or, with priority,
from ``routing.ocr_fallback_params``. If neither is set, an empty dict is
used as a fallback.

Engines supported:
- ``rapidocr`` — RapidAI RapidOCR on onnxruntime. Default. Recipe for
  Russian: PP-OCRv6 small det + PP-OCRv5 eslav mobile rec (v6 rec has no
  Cyrillic head at all, and eslav has no server ONNX — both verified).
- ``pdf_inspector_ocr`` — PP-OCRv6 Small via pdf-inspector Rust + ONNX.
  Cannot do Cyrillic (dictionary has zero Cyrillic tokens).
- ``tesseract`` — local CLI, no API key required.
- ``glm_ocr`` — Z.ai layout_parsing API, requires api_key.
- ``deepseek_ocr`` — Novita AI deepseek-ocr-2, requires api_key.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

from pdf_router.config import OCRAdapterSpec, RouterConfig
from pdf_router.ir import SrcTool
from pdf_router.rasterize import rasterize_page, RasterizeError

logger = logging.getLogger("pdf_router")


class OcrError(RuntimeError):
    """Any OCR failure."""


def _lookup_params(config: RouterConfig, engine: str) -> dict[str, Any]:
    """Find params for engine.

    Priority: routing.ocr_fallback_params (v0.3) > ocr_specs (v0.2 compat).
    """
    # v0.3: params passed directly via routing config.
    if config.routing.ocr_fallback_params:
        return dict(config.routing.ocr_fallback_params)
    # v0.2 compat: look up from ocr_specs.
    for spec in config.ocr_specs:
        if spec.name == engine:
            return dict(spec.params)
    return {}


# ---------------------------------------------------------------------------
# Engine implementations (flat functions, no class)
# ---------------------------------------------------------------------------


# Module-level engine cache for rapidocr: model init (even warm, from the
# ModelScope cache) takes ~0.6s and loads two ONNX sessions; doing that on
# every page would be wasteful. Keyed by the resolved recipe so a params
# change creates a fresh engine instead of silently reusing the old one.
_RAPIDOCR_ENGINE_CACHE: dict[tuple, Any] = {}

# routing.language -> rapidocr LangRec. Det is always the multilingual v6
# model (LangDet.CH in rapidocr terms); only Rec is language-sensitive.
# No language hint defaults to ESLAV (most likely Russian corpus for this
# project; in v0.4+ we can wire a script-detection pre-pass).
_LANGUAGE_TO_LANG_REC = {
    "ru": "ESLAV",       # Russian/Belarusian/Ukrainian head, PP-OCRv5 mobile
    "be": "ESLAV",
    "uk": "ESLAV",
    "en": "EN",          # English/Latin head
    "bg": "CYRILLIC",   # generic Cyrillic head (35 languages)
    "sr": "CYRILLIC",
    "mk": "CYRILLIC",
    "kk": "CYRILLIC",
    None: "ESLAV",      # default — see comment above
}
_LANG_REC_DEFAULT = "ESLAV"


def _rapidocr_language(config: RouterConfig):
    """Resolve routing.language to a rapidocr LangRec enum.

    Public for tests. Returns a LangRec member; default is ESLAV
    (project's primary corpus is Russian).
    """
    from rapidocr import LangRec

    name = _LANGUAGE_TO_LANG_REC.get(config.routing.language, _LANG_REC_DEFAULT)
    return getattr(LangRec, name)


def _rapidocr_build_engine(config: RouterConfig) -> dict[str, Any]:
    """Build the rapidocr params dict for the configured recipe.

    Public for tests. Does not instantiate the engine — that is the job of
    ``_rapidocr_get_engine``. Returns a dict of dotted keys suitable for
    ``RapidOCR(params=...)``.

    Recipe defaults — the SOTA combo validated in TKT-OC-02 on pzu:
    Det PP-OCRv6 small (multilingual) + Rec PP-OCRv5 mobile. Rec language
    comes from ``routing.language``; with no hint we fall back to ESLAV
    (project's primary corpus).
    """
    from rapidocr import EngineType, LangDet, ModelType, OCRVersion

    params = config.routing.ocr_fallback_params or {}
    det_model_type = str(params.get("det_model_type", "small"))
    rec_model_type = str(params.get("rec_model_type", "mobile"))

    return {
        "Global.use_cls": False,
        "Det.engine_type": EngineType.ONNXRUNTIME,
        "Det.ocr_version": OCRVersion.PPOCRV6,
        "Det.model_type": getattr(ModelType, det_model_type.upper()),
        "Det.lang_type": LangDet.CH,  # v6 det is one multilingual model
        "Rec.engine_type": EngineType.ONNXRUNTIME,
        "Rec.ocr_version": OCRVersion.PPOCRV5,
        "Rec.model_type": getattr(ModelType, rec_model_type.upper()),
        "Rec.lang_type": _rapidocr_language(config),
    }


def _rapidocr_get_engine(config: RouterConfig):
    """Construct (or fetch cached) RapidOCR engine for the configured recipe.

    Module-level cache — model init (even warm, from the ModelScope cache)
    takes ~0.6s and loads two ONNX sessions; doing that on every page
    would be wasteful. Keyed by the resolved recipe so a params change
    creates a fresh engine instead of silently reusing the old one.
    """
    params = _rapidocr_build_engine(config)
    cache_key = tuple(sorted(params.items()))
    cached = _RAPIDOCR_ENGINE_CACHE.get(cache_key)
    if cached is not None:
        return cached

    from rapidocr import RapidOCR

    engine = RapidOCR(params=params)
    _RAPIDOCR_ENGINE_CACHE[cache_key] = engine
    return engine


def _ocr_rapidocr(pdf_path: str, page_idx: int, config: RouterConfig) -> str:
    """OCR via RapidOCR (RapidAI) on onnxruntime.

    Renders the page with pymupdf and feeds the numpy RGB array to the
    engine. Result boxes are already y-sorted; v1 reading order is a plain
    join of lines (column interleaving on drawings is acceptable — see
    wayfinder map).

    Tuning knobs (all in ``routing.ocr_fallback_params``):
    - ``dpi`` (default 150): raster resolution. Probe showed OCR time is
      roughly dpi-independent (24-40s/page CPU-only) while 100 hallucinates.
    - ``det_model_type`` / ``rec_model_type``: "small"/"mobile" by default;
      "tiny"/"medium"/"server" variants exist for tuning.

    Language hint comes from ``routing.language`` via ``_rapidocr_language``
    (not from this function's params).
    """
    import numpy as np
    import pymupdf

    params = config.routing.ocr_fallback_params or {}
    dpi = int(params.get("dpi", 150))

    try:
        engine = _rapidocr_get_engine(config)
    except Exception as exc:
        raise OcrError(f"rapidocr engine init failed: {exc}") from exc

    doc = pymupdf.open(pdf_path)
    try:
        if not 0 <= page_idx < doc.page_count:
            raise OcrError(f"page index {page_idx} out of range")
        page = doc[page_idx]
        pix = page.get_pixmap(dpi=dpi, alpha=False)
        img = np.frombuffer(pix.samples, dtype=np.uint8).reshape(
            pix.height, pix.width, pix.n
        )
        if pix.n == 4:
            img = img[:, :, :3]
    finally:
        doc.close()

    try:
        result = engine(img)
    except Exception as exc:
        raise OcrError(f"rapidocr inference failed: {exc}") from exc

    lines = [str(t) for t in (result.txts or []) if str(t).strip()]
    if not lines:
        raise OcrError(f"page {page_idx + 1}: empty rapidocr result")
    return "\n".join(lines)


def _ocr_pdf_inspector(pdf_path: str, page_idx: int, params: dict[str, Any]) -> str:
    """OCR via pdf-inspector's selective OCR pipeline.

    Uses ``process_pdf_with_ocr(mode='auto')`` for one page at a time.

    mode='auto' is what pdf-inspector calls "selective OCR": it runs OCR
    only on pages where its own quality signals recommend it, and keeps
    native text for the rest. Our orchestrator has already decided this
    page needs OCR (either via page.needs_ocr, or because looks_like_text()
    failed on the native text), so we pass page_numbers=[page_idx+1] to
    scope the call, and trust mode='auto' to actually OCR it. This is
    much faster than mode='force' (which OCRs every page we pass) on PDFs
    where most pages have good native text and only a few need OCR.

    Per-page call instead of one batched call: keeps ocr_page()'s
    (path, page_idx) signature unchanged, so core.py does not need a
    refactor. PDFium calls are cheap (~10ms) so per-page overhead is
    acceptable. A batched variant is a v0.5+ optimization if needed.

    Requires ``pdfium_lib_path`` and ``ort_dylib_path`` in params. These are
    the PDFium shared library and the ONNX Runtime dylib that pdf-inspector
    needs to actually run its PP-OCRv6 model.

    Tuning knobs (all optional, safe defaults):
    - ``dpi`` (default 150.0): raster resolution for the render step.
      100 is faster but hallucinates. 200/300 are slower, not better.
    - ``minimum_confidence`` (default 0.0): drop regions below this threshold.
      0.8 would cut ~12% text on complex docs — too aggressive for
      documentation. Keep 0.0.
    - ``offline`` (default True): skip hosted-OCR recommendation checks
      and block model downloads. Set False only if you accept the network
      round-trip to fetch a missing pinned model artifact on first use.
    - ``model_cache_dir``: override the local model cache directory
      (default ``~/.cache/pdf-inspector/models``).
    """
    import os

    pdfium_lib = params.get("pdfium_lib_path")
    ort_dylib = params.get("ort_dylib_path")
    model_cache = params.get("model_cache_dir")
    dpi = float(params.get("dpi", 150.0))
    minimum_confidence = float(params.get("minimum_confidence", 0.0))
    offline = bool(params.get("offline", True))

    if not (pdfium_lib and ort_dylib):
        raise OcrError("pdf_inspector_ocr requires pdfium_lib_path and ort_dylib_path")
    if not (Path(pdfium_lib).is_file() and Path(ort_dylib).is_file()):
        raise OcrError(f"pdf_inspector_ocr binaries not found: {pdfium_lib}, {ort_dylib}")

    # Wire env for the Rust core.
    os.environ["PDFIUM_LIB_PATH"] = str(pdfium_lib)
    os.environ["ORT_DYLIB_PATH"] = str(ort_dylib)
    ort_dir = str(Path(ort_dylib).parent)
    ld = os.environ.get("LD_LIBRARY_PATH", "")
    if ort_dir not in ld.split(":"):
        os.environ["LD_LIBRARY_PATH"] = f"{ort_dir}:{ld}" if ld else ort_dir
    if model_cache:
        os.environ.setdefault("PDF_INSPECTOR_MODEL_CACHE", str(model_cache))

    import pdf_inspector

    result = pdf_inspector.process_pdf_with_ocr(
        pdf_path,
        mode="auto",
        page_numbers=[page_idx + 1],  # 1-indexed
        dpi=dpi,
        minimum_confidence=minimum_confidence,
        offline=offline,
    )
    for pg in result.pages:
        if pg.page_number == page_idx + 1:
            md = pg.markdown or ""
            if not md.strip():
                raise OcrError(f"page {page_idx + 1}: empty pdf_inspector_ocr result")
            return md
    raise OcrError(f"page {page_idx + 1} missing from pdf_inspector_ocr result")
def _ocr_tesseract(pdf_path: str, page_idx: int, params: dict[str, Any]) -> str:
    """OCR via tesseract CLI subprocess.

    Renders page to PNG via ``rasterize_page`` (shared helper, 200 DPI).
    Requires ``tesseract`` binary; ``languages`` defaults to ``eng``.
    """
    import shutil
    import subprocess

    bin_name = params.get("bin") or "tesseract"
    languages = params.get("languages") or ["eng"]
    lang_str = "+".join(languages) if isinstance(languages, list) else str(languages)
    dpi = int(params.get("dpi", 200))

    if shutil.which(bin_name) is None:
        raise OcrError(f"tesseract binary not found: {bin_name}")

    png = rasterize_page(pdf_path, page_idx, dpi=dpi)
    proc = subprocess.run(
        [bin_name, "stdin", "stdout", "-l", lang_str, "--dpi", str(dpi)],
        input=png,
        capture_output=True,
    )
    if proc.returncode != 0:
        raise OcrError(
            f"tesseract exited {proc.returncode}: "
            + proc.stderr.decode(errors="replace")[:300]
        )
    text = proc.stdout.decode("utf-8", errors="replace").strip()
    if not text:
        raise OcrError(f"page {page_idx + 1}: empty tesseract result")
    return text


def _ocr_glm(pdf_path: str, page_idx: int, params: dict[str, Any]) -> str:
    """OCR via Z.ai GLM layout_parsing API.

    Renders page to a single-page PDF (PyMuPDF insert_pdf) and POSTs it as
    base64 data-URL. Returns markdown from ``md_results``.
    Requires ``api_key`` in params; ``base_url`` defaults to Z.ai endpoint.
    """
    import base64

    api_key = params.get("api_key")
    base_url = params.get("base_url", "https://api.z.ai/api/paas/v4")
    model = params.get("model", "glm-ocr")
    timeout = float(params.get("timeout", 300.0))

    if not api_key:
        raise OcrError("glm_ocr requires api_key")

    try:
        import pymupdf
    except Exception as exc:
        raise OcrError(f"pymupdf not importable: {exc}") from exc

    try:
        import requests
    except Exception as exc:
        raise OcrError(f"requests not importable (install [cloud] extra): {exc}") from exc

    # Build single-page PDF.
    src = pymupdf.open(pdf_path)
    try:
        if not (0 <= page_idx < src.page_count):
            raise OcrError(f"page index {page_idx} out of range")
        single = pymupdf.open()
        single.insert_pdf(src, from_page=page_idx, to_page=page_idx)
        pdf_bytes = single.tobytes()
    finally:
        src.close()

    data_url = "data:application/pdf;base64," + base64.b64encode(pdf_bytes).decode("ascii")

    try:
        r = requests.post(
            f"{base_url.rstrip('/')}/layout_parsing",
            json={"model": model, "file": data_url},
            headers={
                "Content-Type": "application/json",
                "Authorization": f"Bearer {api_key}",
            },
            timeout=timeout,
        )
        r.raise_for_status()
    except Exception as exc:
        raise OcrError(f"glm_ocr layout_parsing failed: {exc}") from exc

    md = r.json().get("md_results")
    if not isinstance(md, str) or not md.strip():
        raise OcrError("glm_ocr: empty md_results in response")
    return md.strip()


def _ocr_deepseek(pdf_path: str, page_idx: int, params: dict[str, Any]) -> str:
    """OCR via Novita AI deepseek-ocr-2.

    Renders page to PNG (200 DPI), uploads to uguu.se, POSTs to
    OpenAI-compatible /chat/completions endpoint. Requires ``api_key`` in params.
    """
    api_key = params.get("api_key")
    base_url = params.get("base_url", "https://api.novita.ai/openai")
    model = params.get("model", "deepseek/deepseek-ocr-2")
    upload_url = params.get("upload_url", "https://uguu.se/upload")
    prompt = params.get("prompt", "OCR this image.")
    timeout = float(params.get("timeout", 60.0))
    max_tokens = int(params.get("max_tokens", 4096))
    dpi = int(params.get("dpi", 200))

    if not api_key:
        raise OcrError("deepseek_ocr requires api_key")

    try:
        import requests
    except Exception as exc:
        raise OcrError(f"requests not importable (install [cloud] extra): {exc}") from exc

    # Render and upload.
    png = rasterize_page(pdf_path, page_idx, dpi=dpi)
    host = upload_url.lower()
    if "uguu" in host:
        files = {"files[]": ("page.png", png, "image/png")}
        data = None
    else:
        files = {"fileToUpload": ("page.png", png, "image/png")}
        data = {"reqtype": "fileupload"}

    try:
        r = requests.post(upload_url, data=data, files=files, timeout=timeout)
        r.raise_for_status()
    except Exception as exc:
        raise OcrError(f"deepseek_ocr image upload failed: {exc}") from exc

    # Parse upload response.
    try:
        payload = r.json()
        uploaded = payload.get("files") or []
        if uploaded and str(uploaded[0].get("url", "")).startswith("http"):
            image_url = str(uploaded[0]["url"])
        else:
            raise OcrError("bad uguu.se response format")
    except Exception:
        # Fallback: plain-text URL.
        url_text = r.text.strip().splitlines()[-1].strip()
        if url_text.startswith("http"):
            image_url = url_text
        else:
            raise OcrError(f"deepseek_ocr: bad upload response: {r.text[:200]!r}")

    # Chat completion.
    try:
        r = requests.post(
            f"{base_url.rstrip('/')}/chat/completions",
            json={
                "model": model,
                "stream": False,
                "temperature": 0.0,
                "max_tokens": max_tokens,
                "messages": [
                    {
                        "role": "user",
                        "content": [
                            {"type": "image_url", "image_url": {"url": image_url, "detail": "high"}},
                            {"type": "text", "text": prompt},
                        ],
                    }
                ],
            },
            headers={"Content-Type": "application/json", "Authorization": f"Bearer {api_key}"},
            timeout=timeout,
        )
        r.raise_for_status()
    except Exception as exc:
        raise OcrError(f"deepseek_ocr chat/completions failed: {exc}") from exc

    choices = r.json().get("choices") or []
    if not choices:
        raise OcrError("deepseek_ocr: no choices in response")
    content = (choices[0].get("message") or {}).get("content")
    if not isinstance(content, str) or not content.strip():
        raise OcrError("deepseek_ocr: empty content in response")
    return content.strip()


# ---------------------------------------------------------------------------
# Public dispatcher
# ---------------------------------------------------------------------------


def ocr_page(
    pdf_path: str,
    page_idx: int,
    config: RouterConfig,
) -> tuple[str, str]:
    """Run OCR on one page using the configured fallback engine.

    Returns ``(markdown, engine_name)``.

    Args:
        pdf_path: path to the PDF file.
        page_idx: 0-indexed page number.
        config: full RouterConfig (contains both ``routing`` and ``ocr_specs``).

    Raises:
        OcrError: if the engine is unknown, unavailable, or returns empty/fails.

    The engine name comes from ``config.routing.ocr_fallback_engine`` (default
    ``rapidocr``). Params come from ``routing.ocr_fallback_params`` or the
    matching ``OCRAdapterSpec`` in ``config.ocr_specs``; if not found, an
    empty dict is used.
    """
    engine = config.routing.ocr_fallback_engine
    params = _lookup_params(config, engine)

    if engine == "rapidocr":
        # rapidocr reads its own knobs (dpi, det/rec model_type) from
        # config.routing.ocr_fallback_params and the language hint from
        # config.routing.language; the dispatcher does not plumb either.
        md = _ocr_rapidocr(pdf_path, page_idx, config)
        return md, engine
    elif engine == "pdf_inspector_ocr":
        md = _ocr_pdf_inspector(pdf_path, page_idx, params)
        return md, engine
    elif engine == "tesseract":
        md = _ocr_tesseract(pdf_path, page_idx, params)
        return md, engine
    elif engine == "glm_ocr":
        md = _ocr_glm(pdf_path, page_idx, params)
        return md, engine
    elif engine == "deepseek_ocr":
        md = _ocr_deepseek(pdf_path, page_idx, params)
        return md, engine
    else:
        raise OcrError(f"unknown ocr_fallback_engine: {engine!r}")
