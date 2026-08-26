"""Ticket 11: end-to-end CLI run on p5.pdf — file presence + golden anchors."""

import json

from pdf_router import cli


def test_p5_e2e_cli(p5_pdf, tmp_path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)
    out_dir = tmp_path / "out"
    code = cli.main(["--out", str(out_dir), str(p5_pdf)])
    assert code == 0

    md = (out_dir / "p5.md").read_text(encoding="utf-8")
    assert "Обозначение" in md
    assert "ИЛО9.1" in md

    chunks_lines = [ln for ln in (out_dir / "p5.chunks.jsonl").read_text(encoding="utf-8").splitlines() if ln]
    assert chunks_lines
    for ln in chunks_lines:
        chunk = json.loads(ln)
        assert {"chunk_id", "text", "page", "section", "src_tool"} <= set(chunk)

    ir_d = json.loads((out_dir / "p5.ir.json").read_text(encoding="utf-8"))
    assert ir_d["metadata"]["pdf_type"] == "text_based"
    assert ir_d["metadata"]["page_count"] == 1
    assert ir_d["pages"][0]["src_tool"] == "pdf_inspector"
