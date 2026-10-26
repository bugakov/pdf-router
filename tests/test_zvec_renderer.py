"""Tests for the native zvec target renderer (ADR-0010)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

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
from pdf_router.ir import BBox, HeadingBlock, TableBlock


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
