"""Native djvu inspector — subprocess plumbing over djvulibre (ADR-0004).

Strategy C (native render + format-aware raster seam): the inspector reads
structure metadata and the hidden text layer straight from the djvu file;
page rasters for OCR are rendered on demand by :mod:`pdf_router.rasterize`
via ``ddjvu``. No intermediate PDF is ever written (TKT-DJV-05: in-memory
page rasters, no temp files, no cache).

External tools (djvulibre-bin, already required at deploy time):
- ``djvused`` — page count, hidden text layer (``print-pure-txt``);
- ``djvudump`` — per-page stored DPI (INFO chunks), for the
  ``-scale=min(dpi, stored_dpi)`` render rule (no upscaling of 72-dpi
  pages, no wasted downsample);
- ``ddjvu`` — page raster (PPM on stdout), owned by rasterize.py.

Indexing: ``PageResult.page`` is 0-indexed; ``djvused``/``ddjvu`` page
selectors are 1-indexed — every boundary is converted exactly once here.

Hidden text layer semantics (TKT-DJV-03/04): a page whose layer yields
non-blank text keeps that text as ``markdown`` with
``needs_ocr=False`` and ``src_tool=DJVU_TEXT_LAYER``; a layerless page
gets ``markdown=""``, ``needs_ocr=True`` and ``src_tool=DJVU_ADAPTER``
(the text pass OCRs it through the raster seam and stamps its own
driver). A junky layer fails the quality gate downstream and
falls back to OCR with ``ocr_reason="low_text_quality"`` — no new
mechanism.
"""

from __future__ import annotations

import functools
import os
import re
import subprocess
import time

from pdf_router.ir import IR, Metadata, OcrReasonEntry, PageResult, SrcTool

__all__ = [
    "DjvuError",
    "djvu_info",
    "djvu_page_count",
    "djvu_page_texts",
    "djvu_stored_dpis",
    "extract_ir",
    "render_page_rgb_djvu",
]


class DjvuError(ValueError):
    """A djvu document could not be read — document-level failure (exit 3)."""


# ---------------------------------------------------------------------------
# Subprocess runners
# ---------------------------------------------------------------------------

#: Hard cap on one djvulibre tool invocation, in seconds. Healthy calls are
#: milliseconds (djvused/djvudump) to a few seconds (ddjvu page render); a
#: corrupted stream can hang the tool forever instead — and a hung worker
#: thread would wedge the whole pipeline (ThreadPoolExecutor.shutdown waits
#: for it), with no page.error, no exit code, no log. A timeout turns that
#: silent wedge into the ordinary typed failure path (DjvuError → page.error
#: → exit 2/3). Generous by design: this guards against hangs, not slowness.
TOOL_TIMEOUT_S = 300.0


def _run(cmd: list[str], *, stdin: bytes | None = None) -> bytes:
    """Run an external tool, raising :class:`DjvuError` on failure or hang."""
    try:
        proc = subprocess.run(
            cmd,
            input=stdin,
            capture_output=True,
            check=False,
            timeout=TOOL_TIMEOUT_S,
        )
    except FileNotFoundError as exc:
        raise DjvuError(f"tool not found: {cmd[0]} (install djvulibre-bin)") from exc
    except subprocess.TimeoutExpired as exc:
        raise DjvuError(
            f"{cmd[0]} timed out after {TOOL_TIMEOUT_S:g}s (corrupt document?)"
        ) from exc
    if proc.returncode != 0:
        err = proc.stderr.decode(errors="replace")[:300]
        raise DjvuError(f"{cmd[0]} exited {proc.returncode}: {err}")
    return proc.stdout


def _djvused(path: str, command: str) -> str:
    """One ``djvused -e`` invocation, stdout as text."""
    out = _run(["djvused", "-u", path, "-e", command])
    return out.decode("utf-8", errors="replace")


# ---------------------------------------------------------------------------
# Page metadata (count, per-page stored DPI)
# ---------------------------------------------------------------------------


def djvu_page_count(path: str) -> int:
    """Number of pages via ``djvused -e n``."""
    out = _djvused(path, "n").strip()
    try:
        return int(out)
    except ValueError as exc:
        raise DjvuError(f"djvused -e n returned {out!r} for {path}") from exc


