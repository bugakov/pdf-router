"""Docling renderer tests (ADR-0009): IR -> DoclingDocument JSON.

The renderer is a *published format* target: its output must be a document
the Docling ecosystem can load without help. So the bar here is not "the
JSON looks right" but "``DoclingDocument.model_validate`` accepts it and
docling's own serializer reads it back".

Fast tier only (synthetic IRs, no OCR, no corpus): the renderer is a pure
function over the IR, so the interesting cases are the mapping rules —
markdown spine, block enrichment, OCR-page substitution, provenance
honesty — not the pipeline that produced the IR.

The whole module skips when the optional ``docling`` extra is missing.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

from pdf_router.config import RouterConfig
from pdf_router.ir import (
    BBox,
    FigureBlock,
    FormulaBlock,
    HeadingBlock,
    IR,
    Metadata,
    PageResult,
    TableBlock,
)
from pdf_router.renderers import docling as docling_renderer
from pdf_router.renderers.docling import DoclingUnavailableError

docling_core = pytest.importorskip("docling_core", reason="extra 'docling' is not installed")

from docling_core.types.doc.document import DoclingDocument  # noqa: E402


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _ir(
    markdown: str,
    *,
    blocks: list[object] | None = None,
    page_bbox: list[float] | None = None,
    images: list[str] | None = None,
    title: str | None = "Документ",
) -> IR:
    page = PageResult(
        page=0,
        markdown=markdown,
        page_bbox=page_bbox,
        blocks=list(blocks or []),
        images=list(images or []),
    )
    meta = Metadata(page_count=1, title=title, source_path="/tmp/sample.pdf")
    return IR(metadata=meta, pages=[page])


def _render(ir: IR) -> dict:
    out = docling_renderer.render(ir, config=RouterConfig().renderers)
    return json.loads(out)


def _validate(payload: dict) -> DoclingDocument:
    """The gate: docling itself must accept the document."""
    return DoclingDocument.model_validate(payload)


def _labels(payload: dict) -> list[str]:
    return [t["label"] for t in payload["texts"]]


def _resolve(payload: dict, ref: dict) -> dict:
    """Resolve a docling ``{"$ref": "#/texts/0"}`` pointer to its item."""
    assert "$ref" in ref, f"not a reference: {ref!r}"
    node: object = payload
    for part in ref["$ref"].lstrip("#/").split("/"):
        key: object = int(part) if part.isdigit() else part
        node = node[key]  # type: ignore[index]
    return node  # type: ignore[return-value]


# ---------------------------------------------------------------------------
# Document shape
# ---------------------------------------------------------------------------


def test_output_is_a_valid_docling_document() -> None:
    payload = _render(_ir("# Заголовок\n\nТекст абзаца."))
    doc = _validate(payload)
    assert doc.name == "Документ"
    assert payload["schema_name"] == "DoclingDocument"
    assert payload["version"]


def test_pages_are_1_indexed_and_carry_the_size() -> None:
    payload = _render(_ir("Текст.", page_bbox=[595.0, 842.0]))
    assert list(payload["pages"]) == ["1"]
    assert payload["pages"]["1"]["size"] == {"width": 595.0, "height": 842.0}


def test_document_name_falls_back_to_the_file_stem() -> None:
    ir = _ir("Текст.", title=None)
    assert _render(ir)["name"] == "sample"


def test_render_is_deterministic() -> None:
    ir = _ir("# Заголовок\n\nТекст.\n\n| A | B |\n|---|---|\n| 1 | 2 |")
    assert docling_renderer.render(ir, config=RouterConfig().renderers) == (
        docling_renderer.render(ir, config=RouterConfig().renderers)
    )


# ---------------------------------------------------------------------------
# The markdown spine
# ---------------------------------------------------------------------------


def test_markdown_becomes_text_items_in_reading_order() -> None:
    payload = _render(_ir("# Заголовок\n\nПервый абзац.\n\nВторой абзац."))
    assert _labels(payload) == ["section_header", "text", "text"]
    assert payload["texts"][0]["text"] == "Заголовок"
    assert payload["texts"][1]["text"] == "Первый абзац."
    assert payload["texts"][2]["text"] == "Второй абзац."


def test_consecutive_list_items_share_one_list_group() -> None:
    payload = _render(_ir("- пункт один\n- пункт два\n- пункт три"))
    doc = _validate(payload)
    groups = [g for g in payload["groups"] if g.get("name") is None or True]
    assert len(groups) == 1, "one list run must produce one list group"
    assert len(groups[0]["children"]) == 3
    assert doc.export_to_markdown().count("пункт") == 3


def test_ordered_and_unordered_lists_are_separate_groups() -> None:
    payload = _render(_ir("- буллет\n\n1. нумерованный"))
    assert len(payload["groups"]) == 2
    assert payload["texts"][0]["label"] == "list_item"
    assert payload["texts"][0]["enumerated"] is False
    assert payload["texts"][1]["label"] == "list_item"
    assert payload["texts"][1]["enumerated"] is True


# ---------------------------------------------------------------------------
# Blocks enrich the spine
# ---------------------------------------------------------------------------


def test_heading_block_level_wins_over_the_markdown_hashes() -> None:
    """A ``HeadingBlock`` knows the true level; the markdown only guesses."""
    block = HeadingBlock(level=3, text="Подраздел", bbox=BBox(10, 700, 200, 720))
    payload = _render(_ir("## Подраздел", blocks=[block]))
    assert payload["texts"][0]["label"] == "section_header"
    assert payload["texts"][0]["level"] == 3


def test_table_block_grid_becomes_a_docling_table() -> None:
    block = TableBlock(
        markdown="| A | B |\n|---|---|\n| 1 | 2 |",
        rows=[["A", "B"], ["1", "2"]],
        header_rows=1,
    )
    payload = _render(_ir(block.markdown, blocks=[block]))
    assert len(payload["tables"]) == 1
    table = payload["tables"][0]
    assert table["data"]["num_rows"] == 2
    assert table["data"]["num_cols"] == 2
    cells = table["data"]["table_cells"]
    assert len(cells) == 4
    assert cells[0]["text"] == "A" and cells[0]["column_header"] is True
    assert cells[2]["text"] == "1" and cells[2]["column_header"] is False


def test_pipe_table_without_a_block_still_becomes_a_table() -> None:
    """A native page's pipe grid is the truth even when no block matched."""
    payload = _render(_ir("| A | B |\n|---|---|\n| 1 | 2 |"))
    assert len(payload["tables"]) == 1
    assert payload["tables"][0]["data"]["num_rows"] == 2


