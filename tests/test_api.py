"""Contract tests for the public facade (``pdf_router`` package surface).

The facade is the embedding contract: these tests pin what an application
relies on when it does ``import pdf_router`` and nothing else.

- Heavy dependencies must NOT be imported at package import time (startup
  cost guard — an app importing pdf-router at boot must not pay for
  onnxruntime/pymupdf until a PDF is actually processed).
- Config coercion: ``None | str | Path | RouterConfig`` everywhere; a missing
  explicit path is an error, not a silent default.
- Renderer functions accept both ``RendererConfig`` and ``RouterConfig``.
- ``write_outputs`` writes exactly the enabled renderers and returns paths.
- ``convert`` end-to-end on a native-text PDF (no OCR).
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

import pdf_router
from pdf_router import (
    ConfigError,
    IR,
    Metadata,
    PageResult,
    ProcessingError,
    ProcessingReport,
    RouterConfig,
    convert,
    from_dict,
    load_config,
    process_pdf,
    render_chunks,
    render_json,
    render_markdown,
    to_dict,
    write_outputs,
)

HEAVY_MODULES = (
    "pymupdf",
    "fitz",
    "rapidocr",
    "numpy",
    "onnxruntime",
    "requests",
    "pdf_inspector",
)


# ---------------------------------------------------------------------------
# Import-cost contract
# ---------------------------------------------------------------------------


def test_public_import_pulls_no_heavy_deps() -> None:
    """``import pdf_router`` must not import any heavy dependency.

    Runs in a fresh interpreter: the test process itself has already imported
    them transitively via other tests.
    """
    code = (
        "import pdf_router, sys\n"
        "heavy = [m for m in %r if m in sys.modules]\n"
        "print(','.join(heavy))\n" % (HEAVY_MODULES,)
    )
    proc = subprocess.run(
        [sys.executable, "-c", code],
        capture_output=True,
        text=True,
        check=True,
        cwd=str(Path(__file__).parent.parent),
    )
    assert proc.stdout.strip() == "", f"heavy deps imported at package import: {proc.stdout}"


def test_all_exports_resolve() -> None:
    """Every name in ``__all__`` is importable from the package (no rot)."""
    for name in pdf_router.__all__:
        assert hasattr(pdf_router, name), f"pdf_router.__all__ entry missing: {name!r}"


# ---------------------------------------------------------------------------
# Config coercion
# ---------------------------------------------------------------------------


def test_load_config_none_gives_defaults() -> None:
    cfg = load_config()
    assert isinstance(cfg, RouterConfig)
    assert cfg.routing.ocr_fallback_engine == "rapidocr"


def test_load_config_missing_path_is_error(tmp_path: Path) -> None:
    # A typo'd config path must not silently fall back to defaults.
    with pytest.raises(ConfigError, match="not found"):
        load_config(tmp_path / "nope.toml")


def test_entry_points_accept_all_config_shapes(p5_pdf: Path) -> None:
    """``None``, TOML path and RouterConfig all work for the same call."""
    ir1, rep1 = process_pdf(p5_pdf, config=None)
    ir2, rep2 = process_pdf(p5_pdf, config=load_config())
    assert ir1.pages[0].markdown == ir2.pages[0].markdown
    assert rep1.pages_total == rep2.pages_total == 1


# ---------------------------------------------------------------------------
# Pipeline (PDF-backed)
# ---------------------------------------------------------------------------


def test_process_pdf_returns_ir_and_report(p5_pdf: Path) -> None:
    ir, report = process_pdf(p5_pdf)
    assert isinstance(ir, IR)
    assert isinstance(report, ProcessingReport)
    assert report.pages_total == 1
    assert report.pages_failed == 0
    page = ir.pages[0]
    assert page.markdown.strip()
    assert page.error is None


def test_convert_returns_markdown_string(p5_pdf: Path) -> None:
    md = convert(p5_pdf)
    assert isinstance(md, str)
    assert md.strip()


def test_strict_raises_when_pages_failed(p5_pdf: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """strict=True surfaces per-page failures instead of swallowing them."""
    import pdf_router.api as api

    real_ir, report = process_pdf(p5_pdf)
    report.pages_failed = 1  # simulate a partial failure as core would record it

    def fake_pipeline(path: str, cfg: RouterConfig) -> tuple[IR, ProcessingReport]:
        return real_ir, report

    monkeypatch.setattr(api, "_pipeline", fake_pipeline)
    with pytest.raises(ProcessingError) as excinfo:
        api.process_pdf(p5_pdf, strict=True)
    assert excinfo.value.report.pages_failed == 1

    # Default (best-effort) must NOT raise.
    _, rep = api.process_pdf(p5_pdf)
    assert rep.pages_failed == 1


def test_ir_roundtrip_via_public_api(p5_pdf: Path) -> None:
    """to_dict/from_dict are on the package surface and round-trip."""
    ir, _ = process_pdf(p5_pdf)
    d = to_dict(ir)
    ir2 = from_dict(d)
    assert ir2.pages[0].markdown == ir.pages[0].markdown
    assert ir2.metadata.page_count == ir.metadata.page_count


# ---------------------------------------------------------------------------
# Renderers accept both config types
# ---------------------------------------------------------------------------


def _fake_ir() -> IR:
    page = PageResult(page=0, markdown="# Hello\n\nWorld")
    return IR(metadata=Metadata(page_count=1), pages=[page])


def test_renderers_accept_router_config() -> None:
    ir = _fake_ir()
    cfg = RouterConfig()
    assert "Hello" in render_markdown(ir, config=cfg)
    assert render_json(ir, config=cfg).strip().startswith("{")
    chunk_line = render_chunks(ir, config=cfg).splitlines()[0]
    assert '"page": 0' in chunk_line


def test_renderers_accept_none_config() -> None:
    ir = _fake_ir()
    assert render_markdown(ir).strip() == "# Hello\n\nWorld"


# ---------------------------------------------------------------------------
# write_outputs
# ---------------------------------------------------------------------------


def test_write_outputs_writes_all_enabled(tmp_path: Path) -> None:
    written = write_outputs(_fake_ir(), tmp_path, "doc")
    assert set(written) == {"md", "chunks", "json"}
    assert written["md"] == tmp_path / "doc.md"
    assert written["chunks"] == tmp_path / "doc.chunks.jsonl"
    assert written["json"] == tmp_path / "doc.ir.json"
    assert written["md"].read_text(encoding="utf-8").strip()


def test_write_outputs_respects_enabled_subset(tmp_path: Path) -> None:
    cfg = RouterConfig()
    cfg.renderers.enabled = {"md"}
    written = write_outputs(_fake_ir(), tmp_path, "doc", config=cfg)
    assert set(written) == {"md"}
    assert list(tmp_path.iterdir()) == [tmp_path / "doc.md"]


def test_write_outputs_unknown_renderer_raises(tmp_path: Path) -> None:
    cfg = RouterConfig()
    cfg.renderers.enabled = {"bogus"}
    with pytest.raises(ConfigError, match="bogus"):
        write_outputs(_fake_ir(), tmp_path, "doc", config=cfg)
