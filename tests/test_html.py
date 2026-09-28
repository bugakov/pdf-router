"""HTML-as-.doc ingest tests (ADR-0008 / TKT-OI-04 ruling 3).

Synthetic control runs everywhere; the 190-ФЗ law anchor skips when the
corpus is unmounted (TKT-OI-07). The bar for this format is deliberately
small — tag-stripped prose with honest metadata, nothing more.
"""

from __future__ import annotations

import json
import time
from pathlib import Path

import pytest

from pdf_router import (
    convert,
    from_dict,
    process_pdf,
    render_chunks,
    render_markdown,
    to_dict,
)
from pdf_router.adapters.html import extract_ir, html_info
from pdf_router.ir import HeadingBlock

LAW_VOLUME = Path(
    "/home/scanum/kotel-zamechanna/normas/01_ФЗ_и_ПП_РФ"
    "/190-ФЗ_Градостроительный_кодекс.doc"
)

_CONTROL = (
    "<!DOCTYPE html>\r\n"
    "<html><head><title>Тестовый документ</title>"
    "<style>p { color: red; }</style>"
    '<script>var x = "<div>";</script>'
    "</head>\r\n"
    "<body>"
    "<h1>Раздел первый</h1>"
    "<p>Ампер &amp; сбоку&nbsp;улица</p>"
    "<p>Второй абзац<br>с переносом</p>"
    "<h2>Второй раздел</h2>"
    "<ul><li>Элемент</li><li>Второй элемент</li></ul>"
    "<!-- комментарий не должен попасть в вывод -->"
    "</body></html>"
)


def _html(tmp_path: Path, content: str, name: str = "law.doc") -> Path:
    p = tmp_path / name
    p.write_bytes(content.encode("utf-8"))
    return p


def _corpus(path: Path) -> str:
    if not path.exists():
        pytest.skip(f"golden corpus anchor not available: {path}")
    return str(path)


# ---------------------------------------------------------------------------
# Synthetic control
# ---------------------------------------------------------------------------


def test_metadata_and_page_semantics(tmp_path: Path) -> None:
    ir = extract_ir(str(_html(tmp_path, _CONTROL)))
    m = ir.metadata
    assert m.page_count == 1
    assert m.source_format == "html"
    assert m.pdf_type == "text_based"
    assert m.confidence == 1.0
    assert m.is_complex_layout is False
    assert m.pages_with_tables == []
    assert m.title == "Тестовый документ"
    assert m.has_encoding_issues is False
    page = ir.pages[0]
    assert page.page == 0
    assert page.needs_ocr is False
    assert page.src_tool.value == "html_adapter"
    assert page.images == []
    assert page.error is None


def test_tags_strip_to_prose(tmp_path: Path) -> None:
    """script/style/head/comments never reach the output; entities are
    decoded; ``<br>`` and block tags split paragraphs."""
    ir = extract_ir(str(_html(tmp_path, _CONTROL)))
    md = ir.pages[0].markdown
    assert md == (
        "# Раздел первый\n\n"
        "Ампер & сбоку улица\n\n"
        "Второй абзац\n\n"
        "с переносом\n\n"
        "## Второй раздел\n\n"
        "Элемент\n\n"
        "Второй элемент"
    )
    assert "<" not in md and ">" not in md  # no markup leaks
    assert "color: red" not in md  # style payload skipped
    assert "var x" not in md and "<div>" not in md  # script payload skipped
    assert "комментарий" not in md  # comment skipped


def test_headings_become_blocks(tmp_path: Path) -> None:
    ir = extract_ir(str(_html(tmp_path, _CONTROL)))
    heads = [b for b in ir.pages[0].blocks if isinstance(b, HeadingBlock)]
    assert [(h.level, h.text) for h in heads] == [
        (1, "Раздел первый"),
        (2, "Второй раздел"),
    ]
    assert all(h.source == "html_tag" for h in heads)
    assert all(h.bbox is None for h in heads)


def test_html_ir_round_trip(tmp_path: Path) -> None:
    ir = extract_ir(str(_html(tmp_path, _CONTROL)))
    assert from_dict(to_dict(ir)) == ir


