"""OCR dispatcher — flat if/elif on engine name.

One function: ``ocr_page``. No adapter registry, no lazy imports, no Protocol.

Engine-specific params come from the matching ``[ocr.<engine>]`` section,
i.e. ``RouterConfig.ocr_engines[engine]``. If no section exists, an empty
dict is used (rapidocr works on built-in defaults).

Engines supported:
- ``rapidocr`` — RapidAI RapidOCR on onnxruntime. Default. Recipe for
  Russian: PP-OCRv6 small det + PP-OCRv5 eslav mobile rec (v6 rec has no
  Cyrillic head at all, and eslav has no server ONNX — both verified).
- ``pdf_inspector_ocr`` — PP-OCRv6 Small via pdf-inspector Rust + ONNX.
  Cannot do Cyrillic (dictionary has zero Cyrillic tokens).
  PDF-only by nature (Rust pdfium core) — refuses djvu input (ADR-0004).
- ``tesseract`` — local CLI, no API key required.
- ``glm_ocr`` — Z.ai layout_parsing API, requires api_key.
- ``deepseek_ocr`` — Novita AI deepseek-ocr-2, requires api_key.

Cloud engines retry transient HTTP failures (429/5xx, network) with
exponential backoff — ``max_retries`` (default 2, 0 disables) and
``retry_backoff_s`` (default 1.0) in their ``[ocr.*]`` sections.

All engines acquire page rasters through :mod:`pdf_router.rasterize`
(the format-aware seam, ADR-0004): pdf -> pymupdf, djvu -> ddjvu. Engine
signatures keep ``(path, page_idx)``; the format dispatch lives below
them. Only ``pdf_inspector_ocr`` is exempt — its Rust core opens PDFs
itself, so djvu is a typed refusal there.

IR 0.5 (ADR-0005): ``ocr_page`` returns ``(markdown, engine_name, blocks)``.
Blocks come from :mod:`pdf_router.structure` — the full geometry pass for
rapidocr (raster + boxes are alive right here; rasterize-once), glm_ocr's
``layout_details`` for the cloud engine, and the pipe-markdown fallback
for boxless engines. A structure failure degrades to no blocks; it never
fails a page whose OCR text is already good.
"""

from __future__ import annotations

import base64
import logging
import random
import threading
import time
from pathlib import Path
from typing import Any

from pdf_router.config import RouterConfig
from pdf_router.rasterize import page_raster_array, page_single_pdf, rasterize_page
from pdf_router.structure import (
    blocks_from_glm,
    blocks_from_pipe_markdown,
    structure_page,
)

logger = logging.getLogger("pdf_router")


class OcrError(RuntimeError):
    """Any OCR failure."""


def _lookup_params(config: RouterConfig, engine: str) -> dict[str, Any]:
    """Params for ``engine``: the matching ``[ocr.<engine>]`` section."""
    return dict(config.ocr_engines.get(engine, {}))


# Transient-by-nature HTTP statuses worth a retry: rate limiting and the
# classic bad-gateway/server-blip family. Everything else (401 bad key,
# 400 bad request, 422) is deterministic — a repeat buys the same answer
# for the same money and the same latency.
_RETRYABLE_STATUS = frozenset({429, 500, 502, 503, 504})


