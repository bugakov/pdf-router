"""Tests for the native zvec target renderer (ADR-0010)."""

from __future__ import annotations

import json
from pathlib import Path


from pdf_router import (
    IR,
    Metadata,
    PageResult,
    RendererConfig,
    RouterConfig,
    SrcTool,
    ZvecConfig,
    iter_zvec_records,
    render_zvec,
    write_outputs,
)
from pdf_router.ir import BBox, FigureBlock, HeadingBlock, TableBlock


def _make_sample_ir() -> IR:
    # Page 1: Heading + Table with numeric data and bbox
    table = TableBlock(
        markdown="| Таблица 1. Расход реагентов |\n| --- |\n| Реагент | Доза мг/л | Расход кг/сут |\n| Коагулянт | 15.5 | 120 |\n| Флокулянт | 2.5 | 20 |",
        rows=[
            ["Таблица 1. Расход реагентов", "", ""],
            ["Реагент", "Доза мг/л", "Расход кг/сут"],
            ["Коагулянт", "15.5", "120"],
            ["Флокулянт", "2.5", "20"],
        ],
        header_rows=1,
        bbox=BBox(x0=50.0, y0=100.0, x1=500.0, y1=300.0),
    )
    p1 = PageResult(
        page=0,
        markdown="## Раздел 4. Водоснабжение\n\nВводная часть раздела.",
        blocks=[
            HeadingBlock(level=2, text="Раздел 4. Водоснабжение"),
            table,
        ],
        src_tool=SrcTool.PDF_INSPECTOR,
    )

    # Page 2: Subheading + Text
    p2 = PageResult(
        page=1,
        markdown="### Насосная станция\n\nТекст описания насосной станции.",
        blocks=[
            HeadingBlock(level=3, text="Насосная станция"),
        ],
        src_tool=SrcTool.PDF_INSPECTOR,
    )

    return IR(
        metadata=Metadata(
            page_count=2,
            source_path="/data/projects/kns_project.pdf",
            title="Том 4. КНС и сети",
            source_format="pdf",
        ),
        pages=[p1, p2],
    )


def test_zvec_render_contract() -> None:
    ir = _make_sample_ir()
    cfg = RendererConfig(
        zvec=ZvecConfig(project_uid="proj-123", doc_uid="doc-456")
    )
    output = render_zvec(ir, config=cfg)
    assert output.endswith("\n")

    lines = [json.loads(line) for line in output.splitlines()]
    assert len(lines) >= 3  # table + text p1 + text p2

    for record in lines:
        assert "id" in record
        assert "embed_text" in record
        assert "fields" in record
        assert "metadata" in record

        f = record["fields"]
        assert f["project_uid"] == "proj-123"
        assert f["doc_uid"] == "doc-456"
        assert f["doc_name"] == "Том 4. КНС и сети"
        assert f["file_path"] == "/data/projects/kns_project.pdf"
        assert isinstance(f["page_num"], int) and f["page_num"] >= 1
        assert isinstance(f["chunk_index"], int)
        assert f["chunk_type"] in ("table", "table_part", "text")
        assert isinstance(f["bbox"], str)
        assert isinstance(f["content"], str)
        assert isinstance(f["section_path"], str)


def test_zvec_table_and_facts() -> None:
    ir = _make_sample_ir()
    records = list(iter_zvec_records(ir, config=RendererConfig(zvec=ZvecConfig(doc_uid="doc-01"))))

    # Find the table chunk
    table_chunks = [r for r in records if r["fields"]["chunk_type"] == "table"]
    assert len(table_chunks) == 1
    tc = table_chunks[0]

    assert tc["id"] == "doc-01_p01_t01"
    assert tc["fields"]["page_num"] == 1
    assert tc["fields"]["bbox"] == "[50.0, 100.0, 500.0, 300.0]"

    # Table facts verification
    facts = json.loads(tc["fields"]["table_facts"])
    assert facts["n_rows"] == 2
    assert facts["n_cols"] == 3
    cols = facts["cols"]
    assert "Расход кг/сут" in cols
    assert cols["Расход кг/сут"]["min"] == 20
    assert cols["Расход кг/сут"]["max"] == 120
    assert cols["Расход кг/сут"]["sum"] == 140

    # embed_text should have KV lines and breadcrumbs
    embed = tc["embed_text"]
    assert "Том 4. КНС и сети › Раздел 4. Водоснабжение" in embed
    assert "Таблица 1. Расход реагентов" in embed
    assert "Расход кг/сут: 120" in embed


