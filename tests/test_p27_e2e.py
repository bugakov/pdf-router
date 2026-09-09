"""Ticket 11: end-to-end CLI run on p27.pdf — no-OCR and tesseract paths.

v0.3: pdf_inspector_ocr is the default fallback engine.
Tests updated: tesseract now requires explicit routing.ocr_fallback_engine = "tesseract".
"""

import json

import pytest

from pdf_router import cli


@pytest.mark.slow
def test_p27_e2e_no_ocr(p27_pdf, tmp_path, monkeypatch) -> None:
    """p27 is image_based. Default pdf_inspector_ocr runs but fails (missing libs).
    In v0.3 there is no way to skip OCR entirely — it is always attempted."""
    monkeypatch.chdir(tmp_path)
    out_dir = tmp_path / "out27"
    code = cli.main(["--out", str(out_dir), str(p27_pdf)])
    # v0.3: pdf_inspector_ocr runs and fails → pages_failed > 0 → exit 1
    assert code == 1
    ir_d = json.loads((out_dir / "p27.ir.json").read_text(encoding="utf-8"))
    assert ir_d["metadata"]["pdf_type"] == "image_based"
    for page in ir_d["pages"]:
        # v0.3: engine IS configured, but fails. Error is set, not "no_ocr_configured"
        assert page["error"] is not None
        assert page["error"] != "no_ocr_configured"
        assert page["src_tool"] == "pdf_inspector_ocr"


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
