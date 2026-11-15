"""Tests for the PyO3 native extension wheel and public facade."""

from __future__ import annotations

from pathlib import Path
import pytest

try:
    import pdf_router_native
except ImportError:
    try:
        from pdf_router import pdf_router_native
    except ImportError:
        pdf_router_native = None

pytestmark = pytest.mark.skipif(
    pdf_router_native is None,
    reason="pdf_router_native extension wheel is not installed",
)


def test_wheel_version() -> None:
    ver = pdf_router_native.version()
    assert isinstance(ver, str)
    assert ver == "0.7.0"


def test_wheel_detect_format() -> None:
    assert pdf_router_native.detect_format(b"%PDF-1.7") == "pdf"
    assert pdf_router_native.detect_format(b"AT&TFORM\x00\x00\x00\x10DJVU") == "djvu"
    assert pdf_router_native.detect_format(b"unknown garbage") == "unknown"


def test_wheel_convert(p5_pdf: Path) -> None:
    md = pdf_router_native.convert(str(p5_pdf))
    assert isinstance(md, str)
    assert md.strip()


def test_wheel_process_pdf(p5_pdf: Path) -> None:
    ir_dict, report_dict = pdf_router_native.process_pdf(str(p5_pdf))
    assert isinstance(ir_dict, dict)
    assert isinstance(report_dict, dict)
    assert report_dict["pages_total"] == 1
    assert report_dict["pages_failed"] == 0
    assert "metadata" in ir_dict
    assert "pages" in ir_dict
    assert len(ir_dict["pages"]) == 1
    assert ir_dict["pages"][0]["markdown"].strip()


def test_wheel_write_outputs(p5_pdf: Path, tmp_path: Path) -> None:
    ir_dict, _ = pdf_router_native.process_pdf(str(p5_pdf))
    written = pdf_router_native.write_outputs(ir_dict, str(tmp_path), "doc")
    assert isinstance(written, dict)
    assert set(written.keys()) == {"md", "chunks", "json"}
    for p in written.values():
        assert Path(p).exists()


def test_wheel_load_config(tmp_path: Path) -> None:
    empty_cfg = pdf_router_native.load_config()
    assert isinstance(empty_cfg, dict)

    sample_toml = tmp_path / "router.toml"
    sample_toml.write_text('[routing]\nocr_fallback_engine = "tesseract"\n', encoding="utf-8")
    cfg = pdf_router_native.load_config(str(sample_toml))
    assert isinstance(cfg, dict)
    assert cfg.get("routing", {}).get("ocr_fallback_engine") == "tesseract"


def test_wheel_iter_zvec_docs_from_path(p5_pdf: Path) -> None:
    docs = pdf_router_native.iter_zvec_docs(str(p5_pdf), doc_uid="test_uid")
    assert isinstance(docs, list)
    assert len(docs) >= 1
    rec = docs[0]
    assert rec["fields"]["doc_uid"] == "test_uid"
    assert "content" in rec["fields"]
    assert "embed_text" in rec


def test_wheel_iter_zvec_docs_from_ir(p5_pdf: Path) -> None:
    ir_dict, _ = pdf_router_native.process_pdf(str(p5_pdf))
    docs = pdf_router_native.iter_zvec_docs(ir_dict, doc_uid="from_ir")
    assert isinstance(docs, list)
    assert len(docs) >= 1
    assert docs[0]["fields"]["doc_uid"] == "from_ir"


def test_wheel_renderers(p5_pdf: Path) -> None:
    ir_dict, _ = pdf_router_native.process_pdf(str(p5_pdf))
    md = pdf_router_native.render_markdown(ir_dict)
    assert isinstance(md, str)
    assert md.strip()

    chunks = pdf_router_native.render_chunks(ir_dict)
    assert isinstance(chunks, str)
    assert chunks.strip()

    zvec = pdf_router_native.render_zvec(ir_dict, doc_uid="zvec_uid")
    assert isinstance(zvec, str)
    assert "zvec_uid" in zvec

    json_str = pdf_router_native.render_json(ir_dict)
    assert isinstance(json_str, str)
    assert json_str.strip().startswith("{")
