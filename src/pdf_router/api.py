"""Public facade — the supported way to embed pdf-router in an application.

Everything re-exported by ``pdf_router/__init__.py`` lives here. The point of
this module is that a caller needs **no knowledge of the package internals**:
no ``core``, no ``config`` imports, no dataclass construction. The happy path
is one call::

    import pdf_router

    markdown = pdf_router.convert("scan.pdf")

Three levels of control, in increasing depth:

1. :func:`convert` — one call, returns Markdown. For the 90% case.
2. :func:`process_pdf` — returns ``(IR, ProcessingReport)``. For callers that
   need provenance, per-page images, or failure counts.
3. :func:`render_markdown` / :func:`render_chunks` / :func:`render_json` —
   render an IR you already hold (e.g. one loaded via :func:`from_dict`).

Four rules this module exists to enforce:

- **Config is optional and polymorphic.** Every entry point accepts
  ``None | str | Path | RouterConfig``. ``None`` means built-in defaults, so a
  caller never has to import ``RouterConfig`` just to get going. The value is
  coerced **once, up front** (in :func:`convert` / :func:`process_pdf`), so a
  bad TOML path fails before the first page is processed — never deep inside
  a renderer after the pipeline has already run.
- **The library writes nothing unless the call says so.** :func:`convert`
  and :func:`process_pdf` are pure by default (no directories, no sidecar
  files); ``output_dir=`` turns on sidecar image extraction;
  :func:`write_outputs` writes rendered files. ``[renderers].output_dir``
  in the config is the CLI's default directory — the library core never
  reads it.
- **The format is detected by content, not by name.** Every entry point
  sniffs magic bytes before any page work (``pdf_router.formats``): a docx
  renamed to ``.pdf`` converts as docx, a PDF renamed to ``.docx``
  converts as PDF. Unroutable input (djvu today, unrecognized bytes)
  raises :class:`UnsupportedFormatError` at the boundary — never an
  untyped error from deep inside the pipeline.
- **Import must stay cheap.** Nothing here may import a heavy dependency
  (pymupdf, onnxruntime, rapidocr, requests, pdf_inspector) at module scope —
  ``tests/test_api.py::test_public_import_pulls_no_heavy_deps`` guards it.
  Downstream apps import pdf-router at startup; a 3-second import is a bug.
"""

from __future__ import annotations

import logging
import os
from collections.abc import Callable
from pathlib import Path
from typing import Any

from pdf_router.config import (
    ConfigError,
    RendererConfig,
    RouterConfig,
)
from pdf_router.core import (
    PipelineCancelled,
    ProcessingReport,
    ProgressEvent,
    process_pdf as _pipeline,
)
from pdf_router.formats import UnsupportedFormatError
from pdf_router.ir import (
    IR,
    IR_VERSION,
    Metadata,
    PageResult,
    SrcTool,
    from_dict,
    to_dict,
)
from pdf_router.ocr_cache import OcrCache, params_fingerprint

logger = logging.getLogger("pdf_router")

__all__ = [
    "ConfigError",
    "OcrCache",
    "PipelineCancelled",
    "ProcessingError",
    "ProcessingReport",
    "ProgressEvent",
    "UnsupportedFormatError",
    "convert",
    "load_config",
    "params_fingerprint",
    "process_pdf",
    "render_chunks",
    "render_json",
    "render_markdown",
    "wire_pdf_inspector_env",
    "write_outputs",
    # Re-exported types, so a caller never imports a submodule.
    "IR",
    "IR_VERSION",
    "Metadata",
    "PageResult",
    "RendererConfig",
    "RouterConfig",
    "SrcTool",
    "from_dict",
    "to_dict",
]


class ProcessingError(RuntimeError):
    """Raised by ``strict=True`` entry points when at least one page failed.

    Carries the :class:`ProcessingReport` so the caller can tell "one page of
    44 died" from "the whole document is unreadable" without parsing strings.
    """

    def __init__(self, report: ProcessingReport, message: str | None = None) -> None:
        self.report = report
        super().__init__(
            message
            or f"{report.pages_failed} of {report.pages_total} page(s) failed"
        )


def _coerce_config(config: str | Path | RouterConfig | None) -> RouterConfig:
    """Accept any of the four shapes a caller plausibly has.

    - ``None`` → built-in defaults (most common; keeps the happy path trivial).
    - ``RouterConfig`` → used as-is.
    - ``str`` / ``Path`` → loaded from TOML.

    Unlike :meth:`RouterConfig.from_toml`, an explicit path that does not
    exist is an error rather than a silent fall back to defaults: a typo in a
    config path must not quietly change routing behaviour in production.
    """
    if config is None:
        return RouterConfig()
    if isinstance(config, RouterConfig):
        return config
    if isinstance(config, (str, Path)):
        path = Path(config)
        if not path.exists():
            raise ConfigError(f"config file not found: {path}")
        return RouterConfig.from_toml(path)
    raise ConfigError(
        f"config must be None, a path, or RouterConfig; got {type(config).__name__}"
    )