def test_ocr_page_drops_the_pipe_twin_and_keeps_the_block_grid() -> None:
    """ADR-0005: on an OCR page the markdown pipe table is the *scrambled
    twin* of the TableBlock — the block wins, the twin is dropped."""
    block = TableBlock(
        markdown="| A | B |\n|---|---|",
        rows=[["Продукт", "Сумма"], ["Гвоздь", "12"]],
        header_rows=1,
        bbox=BBox(10, 400, 300, 500),
        source="ocr_table_lineless",
    )
    payload = _render(_ir("| A | B |\n|---|---|\n| 1 | 2 |", blocks=[block]))
    assert len(payload["tables"]) == 1
    cells = payload["tables"][0]["data"]["table_cells"]
    assert cells[0]["text"] == "Продукт"
    assert cells[2]["text"] == "Гвоздь"


def test_formula_block_becomes_a_formula_item() -> None:
    block = FormulaBlock(latex="x^2 + y^2 = z^2", bbox=BBox(10, 100, 200, 130))
    payload = _render(_ir("x^2 + y^2 = z^2", blocks=[block]))
    assert "formula" in _labels(payload)
    formula = next(t for t in payload["texts"] if t["label"] == "formula")
    assert formula["text"] == "x^2 + y^2 = z^2"


def test_figure_block_becomes_a_picture_with_its_sidecar() -> None:
    block = FigureBlock(bbox=BBox(10, 100, 300, 300), caption="Рис. 1", image_index=0)
    payload = _render(
        _ir(
            "![Рис. 1](images/p1_1.png)",
            blocks=[block],
            images=["images/p1_1.png"],
        )
    )
    assert len(payload["pictures"]) == 1
    picture = payload["pictures"][0]
    assert picture["image"]["uri"] == "images/p1_1.png"
    assert picture["image"]["mimetype"] == "image/png"
    assert picture["captions"], "the figure caption must ride along"
    caption = _resolve(payload, picture["captions"][0])
    assert caption["text"] == "Рис. 1"
    assert caption["label"] == "caption"


def test_figure_caption_without_an_image_stays_a_caption_item() -> None:
    block = FigureBlock(bbox=BBox(10, 100, 300, 300), caption="Рис. 2", image_index=None)
    payload = _render(_ir("Текст.", blocks=[block]))
    assert "caption" in _labels(payload)
    assert not payload["pictures"]


# ---------------------------------------------------------------------------
# Provenance honesty
# ---------------------------------------------------------------------------


def test_no_provenance_when_the_page_size_is_unknown() -> None:
    """docling clamps every bbox to the page size; an unknown size (0x0)
    would silently collapse the geometry to zero — so no prov is emitted."""
    block = HeadingBlock(level=1, text="Заголовок", bbox=BBox(10, 700, 200, 720))
    payload = _render(_ir("# Заголовок", blocks=[block]))
    assert payload["texts"][0].get("prov") in (None, [])