def _post_with_retry(
    url: str,
    *,
    json: dict,
    headers: dict,
    timeout: float,
    max_retries: int = 2,
    backoff_s: float = 1.0,
) -> Any:
    """POST with exponential backoff on transient failures (cloud engines).

    Retries: 429/500/502/503/504 and network errors (ConnectionError,
    Timeout) — up to ``max_retries`` extra attempts with delay
    ``backoff_s * 2**attempt`` + uniform jitter (two workers must not
    wake in lockstep and re-collide). A provider ``Retry-After`` header
    (integer seconds) wins over our own backoff. Deterministic 4xx fails
    on the first attempt.

    On exhaustion the LAST response/exception is returned/raised — the
    caller's ``raise_for_status()``/wrap keeps the error message
    truthful about the final attempt. Each attempt carries its own
    ``timeout`` (the per-call cap from ``[ocr.*].timeout``), so the
    worst-case page latency is ``(max_retries + 1) * timeout`` plus
    backoff — bounded, as opposed to a lost page or a hung worker.

    Known honest trade-off (glm): a coding-plan key gets 429 "Insufficient
    balance" — a permanent cause behind a transient code. Retrying wastes
    a couple of short attempts, but the final OcrError carries the body,
    so the real cause still surfaces. Rate limiting, the far more common
    429, is genuinely transient — retrying it is the point.
    """
    import requests

    for attempt in range(max_retries + 1):
        net_exc: Exception | None = None
        try:
            r = requests.post(url, json=json, headers=headers, timeout=timeout)
        except (requests.ConnectionError, requests.Timeout) as exc:
            net_exc = exc
        else:
            if r.status_code not in _RETRYABLE_STATUS:
                return r  # final answer: success or deterministic failure
        if attempt == max_retries:
            if net_exc is not None:
                raise net_exc  # caller wraps it — message stays the last one
            return r  # caller's raise_for_status() turns it into the error

        # Own backoff + jitter; provider hint (integer seconds) overrides.
        delay = backoff_s * (2**attempt) + random.uniform(0.0, 0.5)
        retry_after: float | None = None
        if net_exc is None:
            raw = r.headers.get("Retry-After")
            if raw is not None:
                try:
                    retry_after = float(raw)
                except ValueError:
                    retry_after = None  # HTTP-date form: ignored
        wait = retry_after if retry_after is not None else delay
        logger.warning(
            "OCR request failed (attempt %d/%d): %s — retrying in %.1fs",
            attempt + 1,
            max_retries + 1,
            f"HTTP {r.status_code}" if net_exc is None else type(net_exc).__name__,
            wait,
        )
        time.sleep(wait)
    raise AssertionError("unreachable: retry loop returns or raises inside")


# ---------------------------------------------------------------------------
# Engine implementations (flat functions, no class)
# ---------------------------------------------------------------------------


# Module-level engine cache for rapidocr: model init (even warm, from the
# ModelScope cache) takes ~0.6s and loads two ONNX sessions; doing that on
# every page would be wasteful. Keyed by the resolved recipe so a params
# change creates a fresh engine instead of silently reusing the old one.
_RAPIDOCR_ENGINE_CACHE: dict[tuple, Any] = {}

# Guards lazy engine construction. `process_pdf` fans pages out to a
# ThreadPoolExecutor, so two OCR pages can reach `_rapidocr_get_engine`
# concurrently; without this lock both threads miss the cache and both
# build the engine (duplicate 25MB ONNX session loads, plus one
# "Using ..." init pair per worker — observed on the pzu benchmark).
# The lock also serializes RapidOCR(...) construction itself, which is
# not guaranteed thread-safe (ONNX session creation).
_RAPIDOCR_ENGINE_LOCK = threading.Lock()

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

    params = _lookup_params(config, "rapidocr")
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

    # Fast path: cache hit without the lock. dict.get is atomic under
    # the GIL, so this is safe and keeps the common (warm) case free of
    # lock contention.
    cached = _RAPIDOCR_ENGINE_CACHE.get(cache_key)
    if cached is not None:
        return cached

    # Slow path: double-checked locking — re-check inside the lock, then
    # build exactly once even under concurrent first use.
    with _RAPIDOCR_ENGINE_LOCK:
        cached = _RAPIDOCR_ENGINE_CACHE.get(cache_key)
        if cached is not None:
            return cached

        # numpy must be imported before rapidocr. onnxruntime initializes
        # its thread pool at session build; importing numpy first pins a
        # deterministic native-init order — cheap insurance against an
        # intermittent 0%-CPU futex hang observed on this 2-CPU box in
        # fresh processes during early OCR (every observed occurrence had
        # another OCR process alive concurrently; 18/18 probe samples OK
        # on a free machine, so the trigger is not fully understood).
        # No env mutation involved — import order only.
        import numpy  # noqa: F401  (imported for native init order)

        from rapidocr import RapidOCR

        engine = RapidOCR(params=params)
        _RAPIDOCR_ENGINE_CACHE[cache_key] = engine
        return engine


