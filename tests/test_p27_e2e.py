"""Ticket 11: end-to-end CLI run on p27.pdf — no-OCR and tesseract paths."""

import json

from pdf_router import cli


def test_p27_e2e_no_ocr(p27_pdf, tmp_path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)
    out_dir = tmp_path / "out27"
    code = cli.main(["--out", str(out_dir), str(p27_pdf)])
    assert code == 2  # partial: the single page was skipped

    ir_d = json.loads((out_dir / "p27.ir.json").read_text(encoding="utf-8"))
    assert ir_d["metadata"]["pdf_type"] == "image_based"
    for page in ir_d["pages"]:
        assert page["error"] == "no_ocr_configured"
        assert page["src_tool"] == "pdf_inspector"


def test_p27_e2e_with_tesseract(p27_pdf, tmp_path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)
    config = tmp_path / "router.toml"
    config.write_text(
        '[ocr.tesseract]\nlanguages = ["rus", "eng"]\n',
        encoding="utf-8",
    )
    out_dir = tmp_path / "out27t"
    code = cli.main(["--config", str(config), "--out", str(out_dir), str(p27_pdf)])
    assert code == 0

    ir_d = json.loads((out_dir / "p27.ir.json").read_text(encoding="utf-8"))
    page = ir_d["pages"][0]
    assert page["src_tool"] == "tesseract"
    assert page["error"] is None
    md = (out_dir / "p27.md").read_text(encoding="utf-8")
    assert md.strip()