def test_provenance_is_emitted_when_the_geometry_is_known() -> None:
    block = HeadingBlock(level=1, text="Заголовок", bbox=BBox(10, 700, 200, 720))
    payload = _render(
        _ir("# Заголовок", blocks=[block], page_bbox=[595.0, 842.0])
    )
    prov = payload["texts"][0]["prov"][0]
    assert prov["page_no"] == 1
    # PDF points with y up -> docling's BOTTOMLEFT frame.
    assert prov["bbox"]["l"] == 10.0
    assert prov["bbox"]["r"] == 200.0
    assert prov["bbox"]["b"] == 700.0
    assert prov["bbox"]["t"] == 720.0


def test_ocr_block_bbox_never_becomes_provenance() -> None:
    """OCR bboxes are raster pixels (ADR-0005), not the page frame — they
    are hints, and promoting them to a prov would be a lie."""
    block = HeadingBlock(
        level=1,
        text="Заголовок",
        bbox=BBox(10, 700, 200, 720),
        source="ocr_box_height",
    )
    payload = _render(
        _ir("# Заголовок", blocks=[block], page_bbox=[595.0, 842.0])
    )
    assert payload["texts"][0].get("prov") in (None, [])


# ---------------------------------------------------------------------------
# The public facade
# ---------------------------------------------------------------------------


def test_api_render_docling_is_exported() -> None:
    import pdf_router

    ir = _ir("# Заголовок\n\nТекст.")
    payload = json.loads(pdf_router.render_docling(ir))
    _validate(payload)


def test_cli_render_docling_writes_the_file(
    p5_pdf: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """``--render docling`` is the CLI door to the interchange format."""
    from pdf_router import cli

    monkeypatch.chdir(tmp_path)
    out_dir = tmp_path / "out"
    code = cli.main(["--render", "docling", "--out", str(out_dir), str(p5_pdf)])
    assert code == 0
    path = out_dir / "p5.docling.json"
    assert path.is_file()
    payload = json.loads(path.read_text(encoding="utf-8"))
    _validate(payload)
    # p5 is a single-table drawing sheet: the table must survive the trip.
    assert payload["tables"], "p5 is a table page — the table must ride along"


def test_cli_default_run_does_not_write_the_docling_file(
    p5_pdf: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The extra is opt-in: the default run must not require docling-core."""
    from pdf_router import cli

    monkeypatch.chdir(tmp_path)
    out_dir = tmp_path / "out"
    code = cli.main(["--out", str(out_dir), str(p5_pdf)])
    assert code == 0
    assert (out_dir / "p5.md").is_file()
    assert not (out_dir / "p5.docling.json").exists()


def test_write_outputs_writes_the_docling_file(tmp_path: Path) -> None:
    from pdf_router import write_outputs

    cfg = RouterConfig()
    cfg.renderers.enabled = {"docling"}
    written = write_outputs(_ir("Текст."), tmp_path, "doc", config=cfg)
    assert set(written) == {"docling"}
    path = written["docling"]
    assert path == tmp_path / "doc.docling.json"
    _validate(json.loads(path.read_text(encoding="utf-8")))


def test_docling_is_not_in_the_default_renderer_set() -> None:
    """The extra is opt-in: a default run must not require docling-core."""
    cfg = RouterConfig()
    assert "docling" not in cfg.renderers.enabled
    assert {"md", "chunks", "json"} <= cfg.renderers.enabled


def test_cli_fails_fast_when_the_extra_is_missing(
    p5_pdf: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """A missing extra is a usage error — and it must surface *before* the
    document is processed: the pipeline may cost minutes of OCR, and the
    fix is one install line."""
    from pdf_router import cli

    def _boom() -> None:
        raise DoclingUnavailableError(
            "the docling renderer needs docling-core: install the extra"
        )

    monkeypatch.setattr(docling_renderer, "ensure_available", _boom)
    out_dir = tmp_path / "out"
    code = cli.main(["--render", "docling", "--out", str(out_dir), str(p5_pdf)])
    assert code == 1
    assert not out_dir.exists(), "nothing may be processed or written"
    assert "docling-core" in capsys.readouterr().err


def test_missing_extra_raises_a_typed_error(monkeypatch: pytest.MonkeyPatch) -> None:
    """Without the extra the failure must be a typed, actionable error —
    not an opaque ImportError from deep inside the renderer."""
    monkeypatch.setattr(docling_renderer, "_TYPES", None)
    for name in list(sys.modules):
        if name == "docling_core" or name.startswith("docling_core."):
            monkeypatch.delitem(sys.modules, name)
    # A None entry in sys.modules makes the import machinery raise
    # ImportError — the "the extra is not installed" case.
    monkeypatch.setitem(sys.modules, "docling_core", None)
    with pytest.raises(DoclingUnavailableError, match="docling-core"):
        docling_renderer.render(_ir("Текст."), config=RouterConfig().renderers)
