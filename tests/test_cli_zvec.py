"""Test CLI converting pre-computed IR directly to zvec JSONL."""

import json
from pathlib import Path
from pdf_router.cli import main
from pdf_router.ir import IR, Metadata, PageResult, SrcTool, to_dict


def test_cli_convert_ir_to_zvec(tmp_path: Path, monkeypatch):
    # 1. Create a sample IR file
    ir = IR(
        metadata=Metadata(page_count=1, source_path="test_doc.pdf", title="Тестовый документ IR"),
        pages=[
            PageResult(
                page=0,
                markdown="## Раздел 1. Введение\n\nТекст раздела для zvec тестирования.",
                src_tool=SrcTool.PDF_INSPECTOR,
            )
        ],
    )
    ir_path = tmp_path / "sample.ir.json"
    ir_path.write_text(json.dumps(to_dict(ir), ensure_ascii=False), encoding="utf-8")

    out_dir = tmp_path / "out"

    # 2. Run CLI command: pdf-router sample.ir.json --render zvec --out out_dir
    monkeypatch.setattr(
        "sys.argv",
        ["pdf-router", str(ir_path), "--render", "zvec", "--out", str(out_dir)],
    )

    exit_code = main()
    assert exit_code == 0

    # 3. Verify output
    zvec_file = out_dir / "sample.zvec.jsonl"
    assert zvec_file.exists(), f"Expected {zvec_file} to exist"

    lines = [json.loads(line) for line in zvec_file.read_text(encoding="utf-8").splitlines() if line.strip()]
    assert len(lines) >= 1
    assert any("Тестовый документ IR" in line["fields"]["doc_name"] for line in lines)
    assert any("Текст раздела для zvec тестирования" in line["fields"]["content"] for line in lines)
