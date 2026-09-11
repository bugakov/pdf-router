"""v0.3 routing pipeline — flat functions, no adapter classes.

Design (TKT-RT-01):
1. extract_ir(pdf_path) — pdf-inspector, always.
2. For each page, in parallel (ThreadPoolExecutor):
   - extract_images_for_page: raster + vector images, independent of text.
   - process_text_for_page: looks_like_text per-page; OCR fallback all pages if needed.
3. Return IR 0.2 with page.images populated.

Key decisions:
- page.needs_ocr from pdf-inspector bypasses our looks_like_text (TKT-RT-03).
- looks_like_text is per-page, not per-block (TKT-RT-02).
- OCR is per-page, one raster + one OCR call per page (TKT-RT-10).
- OCR engine is one, configured via routing.ocr_fallback_engine (TKT-RT-06).
- AdapterPdfInspector compat shim removed in v0.3 (RT-14). Use extract_ir() directly.
"""

from __future__ import annotations

import logging
import os
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from pathlib import Path

from pdf_router.config import RouterConfig
from pdf_router.ir import IR, SrcTool
from pdf_router.quality import looks_like_text
from pdf_router.ocr import ocr_page, OcrError
from pdf_router.extract_images import extract_page_images

logger = logging.getLogger("pdf_router")


@dataclass
class ProcessingReport:
    pages_total: int = 0
    pages_native: int = 0
    pages_ocr: int = 0
    pages_failed: int = 0
    pages_image_failed: int = 0


def _engine_to_src_tool(engine: str) -> SrcTool:
    """Map engine name string to SrcTool enum value."""
    return SrcTool(engine)


def process_pdf(
    pdf_path: str | Path,
    config: RouterConfig,
    *,
    output_dir: Path | None = None,
) -> tuple[IR, ProcessingReport]:
    """Full routing pipeline. Returns (IR, ProcessingReport).

    ``pdf_path`` accepts str or Path — coerced here so every downstream
    call (Rust pyo3 boundary, subprocess args) receives a plain str.

    ``output_dir`` is the pipeline's ONLY IO switch. ``None`` (the default)
    keeps the whole run in memory: no directories created, no sidecar
    images extracted, ``page.images`` stays ``[]``. A path turns on sidecar
    image extraction under ``<output_dir>/<doc-id>/images/``. The pipeline
    never reads ``config.renderers.output_dir`` — output policy belongs to
    the caller (API facade / CLI), not to the config.
    """
    from pdf_router.adapters.pdf_inspector import extract_ir

    pdf_path = os.fspath(pdf_path)
    t0 = time.perf_counter()
    doc = extract_ir(pdf_path)
    report = ProcessingReport(pages_total=len(doc.pages))

    if output_dir is not None:
        output_dir.mkdir(parents=True, exist_ok=True)

    routing_cfg = config.routing
    workers = max(1, routing_cfg.workers or 2)

    with ThreadPoolExecutor(max_workers=workers) as pool:
        futures = []  # list of (page, img_fut, txt_fut)
        for page in doc.pages:
            # Image extraction only runs when the caller asked for a sidecar
            # directory; otherwise the pass is skipped entirely (pure run).
            img_fut = (
                pool.submit(
                    extract_page_images,
                    page,
                    pdf_path,
                    output_dir,
                    raster=routing_cfg.extract_raster_photos,
                    vector=routing_cfg.extract_vector_diagrams,
                )
                if output_dir is not None
                else None
            )
            txt_fut = pool.submit(
                _process_text_for_page,
                page,
                pdf_path,
                config,
            )
            futures.append((page, img_fut, txt_fut))

        for page, img_fut, txt_fut in futures:
            # Images are written to disk and their relative paths stored on the page.
            if img_fut is None:
                page.images = []  # pure run: no sidecar IO at all
            else:
                try:
                    page.images = img_fut.result()
                except Exception as exc:
                    logger.error("page %d: image extraction failed: %s", page.page + 1, exc)
                    page.images = []
                    report.pages_image_failed += 1

            # Text processing mutates page.markdown / src_tool / error.
            # Any unexpected (non-OcrError) exception is recorded on the page:
            # page.error is None *only* when the page really succeeded.
            try:
                txt_fut.result()
            except Exception as exc:
                page.error = f"internal_error: {type(exc).__name__}: {exc}"
                logger.error("page %d: text processing failed: %s", page.page + 1, exc)

    # Aggregate report. pages_failed counts ANY page carrying an error —
    # OCR failures and internal crashes alike (report must not contradict
    # the IR it describes).
    for page in doc.pages:
        if page.error:
            report.pages_failed += 1
        elif page.needs_ocr:
            report.pages_ocr += 1
        else:
            report.pages_native += 1

    elapsed = time.perf_counter() - t0
    logger.info(
        "processed %d pages in %.1fs: %d native, %d OCR, %d failed, %d image-failed",
        report.pages_total,
        elapsed,
        report.pages_native,
        report.pages_ocr,
        report.pages_failed,
        report.pages_image_failed,
    )
    return doc, report


# ---------------------------------------------------------------------------
# Text processing
# ---------------------------------------------------------------------------


def _process_text_for_page(page, pdf_path: str, config: RouterConfig) -> None:
    """Mutates page in-place: markdown, src_tool, error.

    Logic (TKT-RT-01):
    1. If pdf-inspector already said needs_ocr → skip looks_like_text.
    2. Compute page_text from blocks + markdown.
    3. Run looks_like_text(page_text, language, threshold).
    4. If ok → return (native text).
    5. Otherwise: rasterize page once + run OCR via ocr_page().
    """
    needs_ocr_from_inspector = page.needs_ocr

    if not needs_ocr_from_inspector:
        # page.markdown holds the FULL native text of the page; blocks carry
        # only structural elements (tables/headings) parsed out of that same
        # markdown. So markdown is the primary source for the quality check;
        # blocks are a defensive fallback (they cannot be a superset).
        # NB: the old `blocks or markdown` order starved the check to
        # table-cells-only on pages whose markdown was perfectly clean
        # (e.g. pzu p4: 16 chars of doc-number blocks hid 2502 clean chars).
        blocks_text = "\n".join(
            b.text for b in page.blocks if hasattr(b, "text")
        ).strip()
        page_text = (page.markdown or "").strip() or blocks_text or ""

        if looks_like_text(
            page_text,
            language=config.routing.language,
            threshold=config.routing.text_alpha_threshold,
        ):
            return  # native text is fine
        page.ocr_reason = "low_text_quality"
    else:
        page.ocr_reason = "inspector_needs_ocr"

    # Fall through: rasterize + OCR.
    # Set src_tool BEFORE the call so it is correct even on failure.
    _src = _engine_to_src_tool(config.routing.ocr_fallback_engine)
    try:
        md, engine = ocr_page(pdf_path, page.page, config)
        page.markdown = md
        page.src_tool = _src
        page.error = None
    except OcrError as exc:
        page.src_tool = _src  # mark what was attempted
        page.error = f"{config.routing.ocr_fallback_engine}_failed: {exc}"
        logger.warning("page %d: %s", page.page + 1, exc)