@functools.lru_cache(maxsize=32)
def _cached_dpis(path: str, _mtime_ns: int, _size: int) -> tuple[int, ...]:
    """Per-page stored DPI parsed from ``djvudump`` (cache keyed by stat)."""
    out = _run(["djvudump", path]).decode("utf-8", errors="replace")
    dpis: list[int] = []
    for line in out.splitlines():
        if "FORM:DJVU" in line and "FORM:DJVI" not in line:
            dpis.append(300)  # placeholder until the page's INFO line appears
        elif "INFO" in line:
            m = re.search(r"(\d+)\s*dpi", line)
            if m and dpis:
                dpis[-1] = int(m.group(1))
    return tuple(dpis)


def djvu_stored_dpis(path: str) -> tuple[int, ...]:
    """Stored DPI per page (1 value per ``FORM:DJVU``), or ``()`` on doubt.

    The value feeds ``-scale=min(dpi, stored_dpi)``: 72-dpi pages must not
    be upscaled (zero information gain), 300-dpi pages need not render
    above the requested dpi. If ``djvudump`` output does not line up with
    the page count, we return ``()`` and the renderer falls back to the
    caller's dpi verbatim — degraded, never wrong.
    """
    try:
        st = os.stat(path)
    except OSError:
        return ()
    dpis = _cached_dpis(path, st.st_mtime_ns, st.st_size)
    return dpis


def _stored_dpi_for_page(path: str, page_idx: int, default: int = 300) -> int:
    dpis = djvu_stored_dpis(path)
    if not dpis or page_idx >= len(dpis):
        return default
    return dpis[page_idx]


# ---------------------------------------------------------------------------
# Hidden text layer
# ---------------------------------------------------------------------------

# print-pure-txt control separators (djvused man): \f between pages,
# \013 between columns, \035 between regions, \037 between paragraphs.
# Layer text is untrusted scan output — internal \f has been observed
# (196 \f for 186 pages on a corpus file), so whole-document output is
# only used as a fast emptiness probe; exact per-page text always comes
# from per-page ``select N; print-pure-txt`` calls (~39 ms each, noise
# next to OCR).
_TEXT_CTRL = {0x0C: "\n", 0x0B: "\n", 0x1D: "\n", 0x1F: "\n"}


def _clean_layer_text(raw: str) -> str:
    for code, repl in _TEXT_CTRL.items():
        raw = raw.replace(chr(code), repl)
    return raw.strip()


def djvu_page_texts(path: str, page_count: int) -> list[str]:
    """Hidden text per page (0-indexed list), ``""`` where no layer.

    Fast path: one whole-document ``print-pure-txt``; if it is blank
    (after dropping page separators) every page is layerless and we are
    done — 1 spawn for the 30/33 layerless corpus files. Otherwise fall
    back to per-page extraction for exact page attribution.
    """
    try:
        whole = _clean_layer_text(_djvused(path, "print-pure-txt"))
    except DjvuError as exc:
        logger.warning("whole-doc print-pure-txt failed (%s), treating as layerless: %s", exc, path)
        return [""] * page_count

    if not whole:
        return [""] * page_count
    if page_count == 1:
        return [whole]
    texts = []
    for i in range(page_count):
        try:
            texts.append(_clean_layer_text(_djvused(path, f"select {i + 1}; print-pure-txt")))
        except DjvuError:
            texts.append("")
    return texts


# ---------------------------------------------------------------------------
# Page raster (the djvu half of the rasterize seam; called by rasterize.py)
# ---------------------------------------------------------------------------


def render_page_rgb_djvu(
    path: str, page_idx: int, *, dpi: int = 200
) -> tuple[int, int, bytes]:
    """Render one djvu page to raw RGB via ``ddjvu`` (stdout, no temp files).

    ``page_idx`` is 0-indexed (pipeline convention); ``ddjvu -page`` is
    1-indexed — converted here, exactly once. ``-scale`` is unitless dpi
    against the page's stored dpi, capped at native (research: upscaling
    a 72-dpi page gains nothing).

    Returns:
        ``(width, height, rgb_bytes)`` — PPM body, P6 header parsed.

    Raises:
        :class:`DjvuError`: ddjvu failure, out-of-range page, or a PPM
            stream that does not parse. Out-of-range is validated here
            because ddjvu silently clamps page numbers to the last page.
    """
    n = djvu_page_count(path)
    if not 0 <= page_idx < n:
        raise DjvuError(f"page index {page_idx} out of range (0..{n - 1})")
    scale = min(dpi, _stored_dpi_for_page(path, page_idx))
    out = _run(
        [
            "ddjvu",
            "-format=ppm",
            f"-page={page_idx + 1}",
            f"-scale={scale}",
            path,
            "-",
        ]
    )
    return _parse_ppm(out, context=f"page {page_idx + 1} of {path}")


