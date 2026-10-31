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
from pdf_router.ir import from_dict
from pdf_router.renderers import docling as docling_renderer

logger = logging.getLogger("pdf_router")

DEFAULT_CONFIG_NAME = "router.toml"
VALID_RENDERERS = ("md", "chunks", "json", "docling", "zvec")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="pdf-router",
        description="Deterministic PDF -> LLM-ready Markdown router.",
    )
    parser.add_argument("--version", action="version", version=f"pdf-router {__version__}")
    parser.add_argument(
        "--config",
        metavar="PATH",
        default=None,
        help="TOML config path (default: ./router.toml if present)",
    )
    parser.add_argument("--out", metavar="DIR", default=None, help="Output directory (default: out/)")
    parser.add_argument(
        "--render",
        metavar="md,chunks,json,docling,zvec",
        default=None,
        help="Comma-separated subset of renderers (default: md,chunks,json,zvec)",
    )
    parser.add_argument("--info", action="store_true", help="Print metadata JSON only, no render")
    parser.add_argument(
        "--log-level",
        metavar="LEVEL",
        default=None,
        choices=["debug", "info", "warning", "error"],
        help="Override [router].log_level",
    )
    parser.add_argument(
        "input",
        nargs="?",
        help="Input document path (format detected by content; PDF, DOCX, XLSX, DOC, HTML and DJVU today)",
    )
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


def _info_payload(fmt: Format, path: Path) -> dict:
    """Build the ``--info`` JSON payload for a detected format (TKT-DX-06).

    One door: every format answers from its own inspector's metadata
    summary — the same parser the pipeline uses, no drift.
    """
    if fmt is Format.PDF:
        import pdf_inspector

        c = pdf_inspector.classify_pdf(str(path))
        return {
            "source_format": "pdf",
            "pdf_type": c.pdf_type,
            "page_count": c.page_count,
            "confidence": c.confidence,
            "pages_needing_ocr": list(c.pages_needing_ocr or []),
        }
    if fmt is Format.DOCX:
        from pdf_router.adapters.docx import docx_info

        return docx_info(str(path))
    if fmt is Format.DJVU:
        from pdf_router.adapters.djvu import djvu_info

        return djvu_info(str(path))
    if fmt is Format.XLSX:
        from pdf_router.adapters.xlsx import xlsx_info

        return xlsx_info(str(path))
    if fmt is Format.DOC:
        from pdf_router.adapters.doc import doc_info

        return doc_info(str(path))
    if fmt is Format.HTML:
        from pdf_router.adapters.html import html_info

        return html_info(str(path))
    raise UnsupportedFormatError(f"--info does not support {fmt.name} yet")


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
    if "docling" in cfg.renderers.enabled:
        # Fail fast: the docling renderer needs the optional extra, and a
        # missing extra is a usage error — not something the user should
        # learn about after the document was processed (OCR included).
        try:
            docling_renderer.ensure_available()
        except docling_renderer.DoclingUnavailableError as exc:
            print(f"error: {exc}", file=sys.stderr)
            return 1
    log_level = (args.log_level or cfg.log_level).upper()
    logging.basicConfig(
        level=getattr(logging, log_level, logging.WARNING),
        format="%(levelname)s %(name)s: %(message)s",
    )

    # --info: one door (TKT-DX-06): PDFs go to the Rust classifier, docx
    # / djvu / xlsx to their native inspectors' metadata summaries; other
    # recognized formats are a typed refusal. A corrupt document is a
    # document failure (exit 3), an unroutable format a usage error (1).
    if args.info:
        try:
            fmt = detect_format(input_path)
        except UnsupportedFormatError as exc:
            print(f"error: {exc}", file=sys.stderr)
            return 1
        try:
            payload = _info_payload(fmt, input_path)
        except UnsupportedFormatError as exc:
            print(f"error: {exc}", file=sys.stderr)
            return 1
        except Exception as exc:  # corrupt document → document failure
            print(f"error: {exc}", file=sys.stderr)
            return 3
        print(json.dumps(payload, indent=2, ensure_ascii=False))
        return 0

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

    # Fast-path for pre-computed IR files (.ir.json or .json):
    # Skip OCR and heavy pipeline, directly render outputs (e.g. zvec JSONL).
    if input_path.suffix.lower() == ".json":
        try:
            raw_ir = json.loads(input_path.read_text(encoding="utf-8"))
            ir = from_dict(raw_ir)
            stem = input_path.name
            for sfx in (".ir.json", ".json"):
                if stem.endswith(sfx):
                    stem = stem[:-len(sfx)]
                    break
            write_outputs(ir, out_dir, stem, config=cfg.renderers)
            return 0
        except docling_renderer.DoclingUnavailableError as exc:
            print(f"error: {exc}", file=sys.stderr)
            return 1
        except Exception as exc:
            logger.error("rendering from IR JSON failed: %s", exc)
            print(f"error: {exc}", file=sys.stderr)
            return 1

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
    except docling_renderer.DoclingUnavailableError as exc:
        # The docling renderer needs the optional extra: a usage error
        # (install the extra), not a document failure.
        print(f"error: {exc}", file=sys.stderr)
        return 1
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
