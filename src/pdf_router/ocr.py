"""OCR dispatcher — flat if/elif on engine name.

One function: ``ocr_page``. No adapter registry, no lazy imports, no Protocol.

Engine-specific params come from the matching ``OCRAdapterSpec`` in
``RouterConfig.ocr_specs`` (the ``[ocr]`` TOML section). If the engine
is not configured, an empty dict is used as a fallback.

Engines supported:
- ``pdf_inspector_ocr`` — PP-OCRv6 Small via pdf-inspector Rust + ONNX.
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


def _ocr_pdf_inspector(pdf_path: str, page_idx: int, params: dict[str, Any]) -> str:
    """OCR via pdf-inspector's selective OCR pipeline.

    Uses ``process_pdf_with_ocr(mode='force')`` for one page at a time.
    mode='force' because our orchestrator has already decided this page
    needs OCR (either via page.needs_ocr from pdf-inspector, or because
    looks_like_text() failed on the native text). At that point we do not
    want pdf-inspector's auto-decision to second-guess us — e.g. it might
    see a CID-font garbled page and return its own native text instead
    of OCR. mode='force' guarantees OCR.

    Per-page call instead of one batched call: keeps ocr_page()'s
    (path, page_idx) signature unchanged, so core.py does not need a
    refactor. PDFium calls are cheap (~10ms) so per-page overhead is
    acceptable. A batched variant is a v0.5+ optimization if needed.

    Requires ``pdfium_lib_path`` and ``ort_dylib_path`` in params. These are
    the PDFium shared library and the ONNX Runtime dylib that pdf-inspector
    needs to actually run its PP-OCRv6 model.

    The returned ``OcrPageResult.provenance`` (source, ocr_model,
    ocr_confidence, render_dpi, warnings) is available on the result but
    not surfaced to PageResult in v0.3 — it is wired up in v0.4+.
    """
    import os

    pdfium_lib = params.get("pdfium_lib_path")
    ort_dylib = params.get("ort_dylib_path")
    model_cache = params.get("model_cache_dir")
    offline = bool(params.get("offline", False))

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
        mode="force",
        page_numbers=[page_idx + 1],  # 1-indexed
        offline=offline,
    )
    for pg in result.pages:
        if pg.page_number == page_idx + 1:
            md = pg.markdown or ""
            if not md.strip():
                raise OcrError(f"page {page_idx + 1}: empty pdf_inspector_ocr result")
            # pg.provenance has source/ocr_model/ocr_confidence if we want
            # to surface it on PageResult later.
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
    ``pdf_inspector_ocr``). Params come from the matching ``OCRAdapterSpec`` in
    ``config.ocr_specs``; if not found, an empty dict is used.
    """
    engine = config.routing.ocr_fallback_engine
    params = _lookup_params(config, engine)

    if engine == "pdf_inspector_ocr":
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