def test_zvec_hierarchy_walkback() -> None:
    ir = _make_sample_ir()
    records = list(iter_zvec_records(ir))

    # Text chunk on page 2 should inherit section_path: "Раздел 4. Водоснабжение > Насосная станция"
    p2_chunks = [r for r in records if r["fields"]["page_num"] == 2]
    assert len(p2_chunks) >= 1
    p2_chunk = p2_chunks[0]
    assert p2_chunk["fields"]["section_path"] == "Раздел 4. Водоснабжение > Насосная станция"
    assert p2_chunk["fields"]["section_title"] == "Насосная станция"
    assert "Раздел 4. Водоснабжение > Насосная станция" in p2_chunk["embed_text"]


def test_write_outputs_zvec(tmp_path: Path) -> None:
    ir = _make_sample_ir()
    cfg = RouterConfig()
    cfg.renderers.enabled = {"zvec"}
    written = write_outputs(ir, tmp_path, "sample", config=cfg)

    assert set(written.keys()) == {"zvec"}
    out_file = written["zvec"]
    assert out_file == tmp_path / "sample.zvec.jsonl"
    assert out_file.exists()
    content = out_file.read_text(encoding="utf-8")
    assert len(content.splitlines()) >= 3


def test_zvec_figures_and_images() -> None:
    # Page 1: Heading + Drawing FigureBlock (with keyword "схема")
    fig_drawing = FigureBlock(
        bbox=BBox(x0=10.0, y0=20.0, x1=200.0, y1=300.0),
        caption="Схема насосной станции М1:100",
        image_index=0,
    )
    p1 = PageResult(
        page=0,
        markdown="## Раздел 4. Чертежи\n\nНиже представлена схема.",
        blocks=[
            HeadingBlock(level=2, text="Раздел 4. Чертежи"),
            fig_drawing,
        ],
        images=["/tmp/renders/page1_fig0.png"],
        src_tool=SrcTool.PDF_INSPECTOR,
    )

    # Page 2: Regular illustration FigureBlock (no drawing keyword)
    fig_image = FigureBlock(
        bbox=BBox(x0=10.0, y0=20.0, x1=100.0, y1=150.0),
        caption="Внешний вид здания",
        image_index=0,
    )
    p2 = PageResult(
        page=1,
        markdown="### Фото здания",
        blocks=[
            HeadingBlock(level=3, text="Фото здания"),
            fig_image,
        ],
        images=["/tmp/renders/page2_img0.png"],
        src_tool=SrcTool.PDF_INSPECTOR,
    )

    # Page 3: No FigureBlock, but page.images sidecars present
    p3 = PageResult(
        page=2,
        markdown="### Приложение с векторной графикой",
        blocks=[
            HeadingBlock(level=3, text="Приложение с векторной графикой"),
        ],
        images=["/tmp/renders/page3_vector_schema.png"],
        src_tool=SrcTool.PDF_INSPECTOR,
    )

    ir = IR(
        metadata=Metadata(page_count=3, title="Проект КНС"),
        pages=[p1, p2, p3],
    )

    records = list(iter_zvec_records(ir, config=ZvecConfig(doc_uid="kns_doc")))

    drawings = [r for r in records if r["fields"]["chunk_type"] == "drawing"]
    images = [r for r in records if r["fields"]["chunk_type"] == "image"]

    # 1 from FigureBlock on page 1, 1 from page.images with "vector" on page 3
    assert len(drawings) == 2
    assert len(images) == 1

    # Drawing 1 (FigureBlock with "схема")
    d1 = drawings[0]
    assert d1["id"] == "kns_doc_p01_draw01"
    assert d1["fields"]["chunk_type"] == "drawing"
    assert d1["fields"]["caption"] == "Схема насосной станции М1:100"
    assert d1["fields"]["image_url"] == "/tmp/renders/page1_fig0.png"
    assert "[10.0, 20.0, 200.0, 300.0]" == d1["fields"]["bbox"]
    assert "Чертёж / Схема" in d1["embed_text"]
    assert "Схема насосной станции М1:100" in d1["embed_text"]
    meta1 = json.loads(d1["fields"]["image_meta"])
    assert meta1["kind"] == "drawing"
    assert meta1["has_bbox"] is True

    # Image (FigureBlock)
    im = images[0]
    assert im["id"] == "kns_doc_p02_img01"
    assert im["fields"]["chunk_type"] == "image"
    assert im["fields"]["caption"] == "Внешний вид здания"
    assert im["fields"]["image_url"] == "/tmp/renders/page2_img0.png"
    assert "Иллюстрация" in im["embed_text"]

    # Drawing 2 (page.images fallback with vector keyword)
    d2 = drawings[1]
    assert d2["id"] == "kns_doc_p03_draw01"
    assert d2["fields"]["chunk_type"] == "drawing"
    assert d2["fields"]["image_url"] == "/tmp/renders/page3_vector_schema.png"

