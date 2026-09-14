"""Ticket 09: renderers — pure functions over a fixed IR.

The chunks renderer follows the map v0.7 contract (ADR-0006, envelope v2);
invariants specific to it live in tests/test_chunks_rag.py. This file pins
only the md/json renderers and the one chunks shape every renderer test
needs (envelope key set)."""

import json

from pdf_router.config import RendererConfig
from pdf_router.ir import IR, Metadata, PageResult, SrcTool
from pdf_router.renderers import json_renderer, md as md_renderer
from pdf_router.renderers import chunks as chunks_renderer


def make_ir() -> IR:
    return IR(
        metadata=Metadata(pdf_type="text_based", page_count=2),
        pages=[
            PageResult(
                page=0,
                markdown="## Section A\n\nfirst paragraph\n\nsecond paragraph",
                src_tool=SrcTool.PDF_INSPECTOR,
            ),
            PageResult(
                page=1,
                markdown="### Section B\n\npage two body",
                src_tool=SrcTool.TESSERACT,
            ),
        ],
    )


# --- md ------------------------------------------------------------------------


def test_md_concatenation() -> None:
    out = md_renderer.render(make_ir(), config=RendererConfig())
    assert "first paragraph" in out
    assert "page two body" in out
    assert "<!-- page" not in out  # markers off by default


def test_md_page_markers() -> None:
    cfg = RendererConfig(page_markers=True)
    out = md_renderer.render(make_ir(), config=cfg)
    assert "<!-- page 1 -->" in out
    assert "<!-- page 2 -->" in out  # 1-indexed


# --- chunks (shape only; full contract: test_chunks_rag.py) --------------------


def test_chunks_envelope_v2() -> None:
    out = chunks_renderer.render(make_ir(), config=RendererConfig())
    lines = [ln for ln in out.splitlines() if ln.strip()]
    assert lines, "expected at least one chunk"
    for ln in lines:
        chunk = json.loads(ln)
        assert {"chunk_id", "chunk_version", "type", "embed", "markdown", "metadata", "images"} <= set(chunk)
        assert chunk["chunk_version"] == 2


# --- json ----------------------------------------------------------------------


def test_json_round_trip() -> None:
    out = json_renderer.render(make_ir(), config=RendererConfig())
    d = json.loads(out)
    assert d["metadata"]["pdf_type"] == "text_based"
    assert len(d["pages"]) == 2
    assert "page_bbox" not in d["pages"][0]


def test_json_page_bbox_toggle() -> None:
    ir = make_ir()
    ir.pages[0].page_bbox = [595.0, 842.0]
    cfg = RendererConfig(json_include_page_bbox=True)
    out = json_renderer.render(ir, config=cfg)
    d = json.loads(out)
    assert d["pages"][0]["page_bbox"] == [595.0, 842.0]


# --- RT-13: images in chunks/json -------------------------------------------


def test_chunks_include_images() -> None:
    """v0.3: each chunk carries page.images (relative paths)."""
    ir = make_ir()
    ir.pages[0].images = ["abc123/page0001_photo_00.png", "abc123/page0001_photo_01.png"]
    ir.pages[1].images = []
    out = chunks_renderer.render(ir, config=RendererConfig())
    lines = [ln for ln in out.splitlines() if ln.strip()]
    assert len(lines) >= 1
    p0_chunk = next(json.loads(ln) for ln in lines if json.loads(ln)["metadata"]["page"] == 0)
    p1_chunk = next(json.loads(ln) for ln in lines if json.loads(ln)["metadata"]["page"] == 1)
    assert p0_chunk["images"] == ["abc123/page0001_photo_00.png", "abc123/page0001_photo_01.png"]
    assert p1_chunk["images"] == []


def test_json_round_trip_images() -> None:
    """images survive to_dict → from_dict round-trip."""
    ir = make_ir()
    ir.pages[0].images = ["abc/page0001_photo_00.png"]
    from pdf_router.ir import from_dict, to_dict
    d = to_dict(ir)
    assert d["pages"][0]["images"] == ["abc/page0001_photo_00.png"]
    ir2 = from_dict(d)
    assert ir2.pages[0].images == ["abc/page0001_photo_00.png"]