def test_renderers_flow_through_untouched(tmp_path: Path) -> None:
    ir = extract_ir(str(_html(tmp_path, _CONTROL)))
    md = render_markdown(ir)
    assert "# Раздел первый" in md and "# Второй раздел" in md
    payloads = [json.loads(ln) for ln in render_chunks(ir).splitlines() if ln.strip()]
    assert payloads
    assert all(p["embed"].strip() for p in payloads)
    assert all(p["metadata"]["src_tool"] == "html_adapter" for p in payloads)
    joined = "\n".join(p["embed"] for p in payloads)
    assert "Второй элемент" in joined


def test_convert_end_to_end(tmp_path: Path) -> None:
    md = convert(str(_html(tmp_path, _CONTROL)))
    assert "# Раздел первый" in md
    assert "<" not in md


def test_process_pdf_pure_run_writes_nothing(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)
    doc = _html(tmp_path, _CONTROL)
    ir, report = process_pdf(str(doc))
    assert report.pages_total == 1
    assert report.pages_native == 1
    assert report.pages_failed == 0
    assert ir.metadata.source_format == "html"
    assert list(tmp_path.iterdir()) == [doc]


def test_cli_full_run_writes_three_renderers(tmp_path: Path, monkeypatch) -> None:
    from pdf_router import cli

    monkeypatch.chdir(tmp_path)
    doc = _html(tmp_path, _CONTROL, name="закон.doc")
    out = tmp_path / "out"
    code = cli.main(["--out", str(out), str(doc)])
    assert code == 0
    assert (out / "закон.md").is_file()
    assert (out / "закон.chunks.jsonl").is_file()
    ir_json = json.loads((out / "закон.ir.json").read_text(encoding="utf-8"))
    assert ir_json["metadata"]["source_format"] == "html"
    assert ir_json["ir_version"] == "0.6"


def test_html_info_matches_ir(tmp_path: Path) -> None:
    doc = _html(tmp_path, _CONTROL)
    info = html_info(str(doc))
    assert info == {
        "source_format": "html",
        "pdf_type": "text_based",
        "page_count": 1,
        "confidence": 1.0,
        "title": "Тестовый документ",
        "tables": 0,
        "images": 0,
    }


def test_html_bom_input(tmp_path: Path) -> None:
    p = tmp_path / "bom.doc"
    p.write_bytes(b"\xef\xbb\xbf" + "<html><body><p>Текст</p></body></html>".encode("utf-8"))
    ir = extract_ir(str(p))
    assert ir.pages[0].markdown == "Текст"
    assert ir.metadata.has_encoding_issues is False


def test_undecodable_bytes_flag_encoding_issues(tmp_path: Path) -> None:
    """Latin-1 bytes in a UTF-8 world decode with U+FFFD and the metadata
    says so (honest, not silent)."""
    p = tmp_path / "mojibake.doc"
    p.write_bytes(b"<html><body><p>caf\xe9</p></body></html>")  # é as latin-1
    ir = extract_ir(str(p))
    assert ir.metadata.has_encoding_issues is True
    assert "caf" in ir.pages[0].markdown


# ---------------------------------------------------------------------------
# Corpus tier: the 190-ФЗ law anchor (TKT-OI-07)
# ---------------------------------------------------------------------------


def test_golden_190fz_law_anchor() -> None:
    path = _corpus(LAW_VOLUME)
    t0 = time.perf_counter()
    ir = extract_ir(path)
    elapsed = time.perf_counter() - t0
    md = ir.pages[0].markdown
    assert ir.metadata.page_count == 1
    assert ir.metadata.source_format == "html"
    assert len(md) > 1_000_000  # measured 1,335,359
    assert "Градостроительный кодекс" in md
    assert "<" not in md  # tag-strip is total
    assert "DOCTYPE" not in md
    assert ir.metadata.title  # <title> captured
    assert elapsed < 5.0  # measured 1.28 s
    assert from_dict(to_dict(ir)) == ir
    payloads = [json.loads(ln) for ln in render_chunks(ir).splitlines() if ln.strip()]
    assert payloads
    assert all(p["embed"].strip() for p in payloads)
