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
        assert set(chunk) == {"chunk_id", "text", "page", "section", "src_tool"}


def test_chunks_sections_and_pages() -> None:
    out = chunks_renderer.render(make_ir(), config=RendererConfig())
    chunks = [json.loads(ln) for ln in out.splitlines() if ln.strip()]
    sections = {c["section"] for c in chunks}
    assert sections == {"Section A", "Section B"}
    pages = {c["page"] for c in chunks}
    assert pages == {0, 1}
    tools = {c["src_tool"] for c in chunks}
    assert tools == {"pdf_inspector", "tesseract"}


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
