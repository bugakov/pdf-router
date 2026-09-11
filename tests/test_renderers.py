"""Ticket 09: renderers — pure functions over a fixed IR."""

import json

from pdf_router.config import RendererConfig
from pdf_router.ir import IR, Metadata, PageResult, SrcTool
from pdf_router.renderers import chunks as chunks_renderer
from pdf_router.renderers import json_renderer, md as md_renderer


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


# --- chunks --------------------------------------------------------------------


def test_chunks_jsonl_structure() -> None:
    out = chunks_renderer.render(make_ir(), config=RendererConfig())
    lines = [ln for ln in out.splitlines() if ln.strip()]
    assert lines, "expected at least one chunk"
    for ln in lines:
        chunk = json.loads(ln)
        assert set(chunk) == {
            "chunk_id",
            "text",
            "page",
            "section",
            "src_tool",
            "error",
            "images",
        }


def test_chunks_sections_and_pages() -> None:
    out = chunks_renderer.render(make_ir(), config=RendererConfig())
    chunks = [json.loads(ln) for ln in out.splitlines() if ln.strip()]
    sections = {c["section"] for c in chunks}
    assert sections == {"Section A", "Section B"}
    pages = {c["page"] for c in chunks}
    assert pages == {0, 1}
    tools = {c["src_tool"] for c in chunks}
    assert tools == {"pdf_inspector", "tesseract"}


def test_chunks_carry_page_error() -> None:
    """Reliability signal for RAG: chunks from a failed page carry
    ``page.error`` so an ingest pipeline can drop them without joining
    against the IR; healthy pages carry ``null``."""
    ir = make_ir()
    ir.pages[1].error = "rapidocr_failed: engine exploded"
    out = chunks_renderer.render(ir, config=RendererConfig())
    chunks = [json.loads(ln) for ln in out.splitlines() if ln.strip()]
    assert all(c["error"] is None for c in chunks if c["page"] == 0)
    failed = [c for c in chunks if c["page"] == 1]
    assert failed
    assert all(c["error"] == "rapidocr_failed: engine exploded" for c in failed)


def test_chunks_long_block_split_with_overlap() -> None:
    long_text = "word " * 2000  # ~10k chars ≈ 2500 tokens > 512
    ir = IR(
        pages=[PageResult(page=0, markdown=f"## Big\n\n{long_text}", src_tool=SrcTool.PDF_INSPECTOR)]
    )
    cfg = RendererConfig()
    cfg.chunks.max_tokens = 100
    cfg.chunks.overlap_tokens = 20
    out = chunks_renderer.render(ir, config=cfg)
    chunks = [json.loads(ln) for ln in out.splitlines() if ln.strip()]
    assert len(chunks) > 1
    for c in chunks:
        assert len(c["text"]) <= 100 * 4 + 10  # within budget (strip slack)


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
    p0_chunk = next(json.loads(ln) for ln in lines if json.loads(ln)["page"] == 0)
    p1_chunk = next(json.loads(ln) for ln in lines if json.loads(ln)["page"] == 1)
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
