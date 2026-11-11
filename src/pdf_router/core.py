"""v0.4 routing pipeline — flat functions, no adapter classes.

Design (TKT-RT-01, extended by ADR-0003, ADR-0004 and ADR-0008):
1. route(pdf_path) — the format dispatcher picks the adapter (PDF →
   pdf-inspector; DOCX → the native stdlib inspector; DJVU → the native
   djvulibre subprocess inspector, ADR-0004; XLSX → the native stdlib
   inspector, ADR-0008).
2. For each page, in parallel (ThreadPoolExecutor):
   - extract_images_for_page: raster + vector images (PDF) or media parts
     (docx), independent of text. djvu: no image pass at all — a djvu page
     IS a page-background scan, which the PDF image pass filters out
     anyway (ADR-0004, TKT-DJV-06).
   - process_text_for_page (PDF and DJVU): looks_like_text per-page; OCR
     fallback for pages that need it. For djvu, the inspector already
     knows needs_ocr per page (hidden text layer), and page rasters are
     rendered on demand through the raster seam (ddjvu). DOCX is
     born-digital — its text is authoritative, there is no OCR pass to
     fall back to (ADR-0003), so the text pass is skipped entirely.
3. Return IR with page.images populated (djvu pages always []).

Key decisions:
- page.needs_ocr from an inspector bypasses our looks_like_text (TKT-RT-03);
  a djvu text-layer page that fails the quality gate still falls back to
  OCR via low_text_quality (TKT-DJV-03).
- looks_like_text is per-page, not per-block (TKT-RT-02).
- OCR is per-page, one raster + one OCR call per page (TKT-RT-10).
- OCR engine is one, configured via routing.ocr_fallback_engine (TKT-RT-06).
- AdapterPdfInspector compat shim removed in v0.3 (RT-14). Core routes
  through pdf_router.formats (the dispatcher owns format -> adapter).
- Sidecar image extraction is dispatched on Metadata.source_format (IR 0.3):
  PDF → extract_images.extract_page_images, DOCX → the docx adapter's
  extract_docx_images, DOC → the doc adapter's extract_doc_images
  (office-oxide base64 payloads → files), DJVU/XLSX/HTML → none (images
  stay []; the xlsx/html corpora have 0 media, ADR-0008). This is pipeline
  machinery, not a second registry — the inspector registry (formats.py)
  stays path -> IR only.
"""

from __future__ import annotations

import functools
import logging
import os
import threading
import time
from collections.abc import Callable
from concurrent.futures import Future, ThreadPoolExecutor
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from pdf_router.config import RouterConfig
from pdf_router.formats import route
from pdf_router.ir import IR, SrcTool
from pdf_router.quality import looks_like_text, looks_garbled
from pdf_router.ocr import ocr_page, OcrError
from pdf_router.ocr_cache import _md5_of, params_fingerprint
from pdf_router.extract_images import extract_page_images

logger = logging.getLogger("pdf_router")


@dataclass(frozen=True)
class ProgressEvent:
    """One page finished — streamed to ``progress=`` callbacks.

    ``status`` is computed by the SAME rule as the final report's
    aggregation: ``page.error`` → ``"failed"``, ``page.needs_ocr`` →
    ``"ocr"``, else ``"native"``. No events are emitted for pages skipped
    by a cancellation — the stream simply stops. This keeps the streaming
    contract consistent with the IR the caller receives at the end (the
    report-must-not-contradict-the-IR invariant, per page).
    ``elapsed_s`` is monotonic time since the ``process_pdf`` call.
    """

    page: int  # 0-based page index that just finished
    pages_done: int  # finished pages so far, INCLUDING this one
    pages_total: int
    status: str  # "native" | "ocr" | "failed"
    elapsed_s: float


class PipelineCancelled(RuntimeError):
    """Raised by a ``progress=`` callback to stop the remaining pages.

    The sanctioned cancel protocol: raising this from inside the callback
    aborts the run cleanly — pages already done keep their results, pages
    not yet started get ``page.error = "cancelled"`` and count into
    ``report.pages_failed`` (the report stays consistent with the IR).
    Any OTHER exception escaping the callback is treated as a bug in the
    caller's code: logged with traceback and counted in
    ``report.progress_callback_errors``, the pipeline itself continues.
    """