def _rapidocr_triples(result: Any) -> list:
    """RapidOCR result -> ``[(box(4, 2), text, score), ...]``.

    That is the input contract of the TableStructureRec engines (ST-03
    bench: rapidocr hands out flat ``(8,)`` boxes, the table engines want
    ``(4, 2)`` quads). Zip order is det-box order, matching txts/scores;
    a box that fails to reshape is dropped — structure-only loss, the
    flat markdown is untouched.
    """
    import numpy as np

    # result.boxes/txts/scores may be numpy arrays; `or` on an ndarray
    # raises (ambiguous truth). Explicit None-checks, then list().
    boxes = getattr(result, "boxes", None)
    txts = getattr(result, "txts", None)
    scores = getattr(result, "scores", None)
    boxes = [] if boxes is None else list(boxes)
    txts = [] if txts is None else list(txts)
    scores = [] if scores is None else list(scores)
    out: list = []
    # strict=False is deliberate: txts alone already built the flat markdown;
    # misaligned/missing geometry is a structure-only loss (the same philosophy
    # as the reshape guard below), never a page failure.
    for box, text, score in zip(boxes, txts, scores, strict=False):
        try:
            quad = np.asarray(box, dtype=float).reshape(4, 2)
        except (TypeError, ValueError):
            continue
        out.append((quad, str(text), float(score)))
    return out


