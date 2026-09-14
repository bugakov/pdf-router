"""pdf-router: ultra-minimal, deterministic PDF -> LLM-ready Markdown router.

This package's public surface is defined in :mod:`pdf_router.api` and
re-exported here verbatim. ``import pdf_router`` is all an embedding
application needs::

    import pdf_router

    md = pdf_router.convert("scan.pdf")

The submodules (``core``, ``config``, ``ir``, ``ocr``, ...) are
implementation details; importing them directly is unsupported. Heavy
dependencies (pymupdf, onnxruntime, rapidocr) are loaded lazily inside
functions, so ``import pdf_router`` stays fast.
"""

from pdf_router.api import (
    ConfigError,
    IR,
    IR_VERSION,
    Metadata,
    PageResult,
    ProcessingError,
    ProcessingReport,
    RendererConfig,
    RouterConfig,
    SrcTool,
    UnsupportedFormatError,
    convert,
    from_dict,
    load_config,
    process_pdf,
    render_chunks,
    render_json,
    render_markdown,
    to_dict,
    wire_pdf_inspector_env,
    write_outputs,
)

__version__ = "0.5.0"

__all__ = [
    "__version__",
    # Entry points
    "convert",
    "load_config",
    "process_pdf",
    "render_chunks",
    "render_json",
    "render_markdown",
    "wire_pdf_inspector_env",
    "write_outputs",
    # Errors
    "ConfigError",
    "ProcessingError",
    "UnsupportedFormatError",
    # Types
    "IR",
    "IR_VERSION",
    "Metadata",
    "PageResult",
    "ProcessingReport",
    "RendererConfig",
    "RouterConfig",
    "SrcTool",
    "from_dict",
    "to_dict",
]