@dataclass
class ProcessingReport:
    pages_total: int = 0
    pages_native: int = 0
    pages_ocr: int = 0
    pages_failed: int = 0
    pages_image_failed: int = 0
    #: Exceptions raised by the ``progress=`` callback that were NOT the
    #: ``PipelineCancelled`` protocol — caller's bugs, logged and survived.
    progress_callback_errors: int = 0
    #: Pages not processed because a callback raised PipelineCancelled.
    pages_cancelled: int = 0
    #: OCR pages served from the content-addressed cache (ADR-0007) —
    #: included in pages_ocr, so total counts stay unchanged.
    pages_ocr_cached: int = 0
    #: Pages whose text layer was present but undecodable, dropped rather
    #: than OCR'd (routing.garbled_text_policy="drop"). They count as
    #: neither native nor OCR — the text they carry is not knowledge.
    pages_unusable: int = 0
    #: Raster sidecars refused (ticket s3-worker-economy/01): the size gate
    #: (icons, thumbnails, rules) and the page-background filter alike.
    #: Never written, never uploaded. Tuning the gate reads this counter.
    images_dropped: int = 0


def _engine_to_src_tool(engine: str) -> SrcTool:
    """Map engine name string to SrcTool enum value."""
    return SrcTool(engine)


def _collect_page_images(result: Any, source_format: str) -> tuple[list[str], int]:
    """Split an image-pass result into (saved paths, dropped count).

    pdf and djvu return ``(saved, dropped)`` (size gate); docx/doc return ``[saved]``
    (no gate there). One place, both collection loops share it.
    """
    if source_format in ("pdf", "djvu"):
        saved, dropped = result
        return saved, dropped
    return result, 0


class _LazyDigest:
    """Thread-safe, on-first-use md5 of the source file (ADR-0007).

    The digest is needed only when the first OCR page touches the cache,
    so native-only runs never pay for hashing a large file. ``known``
    (caller-supplied ``content_md5``) short-circuits hashing entirely.
    A hashing failure disables the cache for the run (returns ``None``)
    instead of breaking it — the cache may only accelerate.
    """

    def __init__(self, path: str, known: str | None) -> None:
        self._path = path
        self._lock = threading.Lock()
        self._value: str | None = known if known else None
        self._broken = known is not None and not known

    def get(self) -> str | None:
        if self._value is not None:
            return self._value
        with self._lock:
            if self._value is None and not self._broken:
                try:
                    self._value = _md5_of(self._path)
                except OSError:
                    logger.warning(
                        "ocr_cache: cannot hash %s; cache disabled for this run",
                        self._path,
                        exc_info=True,
                    )
                    self._broken = True
            return self._value


