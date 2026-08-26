"""Ticket 11: end-to-end CLI run on big_test.pdf (44p, mixed)."""

import json

import pytest

from pdf_router import cli
from pdf_router.ir import from_dict


@pytest.mark.slow
def test_big_test_e2e_cli(big_test_pdf, tmp_path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)
    out_dir = tmp_path / "out_big"
    code = cli.main(["--out", str(out_dir), str(big_test_pdf)])
    assert code == 2  # 10 OCR pages skipped (no OCR configured), 34 native OK

    ir_d = json.loads((out_dir / "big_test.ir.json").read_text(encoding="utf-8"))
    assert ir_d["metadata"]["pdf_type"] == "mixed"
    assert len(ir_d["metadata"]["pages_needing_ocr"]) == 10

    # first 25 pages (1-indexed) are native
    for page in ir_d["pages"][:25]:
        assert page["src_tool"] == "pdf_inspector"
        assert page["needs_ocr"] is False

    # JSON round-trips through IR.from_dict
    ir = from_dict(ir_d)
    assert ir.metadata.page_count == 44
    assert len(ir.pages) == 44

    # every skipped OCR page carries the error marker
    ocr_pages = {p["page"] for p in ir_d["pages"] if p["needs_ocr"]}
    assert len(ocr_pages) == 10
    for page in ir_d["pages"]:
        if page["needs_ocr"]:
            assert page["error"] == "no_ocr_configured"
