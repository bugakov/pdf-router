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

import shutil
import subprocess
import sys
from pathlib import Path
import logging
import os

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
    UnsupportedFormatError,
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
# --- wire_pdf_inspector_env -------------------------------------------------


def test_wire_noop_for_other_engines(tmp_path) -> None:
    """Default config (rapidocr) → wire does nothing, env untouched."""
    before = dict(os.environ)
    pdf_router.wire_pdf_inspector_env(RouterConfig())
    assert dict(os.environ) == before


def test_wire_sets_env_from_section(tmp_path) -> None:
    """Selected pdf_inspector_ocr → both paths land in env."""
    pdfium = tmp_path / "libpdfium.so"
    ort = tmp_path / "libonnxruntime.so"
    pdfium.write_bytes(b"x")
    ort.write_bytes(b"x")
    cfg = RouterConfig()
    cfg.routing.ocr_fallback_engine = "pdf_inspector_ocr"
    cfg.ocr_engines["pdf_inspector_ocr"] = {
        "pdfium_lib_path": str(pdfium),
        "ort_dylib_path": str(ort),
    }
    monkeypatch = pytest.MonkeyPatch()
    try:
        monkeypatch.delenv("PDFIUM_LIB_PATH", raising=False)
        monkeypatch.delenv("ORT_DYLIB_PATH", raising=False)
        pdf_router.wire_pdf_inspector_env(cfg)
        assert os.environ["PDFIUM_LIB_PATH"] == str(pdfium)
        assert os.environ["ORT_DYLIB_PATH"] == str(ort)
    finally:
        monkeypatch.undo()


def test_wire_overrides_mismatched_env_with_warning(tmp_path, caplog) -> None:
    """Stale env pointing elsewhere → overwritten + warning (config wins)."""
    pdfium = tmp_path / "libpdfium.so"
    ort = tmp_path / "libonnxruntime.so"
    pdfium.write_bytes(b"x")
    ort.write_bytes(b"x")
    cfg = RouterConfig()
    cfg.routing.ocr_fallback_engine = "pdf_inspector_ocr"
    cfg.ocr_engines["pdf_inspector_ocr"] = {
        "pdfium_lib_path": str(pdfium),
        "ort_dylib_path": str(ort),
    }
    monkeypatch = pytest.MonkeyPatch()
    try:
        monkeypatch.setenv("PDFIUM_LIB_PATH", "/stale/libpdfium.so")
        monkeypatch.setenv("ORT_DYLIB_PATH", "/stale/libonnxruntime.so")
        with caplog.at_level(logging.WARNING, logger="pdf_router"):
            pdf_router.wire_pdf_inspector_env(cfg)
        assert os.environ["PDFIUM_LIB_PATH"] == str(pdfium)
        assert os.environ["ORT_DYLIB_PATH"] == str(ort)
        assert any("overriding" in rec.message for rec in caplog.records)
    finally:
        monkeypatch.undo()


def test_wire_keeps_equal_env_silently(tmp_path, caplog) -> None:
    """Env already matching the config → no warning, no rewrite."""
    pdfium = tmp_path / "libpdfium.so"
    ort = tmp_path / "libonnxruntime.so"
    pdfium.write_bytes(b"x")
    ort.write_bytes(b"x")
    cfg = RouterConfig()
    cfg.routing.ocr_fallback_engine = "pdf_inspector_ocr"
    cfg.ocr_engines["pdf_inspector_ocr"] = {
        "pdfium_lib_path": str(pdfium),
        "ort_dylib_path": str(ort),
    }
    monkeypatch = pytest.MonkeyPatch()
    try:
        monkeypatch.setenv("PDFIUM_LIB_PATH", str(pdfium))
        monkeypatch.setenv("ORT_DYLIB_PATH", str(ort))
        with caplog.at_level(logging.WARNING, logger="pdf_router"):
            pdf_router.wire_pdf_inspector_env(cfg)
        assert not any("overriding" in rec.message for rec in caplog.records)
    finally:
        monkeypatch.undo()


# --- load_config / defaults -------------------------------------------------
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


def test_entry_points_accept_all_config_shapes(p5_pdf: Path, tmp_path: Path) -> None:
    """``None``, TOML path (str and Path) and RouterConfig all work for the
    same call — and produce the same result."""
    toml = tmp_path / "router.toml"
    toml.write_text("", encoding="utf-8")  # empty file → built-in defaults
    ir1, rep1 = process_pdf(p5_pdf, config=None)
    ir2, rep2 = process_pdf(p5_pdf, config=load_config())
    ir3, rep3 = process_pdf(p5_pdf, config=str(toml))
    ir4, rep4 = process_pdf(p5_pdf, config=toml)
    expected = ir1.pages[0].markdown
    assert ir2.pages[0].markdown == expected
    assert ir3.pages[0].markdown == expected
    assert ir4.pages[0].markdown == expected
    assert rep1.pages_total == rep2.pages_total == rep3.pages_total == rep4.pages_total == 1