def process_pdf(
    pdf_path: str | Path,
    config: RouterConfig,
    *,
    output_dir: Path | None = None,
    progress: "Callable[[ProgressEvent], None] | None" = None,
    ocr_cache: "Any | None" = None,
    content_md5: "str | None" = None,
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

    ``progress`` receives a :class:`ProgressEvent` after each page is
    finalized, in document order, on the caller's thread. Raising
    :class:`PipelineCancelled` inside it is the sanctioned cancel protocol
    (see that class). Any other exception is caller's-bug: logged and
    counted, never fatal.

    ``ocr_cache`` (ADR-0007): optional :class:`pdf_router.ocr_cache.OcrCache`.
    When given, every OCR page consults the cache first and every completed
    OCR page is written to it — a re-run of an interrupted document then
    pays OCR only for the pages that never finished. ``content_md5``
    supplies the document digest for the cache key; ``None`` (default)
    hashes the file lazily, only if the first OCR page needs the key —
    native-only runs never hash. Cache errors degrade silently to live
    OCR: the cache can only accelerate, never break the run.
    """
    pdf_path = os.fspath(pdf_path)
    t0 = time.perf_counter()
    # The format gate: magic bytes decide, not the extension. Raises
    # UnsupportedFormatError before any page work for unrecognized content.
    inspector = route(pdf_path)
    doc = inspector(pdf_path)
    # ADR-0007: lazy per-run digest wrapper (hashes only if the first OCR
    # page needs a cache key; a caller-supplied content_md5 short-circuits
    # hashing entirely). None when ocr_cache is None — zero overhead.
    digest = (
        _LazyDigest(pdf_path, content_md5)
        if ocr_cache is not None
        else None
    )
    # IR 0.5.1 (ADR-0006): the single fill point for source_path. The
    # renderer layer derives doc_id/doc_title from it; adapters stay
    # path-agnostic. asdict() carries it into to_dict automatically.
    doc.metadata.source_path = pdf_path
    report = ProcessingReport(pages_total=len(doc.pages))

    # Format-aware passes (IR 0.3/0.4, ADR-0003/0004):
    # - text pass: pdf (quality gate + OCR fallback) and djvu (the
    #   inspector marked needs_ocr per page; a junky hidden-text layer
    #   still falls back via low_text_quality). docx is born-digital —
    #   no OCR pass to fall back to.
    # - image pass: pdf → pymupdf extractor, docx → media parts, djvu →
    #   none (a djvu page is itself the page-background scan; exporting
    #   it as a sidecar image would duplicate the OCR raster).
    source_format = doc.metadata.source_format
    run_text_pass = source_format in ("pdf", "djvu")

    if output_dir is not None:
        output_dir.mkdir(parents=True, exist_ok=True)

    routing_cfg = config.routing
    workers = max(1, routing_cfg.workers or 2)

    def _emit(page, pages_done: int, elapsed_s: float) -> bool:
        """Stream one page-finished event. Returns False when the callback
        cancelled the pipeline. Any other callback exception is a caller
        bug — logged with traceback, counted, survived. Runs on the
        collecting (caller's) thread, serialized in document order."""
        if progress is None:  # unreachable via the call site; satisfies narrowing
            return True
        # Same rule as the report aggregation below — the event stream must
        # never contradict the report/IR the caller ends up with. NB:
        # src_tool is NOT the discriminator (native pages carry the default
        # SrcTool.PDF_INSPECTOR); needs_ocr is what the report counts on.
        if page.error:
            status = "failed"
        elif page.needs_ocr:
            status = "ocr"
        else:
            status = "native"
        try:
            progress(
                ProgressEvent(
                    page=page.page,
                    pages_done=pages_done,
                    pages_total=report.pages_total,
                    status=status,
                    elapsed_s=elapsed_s,
                )
            )
        except PipelineCancelled:
            logger.info("progress callback cancelled the pipeline at page %d", page.page + 1)
            return False
        except Exception:
            report.progress_callback_errors += 1
            logger.exception("progress callback raised; event for page %d dropped", page.page + 1)
        return True

    cancel_requested = False
    pages_done = 0
    # ADR-0007: bind the cache context via partial only when a cache is in
    # play — the plain path keeps the historical 3-positional-arg call so
    # existing tests can keep patching _process_text_for_page(page, path, cfg).
    # suspect_encoding rides along only for a document the inspector flagged
    # for encoding trouble: it is what unlocks the looks_garbled check.
    _extra: dict[str, Any] = {}
    if ocr_cache is not None:
        _extra.update(ocr_cache=ocr_cache, digest=digest)
    if getattr(doc.metadata, "has_encoding_issues", False):
        _extra["suspect_encoding"] = True
    text_worker = (
        functools.partial(_process_text_for_page, **_extra) if _extra else _process_text_for_page
    )
    with ThreadPoolExecutor(max_workers=workers) as pool:
        futures = []  # list of (page, img_fut, txt_fut)
        for page in doc.pages:
            # Image extraction only runs when the caller asked for a sidecar
            # directory; otherwise the pass is skipped entirely (pure run).
            # Heterogeneous futures by format: pdf returns (saved, dropped),
            # docx/doc return [saved] — annotated at the seam, unpacked per
            # format at the collection points below.
            img_fut: Future[Any] | None = None
            if output_dir is not None:
                if source_format == "pdf":
                    img_fut = pool.submit(
                        extract_page_images,
                        page,
                        pdf_path,
                        output_dir,
                        raster=routing_cfg.extract_raster_photos,
                        vector=routing_cfg.extract_vector_diagrams,
                        min_size=routing_cfg.images_min_size,
                        min_thin=routing_cfg.images_min_thin,
                        max_aspect_ratio=routing_cfg.images_max_aspect_ratio,
                        full_page_min_ink=routing_cfg.images_full_page_min_ink,
                        vector_min_share=routing_cfg.images_vector_min_share,
                        layout_figures=routing_cfg.extract_layout_figures,
                        layout_conf_thresh=routing_cfg.layout_conf_thresh,
                    )
                elif source_format == "docx":
                    from pdf_router.adapters.docx import extract_docx_images

                    img_fut = pool.submit(
                        extract_docx_images, page, pdf_path, output_dir
                    )
                elif source_format == "doc":
                    from pdf_router.adapters.doc import extract_doc_images

                    img_fut = pool.submit(
                        extract_doc_images, page, pdf_path, output_dir
                    )
                elif source_format == "djvu":
                    if routing_cfg.extract_layout_figures:
                        from pdf_router.extract_images import extract_djvu_images

                        img_fut = pool.submit(
                            extract_djvu_images,
                            page,
                            pdf_path,
                            output_dir,
                            conf_thresh=routing_cfg.layout_conf_thresh,
                        )
                # xlsx/html: img_fut stays None — page.images = [] (ADR-0008).
            txt_fut = (
                pool.submit(text_worker, page, pdf_path, config)
                if run_text_pass
                else None
            )
            futures.append((page, img_fut, txt_fut))

        for page, img_fut, txt_fut in futures:
            # Images are written to disk and their relative paths stored on the page.
            if img_fut is None:
                page.images = []  # pure run: no sidecar IO at all
            else:
                try:
                    page.images, dropped = _collect_page_images(
                        img_fut.result(), source_format
                    )
                    report.images_dropped += dropped
                except Exception as exc:
                    logger.error("page %d: image extraction failed: %s", page.page + 1, exc)
                    page.images = []
                    report.pages_image_failed += 1

            # Text processing mutates page.markdown / src_tool / error.
            # Any unexpected (non-OcrError) exception is recorded on the page:
            # page.error is None *only* when the page really succeeded.
            # (docx pages skip the pass entirely — see run_text_pass above.)
            if txt_fut is not None:
                try:
                    if txt_fut.result():
                        report.pages_ocr_cached += 1
                except Exception as exc:
                    page.error = f"internal_error: {type(exc).__name__}: {exc}"
                    logger.error("page %d: text processing failed: %s", page.page + 1, exc)

            pages_done += 1
            if progress is not None and not _emit(
                page, pages_done, time.perf_counter() - t0
            ):
                cancel_requested = True
                # Cancel the still-queued futures BEFORE the with-block exit
                # — its shutdown(wait=True) would otherwise drain the whole
                # queue and make the cancel pointless. Pages already IN
                # FLIGHT (at most `workers`) finish during the exit, bounded
                # by the per-call timeouts added earlier.
                pool.shutdown(wait=False, cancel_futures=True)
                break

    if cancel_requested:
        # Pages that never ran get page.error = "cancelled"; in-flight pages
        # keep their real result (success or failure). Future.cancelled() is
        # True exactly for futures discarded before they started, so it
        # separates "never ran" from "ran during pool exit". A page ran iff
        # at least one of its futures exists and was not cancelled.
        for page, img_fut, txt_fut in futures[pages_done:]:
            ran_img = img_fut is not None and not img_fut.cancelled()
            ran_txt = txt_fut is not None and not txt_fut.cancelled()
            if not ran_img and not ran_txt:
                page.error = "cancelled"
                continue
            # In-flight page: surface its outcome exactly like the main
            # collection loop above — a queued-but-cancelled future never
            # ran, so only non-cancelled ones carry a result or a crash.
            if img_fut is not None:
                if ran_img:
                    try:
                        page.images, dropped = _collect_page_images(
                            img_fut.result(), source_format
                        )
                        report.images_dropped += dropped
                    except Exception as exc:
                        logger.error(
                            "page %d: image extraction failed: %s", page.page + 1, exc
                        )
                        page.images = []
                        report.pages_image_failed += 1
                else:
                    page.images = []
            if txt_fut is not None and ran_txt:
                try:
                    if txt_fut.result():
                        report.pages_ocr_cached += 1
                except Exception as exc:
                    page.error = f"internal_error: {type(exc).__name__}: {exc}"
                    logger.error(
                        "page %d: text processing failed: %s", page.page + 1, exc
                    )

    # Aggregate report. pages_failed counts ANY page carrying an error —
    # OCR failures, internal crashes and cancelled pages alike (report must
    # not contradict the IR it describes).
    for page in doc.pages:
        if page.error == "cancelled":
            report.pages_cancelled += 1
            report.pages_failed += 1
        elif page.error:
            report.pages_failed += 1
        elif page.src_tool == SrcTool.TEXT_LAYER_UNUSABLE:
            report.pages_unusable += 1
        elif page.needs_ocr:
            report.pages_ocr += 1
        else:
            report.pages_native += 1

    doc.metadata.unusable_text_pages = [
        p.page + 1
        for p in doc.pages
        if p.error is None and p.src_tool == SrcTool.TEXT_LAYER_UNUSABLE
    ]
    if report.pages_unusable:
        logger.warning(
            "%d page(s) carry an undecodable text layer and were dropped "
            "(routing.garbled_text_policy=%r): pages %s",
            report.pages_unusable,
            routing_cfg.garbled_text_policy,
            doc.metadata.unusable_text_pages[:20],
        )

    elapsed = time.perf_counter() - t0
    logger.info(
        "processed %d pages in %.1fs: %d native, %d OCR, %d failed,"
        " %d image-failed, %d images-dropped",
        report.pages_total,
        elapsed,
        report.pages_native,
        report.pages_ocr,
        report.pages_failed,
        report.pages_image_failed,
        report.images_dropped,
    )
    return doc, report


# ---------------------------------------------------------------------------
# Text processing
# ---------------------------------------------------------------------------


def _process_text_for_page(
    page,
    pdf_path: str,
    config: RouterConfig,
    ocr_cache: "Any | None" = None,
    digest: "_LazyDigest | None" = None,
    suspect_encoding: bool = False,
) -> bool:
    """Mutate page in-place: markdown, src_tool, error.

    Returns True only when the page came from the OCR cache (ADR-0007) —
    the caller aggregates that counter on the main thread (workers never
    touch report counters).

    Logic (TKT-RT-01, extended by TKT-DJV-03/04):
    1. If the inspector already said needs_ocr → skip looks_like_text
       (djvu layerless pages land here, reason inspector_needs_ocr).
    2. Compute page_text from blocks + markdown.
    3. Run looks_like_text(page_text, language, threshold).
    4. If ok → return (native text — pdf text or a good djvu text layer).
    5. Otherwise: rasterize page once (format-aware seam) + run OCR via
       ocr_page(). A junky djvu text layer falls back here with reason
       low_text_quality — the layer is not trusted over real OCR.

    A text layer that passes looks_like_text can still be undecodable: a
    subset font without /ToUnicode passes any alpha-ratio check while
    carrying zero readable characters. When the document is flagged
    ``has_encoding_issues``, looks_garbled gets the last word — see
    ``routing.garbled_text_policy`` for what happens then.
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
            # The layer is text-shaped, but is it READABLE? Only worth asking
            # of a document the inspector already flagged for encoding
            # trouble — a clean book never reaches this test at all.
            if suspect_encoding and looks_garbled(page_text):
                if config.routing.garbled_text_policy == "ocr":
                    page.ocr_reason = "garbled_text_layer"
                else:
                    # Drop, don't OCR: code soup is not knowledge, and a
                    # rasterization + recognition pass on a page we already
                    # know we cannot read is money spent on a guess.
                    page.markdown = ""
                    page.blocks = []
                    page.src_tool = SrcTool.TEXT_LAYER_UNUSABLE
                    return False
            return False  # native text is fine
        page.ocr_reason = "low_text_quality"
    else:
        page.ocr_reason = "inspector_needs_ocr"

    # Fall through: rasterize + OCR.
    # Set src_tool BEFORE the call so it is correct even on failure.
    _src = _engine_to_src_tool(config.routing.ocr_fallback_engine)

    # ADR-0007: consult the content-addressed cache before paying for
    # rasterization + OCR. All cache errors degrade to a live OCR call —
    # the cache can only accelerate, never break the run.
    if ocr_cache is not None and digest is not None:
        try:
            engine = config.routing.ocr_fallback_engine
            content_md5 = digest.get()
            hit = (
                None
                if content_md5 is None
                else ocr_cache.get(
                    content_md5, page.page, engine, params_fingerprint(config, engine)
                )
            )
        except Exception:
            logger.warning(
                "page %d: ocr_cache get failed; live OCR", page.page + 1, exc_info=True
            )
            hit = None
        if hit is not None:
            md, blocks = hit
            page.markdown = md
            page.blocks = blocks
            page.src_tool = _src
            page.error = None
            # ADR-0007: a restored page is indistinguishable from a freshly
            # OCR'd one — the gates above already set the true ocr_reason
            # (low_text_quality / inspector_needs_ocr); never rewrite it.
            return True

    try:
        md, _engine, blocks = ocr_page(pdf_path, page.page, config)
        page.markdown = md
        # IR 0.5 (ADR-0005): OCR pages carry real blocks. The old native
        # blocks described the native markdown we just replaced — both go.
        page.blocks = blocks
        page.src_tool = _src
        page.error = None
        if ocr_cache is not None and digest is not None:
            try:
                engine = config.routing.ocr_fallback_engine
                content_md5 = digest.get()
                if content_md5 is not None:
                    ocr_cache.put(
                        content_md5,
                        page.page,
                        engine,
                        params_fingerprint(config, engine),
                        md,
                        blocks,
                    )
            except Exception:
                logger.warning(
                    "page %d: ocr_cache put failed", page.page + 1, exc_info=True
                )
        return False
    except OcrError as exc:
        page.src_tool = _src  # mark what was attempted
        page.error = f"{config.routing.ocr_fallback_engine}_failed: {exc}"
        logger.warning("page %d: %s", page.page + 1, exc)
        return False



