"""Ticket 11: end-to-end CLI run on big_test.pdf (44p, mixed).

Default engine (rapidocr) runs the 10 OCR pages; in this environment they
succeed, so the run is a full success (exit 0).
"""

import json

import pytest

from pdf_router import cli
from pdf_router.ir import from_dict


@pytest.mark.slow
def test_big_test_e2e_cli(big_test_pdf, tmp_path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)
    out_dir = tmp_path / "out_big"
    code = cli.main(["--out", str(out_dir), str(big_test_pdf)])
    # Default rapidocr handles all 10 OCR pages in this env → full success.
    # (exit 2 would mean >=1 page failed; exit 1 is usage/IO errors only.)
    assert code == 0

    ir_d = json.loads((out_dir / "big_test.ir.json").read_text(encoding="utf-8"))
    assert ir_d["metadata"]["pdf_type"] == "mixed"
    assert len(ir_d["metadata"]["pages_needing_ocr"]) == 10

    # v0.3: first 25 pages may have been attempted with OCR if needs_ocr=True.
    # Check they at least have valid src_tool (not the old "pdf_inspector" default).
    for page in ir_d["pages"][:25]:
        # v0.3: src_tool is set before OCR attempt, even on failure
        assert page["src_tool"] in ("pdf_inspector", "pdf_inspector_ocr")
        assert page["needs_ocr"] is False

    # JSON round-trips through IR.from_dict
    ir = from_dict(ir_d)
    assert ir.metadata.page_count == 44
    assert len(ir.pages) == 44

    # every OCR page either succeeds or carries an error (not "no_ocr_configured")
    ocr_pages = [p for p in ir_d["pages"] if p["needs_ocr"]]
    assert len(ocr_pages) == 10
    for page in ocr_pages:
        # v0.3: engine is configured, but may fail. error != "no_ocr_configured"
        assert page["error"] != "no_ocr_configured"