def test_convert_accepts_toml_path(p5_pdf: Path, tmp_path: Path) -> None:
    """REGRESSION (embedding candidate #1): ``convert(config="router.toml")``
    used to raise ConfigError from the *renderer* — the raw str/Path leaked
    past _coerce_config after the whole pipeline had already run. The TOML
    path must be coerced once, up front."""
    toml = tmp_path / "router.toml"
    toml.write_text("", encoding="utf-8")
    by_str = convert(p5_pdf, config=str(toml))
    by_path = convert(p5_pdf, config=toml)
    assert by_str == by_path
    assert by_str.strip()


def test_convert_missing_config_path_raises(p5_pdf: Path, tmp_path: Path) -> None:
    """A typo'd TOML path fails fast — before any page is processed."""
    with pytest.raises(ConfigError, match="not found"):
        convert(p5_pdf, config=tmp_path / "nope.toml")


# ---------------------------------------------------------------------------
# Format gate (multi-format candidates #1 + #4)
# ---------------------------------------------------------------------------


def test_unsupported_format_raises_typed_error_at_boundary(docx_path: Path) -> None:
    """REGRESSION (multi-format #1+#4): a docx used to die with an untyped
    ValueError from the Rust pdfium boundary deep inside the pipeline. The
    format gate now rejects it at the boundary with UnsupportedFormatError
    — before any page work — for both entry points."""
    with pytest.raises(UnsupportedFormatError, match="DOCX"):
        convert(docx_path)
    with pytest.raises(UnsupportedFormatError, match="DOCX"):
        process_pdf(docx_path)


def test_format_detected_by_content_not_extension(
    p5_pdf: Path, docx_path: Path, tmp_path: Path
) -> None:
    """Magic bytes win: a PDF renamed to .docx still converts; a docx
    renamed to .pdf is rejected (never reaches the pipeline)."""
    disguised_pdf = tmp_path / "scan.docx"
    shutil.copyfile(p5_pdf, disguised_pdf)
    assert convert(disguised_pdf).strip()

    disguised_docx = tmp_path / "report.pdf"
    shutil.copyfile(docx_path, disguised_docx)
    with pytest.raises(UnsupportedFormatError, match="DOCX"):
        convert(disguised_docx)


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


# ---------------------------------------------------------------------------
# Output policy: pure by default, output_dir= is the only IO switch
# ---------------------------------------------------------------------------


def test_process_pdf_is_pure_by_default(
    p5_pdf: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """No output_dir → nothing on disk (README contract: "no files written").

    REGRESSION (embedding candidate #2): the pipeline used to unconditionally
    mkdir config.renderers.output_dir (default ./out), so even a pure
    convert() left an ./out/ sidecar directory behind."""
    monkeypatch.chdir(tmp_path)
    ir, report = process_pdf(p5_pdf)
    assert report.pages_total == 1
    assert list(tmp_path.iterdir()) == []
    assert all(p.images == [] for p in ir.pages)


def test_convert_is_pure_even_when_config_sets_output_dir(
    p5_pdf: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """convert() never writes — even with a TOML that sets output_dir.
    Sidecar images are process_pdf(…, output_dir=…) territory; the config
    key is the CLI's default, never read by the library core."""
    (tmp_path / "router.toml").write_text(
        '[renderers]\noutput_dir = "sidecars"\n', encoding="utf-8"
    )
    monkeypatch.chdir(tmp_path)
    md = convert(p5_pdf, config="router.toml")
    assert md.strip()
    assert not (tmp_path / "sidecars").exists()
    assert sorted(p.name for p in tmp_path.iterdir()) == ["router.toml"]


def test_process_pdf_output_dir_enables_sidecar_dir(p5_pdf: Path, tmp_path: Path) -> None:
    """output_dir= turns the IO on: the sidecar root is created (p5 has no
    raster images, so it may stay empty — the seam under test is the switch)."""
    out = tmp_path / "side"
    ir, _ = process_pdf(p5_pdf, output_dir=out)
    assert out.is_dir()
    assert ir.pages[0].images == []


def test_strict_raises_when_pages_failed(p5_pdf: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """strict=True surfaces per-page failures instead of swallowing them."""
    import pdf_router.api as api

    real_ir, report = process_pdf(p5_pdf)
    report.pages_failed = 1  # simulate a partial failure as core would record it

    def fake_pipeline(
        path: str, cfg: RouterConfig, *, output_dir: Path | None = None
    ) -> tuple[IR, ProcessingReport]:
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