def _ocr_rapidocr(pdf_path: str, page_idx: int, config: RouterConfig) -> tuple[str, list]:
    """OCR via RapidOCR (RapidAI) on onnxruntime.

    Renders the page through the raster seam (pymupdf for pdf, ddjvu for
    djvu) and feeds the numpy RGB array to the engine. Result boxes are
    already y-sorted; v1 reading order is a plain
    join of lines (column interleaving on drawings is acceptable — see
    wayfinder map).

    Returns ``(markdown, blocks)``: after the flat join, the line
    geometry (boxes + scores) that v0.5 threw away feeds the structure
    pass (ADR-0005). Structure failure degrades to the flat text —
    logged, never raised.

    Tuning knobs (all in the ``[ocr.rapidocr]`` section):
    - ``dpi`` (default 150): raster resolution. Probe showed OCR time is
      roughly dpi-independent (24-40s/page CPU-only) while 100 hallucinates.
    - ``det_model_type`` / ``rec_model_type``: "small"/"mobile" by default;
      "tiny"/"medium"/"server" variants exist for tuning.

    Language hint comes from ``routing.language`` via ``_rapidocr_language``
    (not from this function's params).
    """
    params = _lookup_params(config, "rapidocr")
    dpi = int(params.get("dpi", 150))

    try:
        engine = _rapidocr_get_engine(config)
    except Exception as exc:
        raise OcrError(f"rapidocr engine init failed: {exc}") from exc

    try:
        img = page_raster_array(pdf_path, page_idx, dpi=dpi)
    except Exception as exc:
        raise OcrError(f"page render failed: {exc}") from exc

    try:
        result = engine(img)
    except Exception as exc:
        raise OcrError(f"rapidocr inference failed: {exc}") from exc

    txts = [str(t) for t in (result.txts or [])]
    lines = [t for t in txts if t.strip()]
    if not lines:
        raise OcrError(f"page {page_idx + 1}: empty rapidocr result")
    md = "\n".join(lines)

    # IR 0.5 (ADR-0005): raster and line geometry are alive right here
    # (rasterize-once, TKT-RT-10) — feed the structure pass. A structure
    # failure must never kill a page whose OCR text is already good.
    blocks: list = []
    triples = _rapidocr_triples(result)
    if triples:
        try:
            blocks = structure_page(img, triples, config)
        except Exception as exc:
            logger.warning(
                "page %d: structure pass failed, keeping flat text: %s",
                page_idx + 1,
                exc,
            )
    return md, blocks


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

    Environment contract (the library never writes ``os.environ``): the
    pdf-inspector Rust core reads ``PDFIUM_LIB_PATH`` and ``ORT_DYLIB_PATH``
    from the process env — no function kwargs exist for them. They are
    bridged from the ``[ocr.pdf_inspector_ocr]`` section by
    ``pdf_router.wire_pdf_inspector_env()`` (the CLI calls it
    automatically; library callers invoke it once before ``process_pdf``).
    A missing env is a hard error with that instruction — no silent
    fallback, no env writes from here.

    Tuning knobs (all optional, safe defaults):
    - ``dpi`` (default 150.0): raster resolution for the render step.
      100 is faster but hallucinates. 200/300 are slower, not better.
    - ``minimum_confidence`` (default 0.0): drop regions below this threshold.
      0.8 would cut ~12% text on complex docs — too aggressive for
      documentation. Keep 0.0.
    - ``offline`` (default True): skip hosted-OCR recommendation checks
      and block model downloads. Set False only if you accept the network
      round-trip to fetch a missing pinned model artifact on first use.
    - ``model_cache_dir``: passed to pdf-inspector as ``model_directory``
      (default ``~/.cache/pdf-inspector/models``).
    """
    import os

    from pdf_router.rasterize import RasterizeError, _kind

    # Typed refusal first, not a crash: the Rust core opens PDFs only
    # (ADR-0004). A format mismatch is a routing bug — it must surface
    # regardless of deployment state, and this keeps the refusal testable
    # without env wiring.
    try:
        if _kind(pdf_path) != "pdf":
            raise OcrError(
                "pdf_inspector_ocr is PDF-only; route djvu pages through "
                "another engine (rapidocr/tesseract/glm_ocr/deepseek_ocr)"
            )
    except RasterizeError as exc:
        raise OcrError(str(exc)) from exc

    pdfium_lib = os.environ.get("PDFIUM_LIB_PATH")
    ort_dylib = os.environ.get("ORT_DYLIB_PATH")
    if not (pdfium_lib and ort_dylib):
        raise OcrError(
            "pdf_inspector_ocr env not wired: export PDFIUM_LIB_PATH and "
            "ORT_DYLIB_PATH, or call pdf_router.wire_pdf_inspector_env(config) "
            "(the CLI does this automatically)"
        )
    if not (Path(pdfium_lib).is_file() and Path(ort_dylib).is_file()):
        raise OcrError(f"pdf_inspector_ocr binaries not found: {pdfium_lib}, {ort_dylib}")

    model_cache = params.get("model_cache_dir")
    dpi = float(params.get("dpi", 150.0))
    minimum_confidence = float(params.get("minimum_confidence", 0.0))
    offline = bool(params.get("offline", True))

    import pdf_inspector

    result = pdf_inspector.process_pdf_with_ocr(
        pdf_path,
        mode="auto",
        page_numbers=[page_idx + 1],  # 1-indexed
        dpi=dpi,
        minimum_confidence=minimum_confidence,
        offline=offline,
        model_directory=model_cache or None,
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

    Renders page to PNG via ``rasterize_page`` (format-aware seam, 200 DPI).
    Requires ``tesseract`` binary; ``languages`` defaults to ``eng``.

    ``timeout`` (default 300 s) caps one tesseract invocation. A hung tesseract
    on a pathological page must fail that page loudly (OcrError → page.error →
    exit 2), not wedge the worker thread forever — the ThreadPoolExecutor in
    core waits for every future, so one hang would stall the whole run with
    no error surfaced anywhere.
    """
    import shutil
    import subprocess

    bin_name = params.get("bin") or "tesseract"
    languages = params.get("languages") or ["eng"]
    lang_str = "+".join(languages) if isinstance(languages, list) else str(languages)
    dpi = int(params.get("dpi", 200))
    timeout = float(params.get("timeout", 300.0))

    if shutil.which(bin_name) is None:
        raise OcrError(f"tesseract binary not found: {bin_name}")

    png = rasterize_page(pdf_path, page_idx, dpi=dpi)
    try:
        proc = subprocess.run(
            [bin_name, "stdin", "stdout", "-l", lang_str, "--dpi", str(dpi)],
            input=png,
            capture_output=True,
            timeout=timeout,
        )
    except subprocess.TimeoutExpired as exc:
        raise OcrError(
            f"page {page_idx + 1}: tesseract timed out after {timeout:g}s"
        ) from exc
    if proc.returncode != 0:
        raise OcrError(
            f"tesseract exited {proc.returncode}: "
            + proc.stderr.decode(errors="replace")[:300]
        )
    text = proc.stdout.decode("utf-8", errors="replace").strip()
    if not text:
        raise OcrError(f"page {page_idx + 1}: empty tesseract result")
    return text


