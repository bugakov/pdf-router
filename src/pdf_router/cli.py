"""Thin argparse CLI over the Python API. DESIGN.md §8.

Exit codes (locked):
- 0 — full success
- 1 — usage / IO error (file not found, config invalid, unsupported input format)
- 2 — partial success (>=1 page failed, >=1 page succeeded)
- 3 — total failure (pdf-inspector raised on the document itself)
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
from pathlib import Path

from pdf_router import __version__
from pdf_router.config import ConfigError, RouterConfig
from pdf_router.api import wire_pdf_inspector_env, write_outputs
from pdf_router.formats import Format, UnsupportedFormatError, detect_format

logger = logging.getLogger("pdf_router")

DEFAULT_CONFIG_NAME = "router.toml"
VALID_RENDERERS = ("md", "chunks", "json")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="pdf-router",
        description="Deterministic PDF -> LLM-ready Markdown router.",
    )
    parser.add_argument("--version", action="version", version=f"pdf-router {__version__}")
    parser.add_argument("--config", metavar="PATH", default=None, help="TOML config path (default: ./router.toml if present)")
    parser.add_argument("--out", metavar="DIR", default=None, help="Output directory (default: out/)")
    parser.add_argument(
        "--render",
        metavar="md,chunks,json",
        default=None,
        help="Comma-separated subset of renderers (default: all three)",
    )
    parser.add_argument("--info", action="store_true", help="Print metadata JSON only, no render")
    parser.add_argument(
        "--log-level",
        metavar="LEVEL",
        default=None,
        choices=["debug", "info", "warning", "error"],
        help="Override [router].log_level",
    )
    parser.add_argument("input", nargs="?", help="Input document path (format detected by content; PDF and DOCX today)")
    return parser


def _resolve_config_path(explicit: str | None) -> str | None:
    if explicit is not None:
        return explicit
    cwd_config = Path(DEFAULT_CONFIG_NAME)
    return str(cwd_config) if cwd_config.exists() else None


def _parse_renderers(raw: str | None) -> set[str] | None:
    if raw is None:
        return None
    requested = {part.strip() for part in raw.split(",") if part.strip()}
    unknown = requested - set(VALID_RENDERERS)
    if unknown:
        raise SystemExit(f"error: unknown renderer(s): {', '.join(sorted(unknown))}")
    if not requested:
        raise SystemExit("error: --render must name at least one renderer")
    return requested


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    if args.input is None:
        parser.error("INPUT.pdf is required")

    input_path = Path(args.input)
    if not input_path.is_file():
        print(f"error: file not found: {input_path}", file=sys.stderr)
        return 1

    # Config: CLI flag > ./router.toml (if present) > built-in defaults.
    config_path = _resolve_config_path(args.config)
    try:
        cfg = RouterConfig.from_toml(config_path)
    except ConfigError as exc:
        print(f"error: invalid config: {exc}", file=sys.stderr)
        return 1

    # CLI overrides.
    try:
        enabled = _parse_renderers(args.render)
    except SystemExit as exc:
        print(exc, file=sys.stderr)
        return 1
    if enabled is not None:
        cfg.renderers.enabled = enabled
    log_level = (args.log_level or cfg.log_level).upper()
    logging.basicConfig(level=getattr(logging, log_level, logging.WARNING), format="%(levelname)s %(name)s: %(message)s")

    # --info: one door (TKT-DX-06): PDFs go to the Rust classifier, docx to
    # the native inspector's metadata summary; other recognized formats are
    # a typed refusal. Always exit 0 if the file was readable.
    if args.info:
        try:
            fmt = detect_format(input_path)
        except UnsupportedFormatError as exc:
            print(f"error: {exc}", file=sys.stderr)
            return 1
        if fmt is Format.PDF:
            import pdf_inspector

            c = pdf_inspector.classify_pdf(str(input_path))
            print(
                json.dumps(
                    {
                        "source_format": "pdf",
                        "pdf_type": c.pdf_type,
                        "page_count": c.page_count,
                        "confidence": c.confidence,
                        "pages_needing_ocr": list(c.pages_needing_ocr or []),
                    },
                    indent=2,
                    ensure_ascii=False,
                )
            )
            return 0
        if fmt is Format.DOCX:
            from pdf_router.adapters.docx import docx_info

            print(json.dumps(docx_info(str(input_path)), indent=2, ensure_ascii=False))
            return 0
        print(
            f"error: --info does not support {fmt.name} yet",
            file=sys.stderr,
        )
        return 1

    # pdf-inspector's Rust core is env-wired (PDFIUM_LIB_PATH/ORT_DYLIB_PATH);
    # the CLI bridges the TOML paths into the process env here. The library
    # itself never writes os.environ.
    wire_pdf_inspector_env(cfg)

    # Output policy belongs to the caller: the CLI resolves the single output
    # directory (--out > [renderers].output_dir > ./out) and passes it
    # explicitly to the pipeline (sidecar images) and write_outputs (rendered
    # files). The library core never reads the config key.
    out_dir = (
        Path(args.out)
        if args.out is not None
        else (cfg.renderers.output_dir or Path("out"))
    )

    from pdf_router.core import process_pdf

    try:
        ir, report = process_pdf(str(input_path), cfg, output_dir=out_dir)
    except UnsupportedFormatError as exc:
        # Usage error, not a document failure: nothing was processed.
        print(f"error: {exc}", file=sys.stderr)
        return 1
    except Exception as exc:  # document-level failure → exit 3
        logger.error("document processing failed: %s", exc)
        print(f"error: {exc}", file=sys.stderr)
        return 3

    try:
        write_outputs(ir, out_dir, input_path.stem, config=cfg.renderers)
    except OSError as exc:
        print(f"error: cannot write output: {exc}", file=sys.stderr)
        return 1

    if report.pages_failed > 0:
        # Partial success: outputs are written, but some pages failed.
        # Exit 2 (distinct from 1 = nothing happened). List every failed
        # page on stderr so the failure is visible without reading ir.json.
        for page in ir.pages:
            if page.error:
                print(f"error: page {page.page + 1}: {page.error}", file=sys.stderr)
        print(
            f"error: {report.pages_failed} of {report.pages_total} page(s) failed"
            f" ({report.pages_image_failed} with image extraction failure);"
            " outputs written",
            file=sys.stderr,
        )
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
