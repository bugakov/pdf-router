"""djvu ingest end-to-end (ADR-0004).

Pins the decisions the wayfinder map locked (TKT-DJV-01..12):

- the djvu inspector: one PageResult per djvu page, hidden text layer
  becomes markdown with needs_ocr=False, layerless pages need OCR;
- IR 0.4: SrcTool.DJVU_TEXT_LAYER round-trips; metadata is honest
  (pdf_type="scanned", source_format="djvu", pages_needing_ocr excludes
  layer pages);
- the raster seam: pdf -> pymupdf, djvu -> ddjvu, PNG/numpy/single-PDF
  consumers all format-aware; ddjvu never upscales above stored dpi;
- core gating: text pass runs for djvu, image pass never does (a djvu
  page IS the page-background scan); junky layers fall back to OCR via
  low_text_quality; magic bytes beat extensions;
- CLI --info prints the djvu summary, exit 0.

Fixtures are committed 2-page djvus (tests/fixtures/djvu/, built once
with c44+djvm); the real-corpus slow test skips without the corpus.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from pdf_router import core
from pdf_router.adapters.djvu import (
    DjvuError,
    djvu_page_count,
    djvu_page_texts,
    extract_ir,
    render_page_rgb_djvu,
)
from pdf_router.config import RouterConfig
from pdf_router.formats import Format, detect_format, route
from pdf_router.ir import IR_VERSION, SrcTool, from_dict, to_dict
from pdf_router.ocr import OcrError, _ocr_pdf_inspector
from pdf_router.rasterize import (
    RasterizeError,
    page_raster_array,
    page_single_pdf,
    rasterize_page,
)

CORPUS_DIR = Path("/home/scanum/vbrdb/orchestrator/uploads")


def _fake_ocr(calls: list, text: str = "ocr text", engine: str = "rapidocr"):
    # IR 0.5 (ADR-0005): ocr_page returns (markdown, engine, blocks).
    def ocr_page(pdf_path, page_idx, config):
        calls.append((pdf_path, page_idx))
        return text, engine, []

    return ocr_page


# ---------------------------------------------------------------------------
# Inspector: pages, hidden text layer, metadata
# ---------------------------------------------------------------------------


def test_inspector_plain_all_pages_need_ocr(plain_djvu: Path) -> None:
    ir = extract_ir(str(plain_djvu))
    assert ir.metadata.page_count == 2
    assert ir.metadata.source_format == "djvu"
    assert ir.metadata.pdf_type == "scanned"
    assert ir.metadata.confidence == 1.0
    assert [p.page for p in ir.pages] == [0, 1]
    assert all(p.needs_ocr for p in ir.pages)
    assert all(p.markdown == "" for p in ir.pages)
    assert all(p.src_tool is SrcTool.DJVU_ADAPTER for p in ir.pages)
    assert ir.metadata.pages_needing_ocr == [1, 2]
    assert [e.reasons for e in ir.metadata.ocr_reasons_by_page] == [
        ["inspector_needs_ocr"],
        ["inspector_needs_ocr"],
    ]


def test_inspector_text_layer_page_is_native(text_layer_djvu: Path) -> None:
    ir = extract_ir(str(text_layer_djvu))
    p0, p1 = ir.pages
    assert p0.markdown == "Hello"
    assert p0.needs_ocr is False
    assert p0.src_tool is SrcTool.DJVU_TEXT_LAYER
    assert p1.markdown == ""
    assert p1.needs_ocr is True
    assert p1.src_tool is SrcTool.DJVU_ADAPTER  # layerless, honest tag
    # pages_needing_ocr excludes the layer page (it does not need OCR).
    assert ir.metadata.pages_needing_ocr == [2]
    assert all(p.blocks == [] for p in ir.pages)  # no structural data
    assert all(p.images == [] for p in ir.pages)  # no sidecar images


def test_page_count_and_texts_helpers(text_layer_djvu: Path) -> None:
    assert djvu_page_count(str(text_layer_djvu)) == 2
    texts = djvu_page_texts(str(text_layer_djvu), 2)
    assert texts == ["Hello", ""]


def test_inspector_rejects_truncated_djvu(tmp_path: Path) -> None:
    """Magic bytes pass the gate, but djvused fails -> document-level error."""
    p = tmp_path / "broken.djvu"
    p.write_bytes(b"AT&TFORM\x00\x00\x00\x14DJVMDJVM" + b"\x00" * 32)
    with pytest.raises(DjvuError):
        extract_ir(str(p))


# ---------------------------------------------------------------------------
# IR 0.5: round-trip and honest metadata
# ---------------------------------------------------------------------------


def test_ir04_roundtrip_preserves_djvu_provenance(text_layer_djvu: Path) -> None:

    ir = extract_ir(str(text_layer_djvu))
    d = to_dict(ir)
    assert d["ir_version"] == IR_VERSION == "0.5"
    assert d["pages"][0]["src_tool"] == "djvu_text_layer"
    ir2 = from_dict(d)
    assert ir2.pages[0].src_tool is SrcTool.DJVU_TEXT_LAYER
    assert ir2.metadata.source_format == "djvu"


def test_ir04_metadata_counts_are_consistent(text_layer_djvu: Path) -> None:
    """pages_needing_ocr (1-indexed) excludes layer pages; the layer page
    lands in pages_native after the text pass (see core tests below)."""
    ir = extract_ir(str(text_layer_djvu))
    assert ir.metadata.pages_needing_ocr == [2]
    assert 1 not in ir.metadata.pages_needing_ocr


# ---------------------------------------------------------------------------
# Raster seam: ddjvu render, PNG/numpy/single-PDF consumers, scale cap
# ---------------------------------------------------------------------------


def test_render_page_rgb_dims_and_cap(plain_djvu: Path) -> None:
    """Fixture pages are 200x300 @300 dpi stored; dpi=150 downsamples,
    dpi=600 must NOT upscale above stored (min(dpi, stored_dpi))."""
    w150, h150, rgb = render_page_rgb_djvu(str(plain_djvu), 0, dpi=150)
    assert (w150, h150) == (100, 150)
    assert len(rgb) == w150 * h150 * 3
    w300, h300, _ = render_page_rgb_djvu(str(plain_djvu), 0, dpi=300)
    w600, h600, _ = render_page_rgb_djvu(str(plain_djvu), 0, dpi=600)
    assert (w600, h600) == (w300, h300)  # capped at native 300 dpi


def test_rasterize_page_png_format_aware(plain_djvu: Path, p5_pdf: Path) -> None:
    png = rasterize_page(str(plain_djvu), 1, dpi=150)
    assert png.startswith(b"\x89PNG\r\n\x1a\n")
    png_pdf = rasterize_page(str(p5_pdf), 0, dpi=150)  # pdf path unchanged
    assert png_pdf.startswith(b"\x89PNG\r\n\x1a\n")


def test_page_raster_array_shape(text_layer_djvu: Path) -> None:
    arr = page_raster_array(str(text_layer_djvu), 0, dpi=150)
    assert arr.shape == (150, 100, 3)
    assert arr.dtype.name == "uint8"


def test_page_single_pdf_wraps_djvu_raster(text_layer_djvu: Path) -> None:
    import pymupdf

    pdf_bytes = page_single_pdf(str(text_layer_djvu), 1)
    doc = pymupdf.open(stream=pdf_bytes, filetype="pdf")
    try:
        assert doc.page_count == 1
        # The wrapped page carries a real image (not a blank page).
        assert doc[0].get_images() != []
    finally:
        doc.close()


def test_seam_out_of_range_page_raises(plain_djvu: Path) -> None:
    with pytest.raises((RasterizeError, DjvuError)):
        render_page_rgb_djvu(str(plain_djvu), 99, dpi=150)


def test_ddjvu_render_is_deterministic(plain_djvu: Path) -> None:
    a = render_page_rgb_djvu(str(plain_djvu), 0, dpi=150)
    b = render_page_rgb_djvu(str(plain_djvu), 0, dpi=150)
    assert a == b


# ---------------------------------------------------------------------------
# OCR engines over the seam
# ---------------------------------------------------------------------------


def test_pdf_inspector_ocr_refuses_djvu(text_layer_djvu: Path) -> None:
    """The Rust engine is PDF-only by nature — typed refusal, not a crash."""
    with pytest.raises(OcrError, match="PDF-only"):
        _ocr_pdf_inspector(str(text_layer_djvu), 0, {})


def test_rapidocr_path_flows_through_seam(
    monkeypatch: pytest.MonkeyPatch, text_layer_djvu: Path
) -> None:
    """rapidocr must receive a numpy array rendered from the djvu (seam),
    not open the djvu with pymupdf (which cannot)."""
    import numpy as np

    import pdf_router.ocr as ocr_mod

    seen: dict = {}

    def fake_array(path, page_idx, *, dpi):
        seen["args"] = (path, page_idx, dpi)
        return np.zeros((4, 4, 3), dtype=np.uint8)

    class FakeEngine:
        def __call__(self, img):
            assert img.shape == (4, 4, 3)
            return type("R", (), {"txts": ["rendered line"]})()

    monkeypatch.setattr(ocr_mod, "page_raster_array", fake_array)
    monkeypatch.setattr(ocr_mod, "_rapidocr_get_engine", lambda cfg: FakeEngine())
    md, engine, blocks = ocr_mod.ocr_page(str(text_layer_djvu), 1, RouterConfig())
    assert md == "rendered line"
    assert engine == "rapidocr"
    # FakeEngine carries no box geometry -> no triples -> no structure pass:
    # the page degrades to flat text with zero blocks (ADR-0005 contract).
    assert blocks == []
    assert seen["args"][1] == 1


# ---------------------------------------------------------------------------
# Core gating: text pass runs, image pass does not, counts are honest
# ---------------------------------------------------------------------------


def test_core_layerless_djvu_ocrs_all_pages(
    monkeypatch: pytest.MonkeyPatch, plain_djvu: Path, tmp_path: Path
) -> None:
    calls: list = []
    monkeypatch.setattr(core, "ocr_page", _fake_ocr(calls))
    ir, report = core.process_pdf(str(plain_djvu), RouterConfig())
    assert len(calls) == 2
    assert report.pages_total == 2
    assert report.pages_ocr == 2
    assert report.pages_native == 0
    assert report.pages_failed == 0
    assert all(p.src_tool is SrcTool.RAPIDOCR for p in ir.pages)
    assert all(p.markdown == "ocr text" for p in ir.pages)
    assert all(p.images == [] for p in ir.pages)
    # Pure run: nothing written anywhere (tmp_path unused by the pipeline).
    assert list(tmp_path.iterdir()) == []


def test_core_layer_page_skips_ocr_but_layerless_runs(
    monkeypatch: pytest.MonkeyPatch, text_layer_djvu: Path
) -> None:
    """The layer page keeps its text without OCR; the layerless page
    goes through the OCR pass (TKT-DJV-03 semantics)."""
    calls: list = []
    monkeypatch.setattr(core, "ocr_page", _fake_ocr(calls, engine="rapidocr"))
    ir, report = core.process_pdf(str(text_layer_djvu), RouterConfig())
    assert calls == [(str(text_layer_djvu), 1)]  # only the layerless page
    p0, p1 = ir.pages
    assert p0.markdown == "Hello"
    assert p0.src_tool is SrcTool.DJVU_TEXT_LAYER
    assert p0.ocr_reason is None
    assert p1.src_tool is SrcTool.RAPIDOCR
    assert report.pages_native == 1
    assert report.pages_ocr == 1
    assert report.pages_failed == 0


def test_core_junky_layer_falls_back_to_ocr(
    monkeypatch: pytest.MonkeyPatch, text_layer_djvu: Path
) -> None:
    """A layer that fails the quality gate is not trusted: low_text_quality
    -> OCR fallback overwrites markdown and src_tool (TKT-DJV-03/04)."""
    calls: list = []
    monkeypatch.setattr(core, "ocr_page", _fake_ocr(calls))
    monkeypatch.setattr(core, "looks_like_text", lambda *a, **k: False)
    ir, report = core.process_pdf(str(text_layer_djvu), RouterConfig())
    p0, _p1 = ir.pages
    assert p0.ocr_reason == "low_text_quality"
    assert p0.markdown == "ocr text"
    assert p0.src_tool is SrcTool.RAPIDOCR
    assert p0.error is None
    # Counter semantics match pdf: a page whose (layer) text failed the
    # quality gate and got re-OCR'd still counts as native — same as a
    # pdf page with bad native text. Provenance lives in src_tool.
    assert report.pages_native == 1
    assert report.pages_ocr == 1


def test_core_ocr_failure_marks_page_error(
    monkeypatch: pytest.MonkeyPatch, plain_djvu: Path
) -> None:
    def failing(pdf_path, page_idx, config):
        raise OcrError("boom")

    monkeypatch.setattr(core, "ocr_page", failing)
    ir, report = core.process_pdf(str(plain_djvu), RouterConfig())
    assert report.pages_failed == 2
    assert all(p.error and "rapidocr_failed" in p.error for p in ir.pages)


def test_core_output_dir_stays_clean_for_djvu(
    monkeypatch: pytest.MonkeyPatch, plain_djvu: Path, tmp_path: Path
) -> None:
    """Even with output_dir set, djvu writes nothing to images/ (TKT-DJV-06):
    the image pass does not run at all."""
    calls: list = []
    monkeypatch.setattr(core, "ocr_page", _fake_ocr(calls))
    out = tmp_path / "out"
    ir, report = core.process_pdf(str(plain_djvu), RouterConfig(), output_dir=out)
    assert out.exists()  # created by the pipeline contract
    # The image pass never ran: no sidecar subdirectories, nothing listed.
    assert not any(ch.is_dir() for ch in out.iterdir())
    assert all(p.images == [] for p in ir.pages)
    assert report.pages_image_failed == 0


def test_core_magic_bytes_beat_extension(
    monkeypatch: pytest.MonkeyPatch, plain_djvu: Path, tmp_path: Path
) -> None:
    """A djvu renamed to .pdf routes as djvu (and vice versa is detected)."""
    disguised = tmp_path / "book.pdf"
    disguised.write_bytes(plain_djvu.read_bytes())
    assert detect_format(disguised) is Format.DJVU
    calls: list = []
    monkeypatch.setattr(core, "ocr_page", _fake_ocr(calls))
    ir, _report = core.process_pdf(str(disguised), RouterConfig())
    assert ir.metadata.source_format == "djvu"
    assert len(calls) == 2


def test_core_djvu_failure_is_document_level(tmp_path: Path) -> None:
    """djvused failing on a corrupt document raises (exit 3 path), it is
    not a per-page error — the inspector never produced pages."""
    p = tmp_path / "corrupt.djvu"
    p.write_bytes(b"AT&TFORM\x00\x00\x00\x14DJVMDJVM" + b"\x00" * 32)
    with pytest.raises(DjvuError):
        core.process_pdf(str(p), RouterConfig())


# ---------------------------------------------------------------------------
# CLI --info (one door)
# ---------------------------------------------------------------------------


def test_cli_info_djvu(text_layer_djvu: Path, capsys: pytest.CaptureFixture) -> None:
    from pdf_router.cli import main

    rc = main(["--info", str(text_layer_djvu)])
    assert rc == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["source_format"] == "djvu"
    assert payload["pdf_type"] == "scanned"
    assert payload["page_count"] == 2
    assert payload["pages_with_text_layer"] == [1]
    assert payload["pages_needing_ocr"] == [2]


def test_route_djvu_end_to_end(text_layer_djvu: Path) -> None:
    """The gate hands djvu to its inspector — the old
    'does not support it yet' refusal is gone (ADR-0004)."""
    inspector = route(text_layer_djvu)
    ir = inspector(str(text_layer_djvu))
    assert ir.metadata.page_count == 2


# ---------------------------------------------------------------------------
# Slow tier: real OCR on a corpus page (deselect with -m 'not slow')
# ---------------------------------------------------------------------------


def _small_anchor() -> Path | None:
    p = CORPUS_DIR / "4139_from_s3.djvu"
    return p if p.exists() else None


def _text_anchor() -> Path | None:
    p = CORPUS_DIR / "._._-__d2c86e35.djvu"
    return p if p.exists() else None


@pytest.mark.slow
def test_real_ocr_on_corpus_anchor() -> None:
    """Real rapidocr pass on page 1 of the 25-page corpus anchor.
    Deterministic pipeline: two runs must produce identical markdown."""
    anchor = _small_anchor()
    if anchor is None:
        pytest.skip("corpus anchor not available")
    ocr = __import__("pdf_router.ocr", fromlist=["ocr_page"]).ocr_page
    md1, engine1, _blocks1 = ocr(str(anchor), 0, RouterConfig())
    md2, engine2, _ = ocr(str(anchor), 0, RouterConfig())
    assert engine1 == engine2 == "rapidocr"
    assert md1.strip()
    assert md1 == md2


@pytest.mark.slow
def test_real_text_layer_corpus_anchor() -> None:
    """The corpus file with the richest hidden text layer: layered pages
    answer from DJVU_TEXT_LAYER (no OCR), the rest need OCR."""
    from pdf_router.adapters.djvu import extract_ir
    from pdf_router.ir import SrcTool

    anchor = _text_anchor()
    if anchor is None:
        pytest.skip("corpus text anchor not available")
    ir = extract_ir(str(anchor))
    layered = [p for p in ir.pages if p.src_tool is SrcTool.DJVU_TEXT_LAYER]
    assert layered, "anchor must carry a text layer"
    for p in layered:
        assert p.markdown.strip(), f"page {p.page}: layer text must be non-empty"
        assert not p.needs_ocr
    assert ir.metadata.page_count - len(ir.metadata.pages_needing_ocr) == len(layered)