def _ocr_glm(pdf_path: str, page_idx: int, params: dict[str, Any]) -> tuple[str, Any]:
    """OCR via Z.ai GLM layout_parsing API.

    Renders the page to a single-page PDF (verbatim page extract for pdf,
    raster-wrapped page for djvu — the raster seam) and POSTs it as
    base64 data-URL. Returns ``(markdown, layout_details_or_None)`` —
    markdown from ``md_results``, layout from ``layout_details`` (feeds
    :func:`pdf_router.structure.blocks_from_glm`).
    Requires ``api_key`` in params; ``base_url`` defaults to Z.ai endpoint.

    Transient failures (429/5xx, network) are retried ``max_retries``
    times (default 2) with exponential backoff (``retry_backoff_s``,
    default 1.0) plus jitter; ``Retry-After`` wins when larger.
    Deterministic 4xx (bad key, bad request) fail on the first attempt.

    Empirical notes (live-tested 2026-08-27, from the retired adapter):
    - A **coding-plan key** gets 429 "Insufficient balance" on the standard
      endpoint — document parsing is billed separately. Use a key with the
      document/resource package.
    - ``md_results`` is whole-document markdown; a single-page input maps
      1:1 to the requested page (~1.8k tokens/page).
    - Known limitation: Cyrillic in dense stamps gets garbled («Страптельство»
      for «Строительство») — fine for body text, verify stamps.
    """
    import base64

    api_key = params.get("api_key")
    base_url = params.get("base_url", "https://api.z.ai/api/paas/v4")
    model = params.get("model", "glm-ocr")
    timeout = float(params.get("timeout", 300.0))

    if not api_key:
        raise OcrError("glm_ocr requires api_key")

    try:
        import requests  # noqa: F401 — availability guard; _post_with_retry imports its own
    except Exception as exc:
        raise OcrError(f"requests not importable (install [cloud] extra): {exc}") from exc

    # Build single-page PDF via the raster seam (pdf verbatim, djvu wrapped).
    try:
        pdf_bytes = page_single_pdf(pdf_path, page_idx)
    except Exception as exc:
        raise OcrError(f"page render failed: {exc}") from exc

    data_url = "data:application/pdf;base64," + base64.b64encode(pdf_bytes).decode("ascii")

    max_retries = int(params.get("max_retries", 2))
    backoff_s = float(params.get("retry_backoff_s", 1.0))

    try:
        r = _post_with_retry(
            f"{base_url.rstrip('/')}/layout_parsing",
            json={"model": model, "file": data_url},
            headers={
                "Content-Type": "application/json",
                "Authorization": f"Bearer {api_key}",
            },
            timeout=timeout,
            max_retries=max_retries,
            backoff_s=backoff_s,
        )
        r.raise_for_status()
    except Exception as exc:
        raise OcrError(f"glm_ocr layout_parsing failed: {exc}") from exc

    payload = r.json()
    md = payload.get("md_results")
    if not isinstance(md, str) or not md.strip():
        raise OcrError("glm_ocr: empty md_results in response")
    # layout_details feeds blocks_from_glm; absent/empty -> no blocks.
    # Single-page input maps 1:1 to response page 0.
    return md.strip(), payload.get("layout_details")


