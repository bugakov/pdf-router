"""Ticket 11: end-to-end CLI run on p27.pdf — default (rapidocr) and tesseract paths."""

import json

import pytest

from pdf_router import cli


@pytest.mark.slow
def test_p27_e2e_no_ocr(p27_pdf, tmp_path, monkeypatch) -> None:
    """p27 is image_based. Default engine (rapidocr) runs the fallback and
    succeeds → full success, exit 0."""
    monkeypatch.chdir(tmp_path)
    out_dir = tmp_path / "out27"
    code = cli.main(["--out", str(out_dir), str(p27_pdf)])
    assert code == 0
    ir_d = json.loads((out_dir / "p27.ir.json").read_text(encoding="utf-8"))
    assert ir_d["metadata"]["pdf_type"] == "image_based"
    page = ir_d["pages"][0]
    assert page["error"] is None
    assert page["src_tool"] == "rapidocr"
    assert page["markdown"].strip()


@pytest.mark.slow
def test_p27_e2e_with_tesseract(p27_pdf, tmp_path, monkeypatch) -> None:
    """v0.3: tesseract requires explicit routing.ocr_fallback_engine = "tesseract"."""
    monkeypatch.chdir(tmp_path)
    config = tmp_path / "router.toml"
    config.write_text(
        '[routing]\n'
        'ocr_fallback_engine = "tesseract"\n'
        '[ocr.tesseract]\n'
        'languages = ["rus", "eng"]\n',
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