def _parse_ppm(data: bytes, *, context: str) -> tuple[int, int, bytes]:
    """Parse a binary PPM (P6) buffer into ``(w, h, rgb)``."""
    if not data.startswith(b"P6"):
        raise DjvuError(f"ddjvu: unexpected raster format for {context}")
    pos = 2
    fields: list[int] = []
    while len(fields) < 3:
        # Skip whitespace and #-comments between header fields.
        while pos < len(data) and data[pos : pos + 1].isspace():
            pos += 1
        if pos < len(data) and data[pos : pos + 1] == b"#":
            while pos < len(data) and data[pos] not in (0x0A, 0x0D):
                pos += 1
            continue
        start = pos
        while pos < len(data) and data[pos : pos + 1].isdigit():
            pos += 1
        if start == pos:
            raise DjvuError(f"malformed PPM header for {context}")
        fields.append(int(data[start:pos]))
    # Exactly one whitespace byte separates the header from the sample data.
    pos += 1
    w, h, maxval = fields
    if maxval != 255:
        raise DjvuError(f"unsupported PPM maxval {maxval} for {context}")
    body = data[pos : pos + w * h * 3]
    if len(body) < w * h * 3:
        raise DjvuError(f"truncated PPM body for {context}")
    return w, h, body


# ---------------------------------------------------------------------------
# Inspector (path -> IR)
# ---------------------------------------------------------------------------


def extract_ir(path: str) -> IR:
    """Native djvu -> IR, one page per djvu page (ADR-0004).

    Pure: reads the file, writes nothing. Page rasters are NOT rendered
    here — OCR renders lazily through the seam, only for pages that
    need it (TKT-DJV-12: per-page on demand is fine).

    Raises:
        DjvuError: djvused/djvudump failed on the document — a
            document-level failure (exit 3), not a format rejection.
    """
    t0 = time.perf_counter()
    n = djvu_page_count(path)
    if n <= 0:
        raise DjvuError(f"djvu document has no pages: {path}")
    texts = djvu_page_texts(path, n)

    pages: list[PageResult] = []
    needing_ocr: list[int] = []  # 1-indexed
    for i, text in enumerate(texts):
        has_layer = bool(text)
        if not has_layer:
            needing_ocr.append(i + 1)
        pages.append(
            PageResult(
                page=i,
                markdown=text,
                needs_ocr=not has_layer,
                # Honest provenance (docx precedent): the adapter produced
                # this page's markdown — DJVU_TEXT_LAYER where the layer
                # yielded text, DJVU_ADAPTER where it is empty. OCR
                # overwrites both markdown and src_tool when it runs.
                src_tool=(
                    SrcTool.DJVU_TEXT_LAYER if has_layer else SrcTool.DJVU_ADAPTER
                ),
                blocks=[],  # no structural data in djvu (TKT-DJV-04)
                images=[],  # djvu never produces sidecar images (TKT-DJV-06)
            )
        )

    elapsed_ms = (time.perf_counter() - t0) * 1000.0
    metadata = Metadata(
        pdf_type="scanned",  # djvu is a raster-scan format by nature
        page_count=n,
        confidence=1.0,
        processing_time_ms=round(elapsed_ms, 1),
        title=None,
        pages_needing_ocr=needing_ocr,
        ocr_reasons_by_page=[
            OcrReasonEntry(page=p, reasons=["inspector_needs_ocr"])
            for p in needing_ocr
        ],
        source_format="djvu",
    )
    return IR(metadata=metadata, pages=pages)


# ---------------------------------------------------------------------------
# --info payload (one door: PDF classifies, docx/djvu inspect — TKT-DX-06)
# ---------------------------------------------------------------------------


def djvu_info(path: str) -> dict:
    """Metadata summary for ``--info`` — derived from the same inspector.

    One parser, no drift: the counts come from the IR the pipeline would
    build (including the per-page text-layer extraction).
    """
    ir = extract_ir(path)
    return {
        "source_format": ir.metadata.source_format,
        "pdf_type": ir.metadata.pdf_type,
        "page_count": ir.metadata.page_count,
        "confidence": ir.metadata.confidence,
        "pages_needing_ocr": list(ir.metadata.pages_needing_ocr),
        "pages_with_text_layer": [
            p.page + 1 for p in ir.pages if p.markdown.strip()
        ],
    }