def _ocr_deepseek(pdf_path: str, page_idx: int, params: dict[str, Any]) -> str:
    """OCR via Novita AI deepseek-ocr-2.

    Renders page to PNG (200 DPI, format-aware seam) and POSTs it as a
    base64 data-URL **directly inside the chat/completions body** — same
    pattern as ``_ocr_glm``. No public file host is involved; the page
    travels only to the configured ``base_url`` (privacy: P5). Requires
    ``api_key`` in params. Retry policy: same as :func:`_ocr_glm` —
    ``max_retries`` (default 2) with ``retry_backoff_s`` (default 1.0).

    Empirical notes (live-tested 2026-08-26, from the retired adapter):
    - Prompt "OCR this image." beats "Convert the document to markdown.":
      the markdown prompt triggers repetition loops (p27 → 447x "ПОВТОРА №"
      lines; the terse prompt gave clean output on the title page).
    """
    api_key = params.get("api_key")
    base_url = params.get("base_url", "https://api.novita.ai/openai")
    model = params.get("model", "deepseek/deepseek-ocr-2")
    prompt = params.get("prompt", "OCR this image.")
    timeout = float(params.get("timeout", 60.0))
    max_tokens = int(params.get("max_tokens", 4096))
    dpi = int(params.get("dpi", 200))

    if not api_key:
        raise OcrError("deepseek_ocr requires api_key")

    try:
        import requests  # noqa: F401 — availability guard; _post_with_retry imports its own
    except Exception as exc:
        raise OcrError(f"requests not importable (install [cloud] extra): {exc}") from exc

    # Render and inline as data-URL (no upload, no third-party host).
    png = rasterize_page(pdf_path, page_idx, dpi=dpi)
    image_url = "data:image/png;base64," + base64.b64encode(png).decode("ascii")

    max_retries = int(params.get("max_retries", 2))
    backoff_s = float(params.get("retry_backoff_s", 1.0))

    # Chat completion.
    try:
        r = _post_with_retry(
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
            max_retries=max_retries,
            backoff_s=backoff_s,
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
) -> tuple[str, str, list]:
    """Run OCR on one page using the configured fallback engine.

    Returns ``(markdown, engine_name, blocks)`` — blocks are the IR 0.5
    structural blocks (ADR-0005): geometry pass for rapidocr, glm
    ``layout_details`` for glm_ocr, pipe-markdown fallback for the
    boxless engines. Structure failures degrade to ``[]``.

    Args:
        pdf_path: path to the PDF file.
        page_idx: 0-indexed page number.
        config: full RouterConfig (contains ``routing`` and ``ocr_engines``).

    Raises:
        OcrError: if the engine is unknown, unavailable, or returns empty/fails.

    The engine name comes from ``config.routing.ocr_fallback_engine`` (default
    ``rapidocr``). Params come from the matching ``[ocr.<engine>]`` section
    (``config.ocr_engines[engine]``); if absent, an empty dict is used.
    """
    engine = config.routing.ocr_fallback_engine
    params = _lookup_params(config, engine)

    if engine == "rapidocr":
        # rapidocr reads its own knobs (dpi, det/rec model_type) from the
        # [ocr.rapidocr] section and the language hint from
        # config.routing.language; the dispatcher does not plumb either.
        md, blocks = _ocr_rapidocr(pdf_path, page_idx, config)
        return md, engine, blocks
    elif engine == "pdf_inspector_ocr":
        md = _ocr_pdf_inspector(pdf_path, page_idx, params)
        return md, engine, _boxless_blocks(md)
    elif engine == "tesseract":
        md = _ocr_tesseract(pdf_path, page_idx, params)
        return md, engine, _boxless_blocks(md)
    elif engine == "glm_ocr":
        md, layout = _ocr_glm(pdf_path, page_idx, params)
        glm_blocks: list = []
        if layout:
            try:
                glm_blocks = blocks_from_glm(layout)
            except Exception as exc:
                logger.warning("glm layout_details -> blocks failed: %s", exc)
        return md, engine, glm_blocks
    elif engine == "deepseek_ocr":
        md = _ocr_deepseek(pdf_path, page_idx, params)
        return md, engine, _boxless_blocks(md)
    else:
        raise OcrError(f"unknown ocr_fallback_engine: {engine!r}")


def _boxless_blocks(markdown: str) -> list:
    """Pipe-table fallback blocks for engines without box geometry.

    The cheapest honest layer of the hybrid (ADR-0005 §1): parse pipe
    tables out of the engine markdown, re-tagged ``ocr_pipe_fallback``.
    No fake geometry, no headings. Failure degrades to no blocks.
    """
    try:
        return blocks_from_pipe_markdown(markdown or "")
    except Exception as exc:
        logger.warning("pipe-fallback structure pass failed: %s", exc)
        return []