def _coerce_renderer_config(config: RendererConfig | RouterConfig | None) -> RendererConfig:
    """Renderers take a ``RendererConfig``; callers often hold a ``RouterConfig``."""
    if config is None:
        return RendererConfig()
    if isinstance(config, RouterConfig):
        return config.renderers
    if isinstance(config, RendererConfig):
        return config
    raise ConfigError(
        f"renderer config must be RendererConfig or RouterConfig; got {type(config).__name__}"
    )


def wire_pdf_inspector_env(config: RouterConfig) -> None:
    """Bridge ``[ocr.pdf_inspector_ocr]`` paths into the process environment.

    pdf-inspector's Rust core reads ``PDFIUM_LIB_PATH`` and
    ``ORT_DYLIB_PATH`` from the environment — no function kwargs exist for
    them. This function is the only sanctioned bridge; the library itself
    never writes ``os.environ`` (an app's process must not be mutated
    behind its back).

    Behaviour:
    - No-op unless ``routing.ocr_fallback_engine == "pdf_inspector_ocr"``.
    - Sets each var from the TOML section. If the env already holds a
      *different* value, it is overridden with a warning (the TOML section
      is the single source of truth).
    - The CLI calls this automatically before processing; a library caller
      invokes it once before :func:`process_pdf`.
    """
    if config.routing.ocr_fallback_engine != "pdf_inspector_ocr":
        return
    params = config.ocr_engines.get("pdf_inspector_ocr", {})
    for env_name, key in (
        ("PDFIUM_LIB_PATH", "pdfium_lib_path"),
        ("ORT_DYLIB_PATH", "ort_dylib_path"),
    ):
        value = params.get(key)
        if not value:
            continue  # required-keys validation guarantees presence at load
        value = str(value)
        current = os.environ.get(env_name)
        if current == value:
            continue
        if current is not None:
            logger.warning(
                "overriding %s=%r with [ocr.pdf_inspector_ocr] %s=%r",
                env_name,
                current,
                key,
                value,
            )
        os.environ[env_name] = value


def load_config(path: str | Path | None = None) -> RouterConfig:
    """Load a :class:`RouterConfig` from a TOML file.

    ``path=None`` returns built-in defaults — the same object shape a caller
    would get from an empty file, so defaults are inspectable and overridable
    in Python::

        cfg = pdf_router.load_config()
        cfg.routing.language = "ru"
        md = pdf_router.convert("scan.pdf", config=cfg)

    Raises:
        ConfigError: if the path is given but missing, or the TOML is invalid.
    """
    return _coerce_config(path)


def process_pdf(
    pdf_path: str | Path,
    *,
    config: str | Path | RouterConfig | None = None,
    output_dir: str | Path | None = None,
    strict: bool = False,
    progress: "Callable[[ProgressEvent], None] | None" = None,
    ocr_cache: "Any | None" = None,
    content_md5: str | None = None,
) -> tuple[IR, ProcessingReport]:
    """Run the full routing pipeline. Returns ``(IR, ProcessingReport)``.

    This is the deep entry point: everything :func:`convert` does, plus the
    IR (provenance, structural blocks, per-page images) and the report
    (native/OCR/failed counts).

    Args:
        pdf_path: path to the document. The format is detected from magic
            bytes, not the extension; PDF and DOCX are routable today.
        config: ``None``, a TOML path, or a :class:`RouterConfig`.
        output_dir: sidecar image directory. ``None`` (default) keeps the
            run pure: nothing is written to disk and ``page.images`` stays
            empty. A path turns on image extraction under
            ``<output_dir>/<doc-id>/images/`` (relative paths stored on
            ``page.images``).
        strict: raise :class:`ProcessingError` if any page failed. Default
            ``False`` reproduces the CLI's best-effort behaviour: failed pages
            carry ``PageResult.error`` and the rest of the document is fine.
        progress: optional callback invoked after each page is finalized,
            in document order, on the calling thread — no thread-safety
            required. Raising :class:`PipelineCancelled` inside it is the
            sanctioned cancel protocol: the run stops cleanly (pages already
            done keep their results, never-started pages get
            ``error="cancelled"`` and count into ``report.pages_cancelled``
            and ``pages_failed``). Any other exception is treated as a bug
            in the caller's code: logged with traceback, counted in
            ``report.progress_callback_errors``, never fatal.
        ocr_cache (ADR-0007): optional :class:`pdf_router.ocr_cache.OcrCache`.
            ``None`` (default) keeps the run pure — no cache reads or writes.
            When supplied, OCR page results are looked up before running the
            OCR engine and stored after it, keyed by
            ``(content_md5, page_idx, engine, params)`` — a rerun of the
            same document pays OCR only for the pages that never finished.
            The cache object must be thread-safe (pages are processed
            concurrently).
        content_md5: hex MD5 digest of the document bytes, required for the
            cache key when ``ocr_cache`` is given. ``None`` (default) means
            the digest is computed lazily inside the pipeline on first OCR
            page (one extra read of the file, only when needed).

    Raises:
        ConfigError: bad ``config``.
        UnsupportedFormatError: the input is not a routable document
            (checked before any page work).
        ProcessingError: ``strict=True`` and >=1 page failed — note that
            cancelled pages count as failed.
    """
    cfg = _coerce_config(config)
    out_dir = Path(output_dir) if output_dir is not None else None
    ir, report = _pipeline(
        str(pdf_path),
        cfg,
        output_dir=out_dir,
        progress=progress,
        ocr_cache=ocr_cache,
        content_md5=content_md5,
    )
    if strict and report.pages_failed:
        raise ProcessingError(report)
    return ir, report


