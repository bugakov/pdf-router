"""Thin argparse CLI over the Python API. DESIGN.md §8.

Exit codes (locked):
- 0 — full success
- 1 — usage / IO error (file not found, config invalid)
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
from pdf_router.api import write_outputs

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
    parser.add_argument("input", nargs="?", help="Input PDF path")
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
    if args.out is not None:
        cfg.renderers.output_dir = Path(args.out)
    log_level = (args.log_level or cfg.log_level).upper()
    logging.basicConfig(level=getattr(logging, log_level, logging.WARNING), format="%(levelname)s %(name)s: %(message)s")

    # --info: classify only, always exit 0 if the file was readable.
    if args.info:
        import pdf_inspector

        c = pdf_inspector.classify_pdf(str(input_path))
        print(
            json.dumps(
                {
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

    from pdf_router.core import process_pdf

    try:
        ir, report = process_pdf(str(input_path), cfg)
    except Exception as exc:  # document-level failure → exit 3
        logger.error("document processing failed: %s", exc)
        print(f"error: {exc}", file=sys.stderr)
        return 3

    try:
        write_outputs(ir, cfg.renderers.output_dir, input_path.stem, config=cfg.renderers)
    except OSError as exc:
        print(f"error: cannot write output: {exc}", file=sys.stderr)
        return 1

    if report.pages_failed > 0:
        return 1  # Some pages failed
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