def convert(
    pdf_path: str | Path,
    *,
    config: str | Path | RouterConfig | None = None,
    strict: bool = False,
    progress: "Callable[[ProgressEvent], None] | None" = None,
) -> str:
    """Convert a PDF to LLM-ready Markdown in one call.

    ``pdf_router.convert("scan.pdf")`` is the whole integration. Writes no
    files and needs no configuration — the pipeline runs fully in memory
    (sidecar images are :func:`process_pdf` territory, via ``output_dir=``).

    The config is coerced once, up front: a typo'd TOML path raises
    :class:`ConfigError` before any page is processed. The format is
    detected from magic bytes too: a docx renamed to ``.pdf`` converts as
    docx, a PDF renamed to ``.docx`` converts as PDF, unrecognized content
    raises :class:`UnsupportedFormatError`.

    Raises:
        ConfigError: bad ``config``.
        UnsupportedFormatError: the input is not a routable document
            (checked before any page work).
        ProcessingError: ``strict=True`` and >=1 page failed. Without
            ``strict``, a failed page simply contributes an empty section.

    ``progress=`` is the streaming callback described in :func:`process_pdf`
    (including the :class:`PipelineCancelled` cancel protocol); it is
    forwarded verbatim.
    """
    cfg = _coerce_config(config)
    ir, _ = process_pdf(pdf_path, config=cfg, strict=strict, progress=progress)
    return render_markdown(ir, config=cfg)


def render_markdown(ir: IR, *, config: RendererConfig | RouterConfig | None = None) -> str:
    """Render an IR to a single Markdown string."""
    from pdf_router.renderers import md

    return md.render(ir, config=_coerce_renderer_config(config))


def render_chunks(ir: IR, *, config: RendererConfig | RouterConfig | None = None) -> str:
    """Render an IR to JSONL chunks (one JSON object per line)."""
    from pdf_router.renderers import chunks

    return chunks.render(ir, config=_coerce_renderer_config(config))


def render_json(ir: IR, *, config: RendererConfig | RouterConfig | None = None) -> str:
    """Render an IR to its JSON serialization."""
    from pdf_router.renderers import json_renderer

    return json_renderer.render(ir, config=_coerce_renderer_config(config))


#: Renderer name -> function producing its text. Order is irrelevant; the
#: ``enabled`` set in the config decides what is written.
_RENDERERS = {
    "md": (render_markdown, ".md"),
    "chunks": (render_chunks, ".chunks.jsonl"),
    "json": (render_json, ".ir.json"),
}


def write_outputs(
    ir: IR,
    out_dir: str | Path,
    stem: str,
    *,
    config: RendererConfig | RouterConfig | None = None,
) -> dict[str, Path]:
    """Write the enabled renderers to ``out_dir`` and return their paths.

    One call replaces the boilerplate of "render three ways, mkdir, write
    three files". Which renderers run comes from ``config.renderers.enabled``
    (default: all three).

    Returns:
        Mapping of renderer name to the written path, e.g.
        ``{"md": Path("out/doc.md"), ...}`` — handy for logging and tests.

    Raises:
        OSError: propagated from writing (the caller owns output-path policy).
    """
    rcfg = _coerce_renderer_config(config)
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)

    written: dict[str, Path] = {}
    for name in sorted(rcfg.enabled or ()):
        entry = _RENDERERS.get(name)
        if entry is None:
            raise ConfigError(
                f"unknown renderer {name!r}; expected one of {sorted(_RENDERERS)}"
            )
        render_fn, suffix = entry
        path = out / f"{stem}{suffix}"
        path.write_text(render_fn(ir, config=rcfg), encoding="utf-8")
        written[name] = path
    return written
