"""xlsx adapter tests (ADR-0008): synthetic control + golden corpus anchors.

Two layers, the same shape as the docx suite (TKT-OI-07 quality bar):

- **Synthetic control**: a hand-built workbook pinning every structural
  claim of the adapter — sheet=page semantics, alternating dense/sparse
  runs, vertical merge repeat / horizontal merge first-cell-only, hidden
  col/row cutting, cached formula values, repr float cleanup, the
  header_rows heuristic, IR round-trip and the facade/CLI doors.

- **Golden anchors**: real corpus workbooks (ЛСР 10-sheet, СР-2 formulas,
  smallest remarks file) with exact counts and budgets. Skipped (not
  failed) when the corpus is not mounted.
"""

from __future__ import annotations

import io
import json
import re
import time
import zipfile
from pathlib import Path
from xml.sax.saxutils import escape, quoteattr

import pytest

from pdf_router import (
    convert,
    from_dict,
    process_pdf,
    render_chunks,
    render_markdown,
    to_dict,
)
from pdf_router.adapters.xlsx import extract_ir, xlsx_info
from pdf_router.ir import HeadingBlock, TableBlock

# ---------------------------------------------------------------------------
# Synthetic OOXML builder
# ---------------------------------------------------------------------------

_S_NS = "http://schemas.openxmlformats.org/spreadsheetml/2006/main"
_R_NS = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"
_CT_NS = "http://schemas.openxmlformats.org/package/2006/content-types"
_REL_NS = "http://schemas.openxmlformats.org/package/2006/relationships"
_DC_NS = "http://purl.org/dc/elements/1.1/"
_CP_NS = "http://schemas.openxmlformats.org/package/2006/metadata/core-properties"

_REF_RE = re.compile(r"([A-Z]+)(\d+)")


def _col_ci(letters: str) -> int:
    n = 0
    for ch in letters:
        n = n * 26 + (ord(ch) - 64)
    return n


def _build_xlsx(
    tmp_path: Path,
    *,
    sheets: list[dict],
    title: str | None = None,
    name: str = "control.xlsx",
) -> Path:
    """Assemble a structurally-complete workbook (the synthetic control).

    Sheet spec keys: ``name``, ``cells`` (ref -> value), ``merges``
    (``"A13:P13"`` refs), ``hidden_cols`` (``[(min, max)]``),
    ``hidden_rows`` (``[r]``). Cell values: plain ``str`` goes to
    sharedStrings; ``("n", raw)`` is a raw numeric ``<v>`` (float-noise
    control); ``("f", formula, cached)`` is a formula with its cached value.
    """
    shared: dict[str, int] = {}

    def sidx(s: str) -> int:
        if s not in shared:
            shared[s] = len(shared)
        return shared[s]

    def cell_xml(ref: str, spec) -> str:
        if isinstance(spec, (int, float)):
            spec = ("n", str(spec) if isinstance(spec, int) else repr(spec))
        if isinstance(spec, tuple):
            if spec[0] == "n":
                return f'<c r="{ref}"><v>{escape(spec[1])}</v></c>'
            if spec[0] == "f":
                return f'<c r="{ref}"><f>{escape(spec[1])}</f><v>{escape(spec[2])}</v></c>'
            raise ValueError(f"unknown cell spec: {spec!r}")
        return f'<c r="{ref}" t="s"><v>{sidx(spec)}</v></c>'

    sheet_parts: list[str] = []
    workbook_sheets: list[str] = []
    wb_rels: list[str] = []
    for i, spec in enumerate(sheets, start=1):
        by_row: dict[int, list[tuple[int, str, object]]] = {}
        for ref, val in spec.get("cells", {}).items():
            m = _REF_RE.fullmatch(ref)
            assert m, ref
            by_row.setdefault(int(m.group(2)), []).append((_col_ci(m.group(1)), ref, val))
        rows_xml = []
        hidden_rows = set(spec.get("hidden_rows", ()))
        for r in sorted(by_row):
            hidden = ' hidden="1"' if r in hidden_rows else ""
            cells_xml = "".join(cell_xml(ref, val) for _, ref, val in sorted(by_row[r]))
            rows_xml.append(f'<row r="{r}"{hidden}>{cells_xml}</row>')
        cols_xml = ""
        if spec.get("hidden_cols"):
            entries = "".join(
                f'<col min="{lo}" max="{hi}" hidden="1" width="8.43" customWidth="1"/>'
                for lo, hi in spec["hidden_cols"]
            )
            cols_xml = f"<cols>{entries}</cols>"
        merges = spec.get("merges", [])
        merges_xml = ""
        if merges:
            inner = "".join(f'<mergeCell ref="{m}"/>' for m in merges)
            merges_xml = f'<mergeCells count="{len(merges)}">{inner}</mergeCells>'
        sheet_parts.append(
            '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
            f'<worksheet xmlns="{_S_NS}">'
            f"{cols_xml}<sheetData>{''.join(rows_xml)}</sheetData>{merges_xml}"
            "</worksheet>"
        )
        workbook_sheets.append(
            f"<sheet name={quoteattr(spec['name'])} sheetId=\"{i}\" r:id=\"rId{i}\"/>"
        )
        wb_rels.append(
            f'<Relationship Id="rId{i}" Type="{_R_NS}/worksheet" '
            f'Target="worksheets/sheet{i}.xml"/>'
        )

    parts: dict[str, str] = {
        "[Content_Types].xml": (
            f'<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
            f'<Types xmlns="{_CT_NS}">'
            '<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>'
            '<Default Extension="xml" ContentType="application/xml"/>'
            '<Override PartName="/xl/workbook.xml" '
            'ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml"/>'
            + "".join(
                f'<Override PartName="/xl/worksheets/sheet{i}.xml" '
                'ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml"/>'
                for i in range(1, len(sheets) + 1)
            )
            + (
                '<Override PartName="/xl/sharedStrings.xml" '
                'ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.sharedStrings+xml"/>'
                if shared
                else ""
            )
            + "</Types>"
        ),
        "_rels/.rels": (
            f'<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
            f'<Relationships xmlns="{_REL_NS}">'
            f'<Relationship Id="rId1" Type="{_R_NS}/officeDocument" Target="xl/workbook.xml"/>'
            + (
                '<Relationship Id="rId2" Type="http://schemas.openxmlformats.org/package/2006/relationships/metadata/core-properties" '
                'Target="docProps/core.xml"/>'
                if title
                else ""
            )
            + "</Relationships>"
        ),
        "xl/workbook.xml": (
            '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
            f'<workbook xmlns="{_S_NS}" xmlns:r="{_R_NS}">'
            f"<sheets>{''.join(workbook_sheets)}</sheets></workbook>"
        ),
        "xl/_rels/workbook.xml.rels": (
            f'<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
            f'<Relationships xmlns="{_REL_NS}">{"".join(wb_rels)}</Relationships>'
        ),
    }
    if shared:
        items = [None] * len(shared)
        for text, idx in shared.items():
            items[idx] = text
        parts["xl/sharedStrings.xml"] = (
            '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
            f'<sst xmlns="{_S_NS}" count="{len(items)}" uniqueCount="{len(items)}">'
            + "".join(f'<si><t xml:space="preserve">{escape(t)}</t></si>' for t in items)
            + "</sst>"
        )
    if title:
        parts["docProps/core.xml"] = (
            '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
            f'<cp:coreProperties xmlns:cp="{_CP_NS}" xmlns:dc="{_DC_NS}">'
            f"<dc:title>{escape(title)}</dc:title></cp:coreProperties>"
        )
    for i, xml in enumerate(sheet_parts, start=1):
        parts[f"xl/worksheets/sheet{i}.xml"] = xml

    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        for part, content in parts.items():
            zf.writestr(part, content)
    p = tmp_path / name
    p.write_bytes(buf.getvalue())
    return p


def _control(tmp_path: Path, **kw) -> Path:
    """The standard two-sheet control workbook (TKT-OI-07 fast tier).

    Sheet "Смета" (visible cols A,B,D — C is hidden; width 4):
    sparse preamble, dense header+body with an empty row gap (r4) inside
    the run, a vertical merge A5:A7, a formula row, a hidden row (r9),
    a horizontal title merge (r10), whitespace cells (r11) and a
    float-noise number (r12). Sheet "Лист2": one cell — width shrinks to
    it, so the row is trivially dense and lands in a 1x1 table (spec
    TKT-OI-03 #6 has no single-cell exception).
    """
    sheets = [
        {
            "name": "Смета",
            "hidden_cols": [(3, 3)],
            "hidden_rows": [9],
            "merges": ["A5:A7", "A10:D10"],
            "cells": {
                "A1": "Рабочая преамбула к смете",
                "A2": "Позиция",
                "B2": "Ед.",
                "D2": "Цена",
                "A3": "Цемент",
                "B3": "т",
                "C3": "скрытая колонка",
                "D3": 1000,
                "C4": "тоже скрыто",  # hidden col keeps the r4 gap empty
                "A5": "Группа",
                "B5": "x",
                "D5": 1,
                "B6": "y",
                "D6": 2,
                "B7": "z",
                "D7": 3,
                "A8": "Итого",
                "D8": ("f", "=СУММ(D5:D7)", "3000"),
                "A9": "скрытая строка",
                "D9": 99,
                "A10": "Общая графа",
                "A11": "   ",
                "B11": "После пробелов",
                "D12": ("n", "4.8000000000000001E-2"),
            },
        },
        {"name": "Лист2", "cells": {"A1": "Второй лист"}},
    ]
    return _build_xlsx(tmp_path, sheets=sheets, title="Контрольный файл", **kw)


# ---------------------------------------------------------------------------
# Page semantics + honest metadata (TKT-OI-03)
# ---------------------------------------------------------------------------


def test_sheet_is_page_and_markdown_opens_with_heading(tmp_path: Path) -> None:
    ir = extract_ir(str(_control(tmp_path)))
    m = ir.metadata
    assert m.page_count == 2
    assert m.pages_with_tables == [1, 2]
    assert m.source_format == "xlsx"
    assert m.pdf_type == "text_based"
    assert m.confidence == 1.0
    assert m.is_complex_layout is False
    assert m.title == "Контрольный файл"
    assert [p.page for p in ir.pages] == [0, 1]
    assert ir.pages[0].markdown.startswith("## Смета")
    assert ir.pages[1].markdown.startswith("## Лист2")
    assert ir.pages[1].markdown == "## Лист2\n\n| Второй лист |\n| --- |"
    for p in ir.pages:
        assert p.needs_ocr is False
        assert p.src_tool.value == "xlsx_adapter"
        assert p.error is None
    heads = [b for b in ir.pages[0].blocks if isinstance(b, HeadingBlock)]
    assert len(heads) == 1 and heads[0].level == 2 and heads[0].text == "Смета"


def test_dense_runs_become_tables_sparse_rows_prose(tmp_path: Path) -> None:
    """Dense (>=50% visible cols) rows form one table; the empty row 4 and
    the hidden row 9 do not break the run; sparse rows are prose."""
    ir = extract_ir(str(_control(tmp_path)))
    tables = [b for b in ir.pages[0].blocks if isinstance(b, TableBlock)]
    assert len(tables) == 1
    rows = tables[0].rows
    # r2 header, r3 body, r5-r7 body (r4 empty — gap inside the run), r8 formula
    assert len(rows) == 6
    assert rows[0] == ["Позиция", "Ед.", "", "Цена"]
    assert rows[1] == ["Цемент", "т", "", "1000"]
    assert rows[-1][0] == "Итого" and rows[-1][3] == "3000"
    assert all(len(r) == 4 for r in rows)  # rectangular, hidden C stays a column
    assert all(r[2] == "" for r in rows)  # hidden column content is cut
    md = ir.pages[0].markdown
    assert "Рабочая преамбула к смете" in md
    assert "После пробелов" in md
    assert tables[0].markdown in md  # the table is in the page markdown


def test_vertical_merge_repeats_down(tmp_path: Path) -> None:
    ir = extract_ir(str(_control(tmp_path)))
    table = next(b for b in ir.pages[0].blocks if isinstance(b, TableBlock))
    assert [r[0] for r in table.rows[2:5]] == ["Группа", "Группа", "Группа"]


def test_horizontal_merge_keeps_first_cell_only(tmp_path: Path) -> None:
    """The r10 title merge spans A:D — one occurrence in prose, not four."""
    ir = extract_ir(str(_control(tmp_path)))
    md = ir.pages[0].markdown
    assert md.count("Общая графа") == 1
    assert "Общая графа Общая графа" not in md


def test_hidden_row_and_column_content_is_cut(tmp_path: Path) -> None:
    ir = extract_ir(str(_control(tmp_path)))
    blob = json.dumps(to_dict(ir), ensure_ascii=False)
    for secret in ("скрытая колонка", "тоже скрыто", "скрытая строка"):
        assert secret not in blob
    # hidden row 9 dropped entirely: D9=99 never appears
    assert not re.search(r"(?<![\d.])99(?![\d.])", blob)


def test_values_repr_cleanup_and_formula_cache(tmp_path: Path) -> None:
    ir = extract_ir(str(_control(tmp_path)))
    md = ir.pages[0].markdown
    assert "0.048" in md  # float noise killed: 4.8000000000000001E-2 -> 0.048
    assert "4.8000000000000001" not in md and "E-2" not in md
    assert "1000.0" not in md  # integral float -> plain digits
    assert "3000" in md  # cached formula value published
    assert "=СУММ" not in md and "<f>" not in md  # formula text never leaks


def test_header_rows_heuristic(tmp_path: Path) -> None:
    """Leading short-text digit-free rows = header (1 here); the all-digit
    body row below fails the heuristic and stays body (TKT-OI-03 #7)."""
    ir = extract_ir(str(_control(tmp_path)))
    table = next(b for b in ir.pages[0].blocks if isinstance(b, TableBlock))
    assert table.header_rows == 1


def test_empty_sheet_is_still_a_page(tmp_path: Path) -> None:
    ir = extract_ir(str(_build_xlsx(tmp_path, sheets=[{"name": "Пустой", "cells": {}}])))
    assert ir.metadata.page_count == 1
    assert ir.pages[0].markdown == "## Пустой"
    assert len(ir.pages[0].blocks) == 1  # the heading block only
    assert ir.metadata.pages_with_tables == []


# ---------------------------------------------------------------------------
# Failures are document-level (exit 3), not format rejections (exit 1)
# ---------------------------------------------------------------------------


def test_malformed_workbook_raises_document_error(tmp_path: Path) -> None:
    p = tmp_path / "bad.xlsx"
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("xl/workbook.xml", "this is not xml <<<")
    p.write_bytes(buf.getvalue())
    with pytest.raises(ValueError, match=r"malformed xlsx \(unreadable xl/workbook.xml\)"):
        extract_ir(str(p))


def test_malformed_sheet_raises_document_error(tmp_path: Path) -> None:
    p = _control(tmp_path)
    # corrupt one worksheet part in place (workbook + rels stay valid)
    with zipfile.ZipFile(p) as zf:
        payload = {n: zf.read(n) for n in zf.namelist()}
    payload["xl/worksheets/sheet1.xml"] = b"<worksheet><sheetData>"
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        for n, data in payload.items():
            zf.writestr(n, data)
    p.write_bytes(buf.getvalue())
    with pytest.raises(ValueError, match=re.escape("unreadable xl/worksheets/sheet1.xml")):
        extract_ir(str(p))


def test_bad_zip_raises_document_error(tmp_path: Path) -> None:
    p = tmp_path / "fake.xlsx"
    p.write_bytes(b"PK\x03\x04" + b"\x00" * 16)
    with pytest.raises(ValueError, match="bad zip"):
        extract_ir(str(p))


# ---------------------------------------------------------------------------
# IR round-trip, renderers and the facade/CLI doors
# ---------------------------------------------------------------------------


def test_xlsx_ir_round_trip(tmp_path: Path) -> None:
    ir = extract_ir(str(_control(tmp_path)))
    assert from_dict(to_dict(ir)) == ir


def test_renderers_flow_through_untouched(tmp_path: Path) -> None:
    ir = extract_ir(str(_control(tmp_path)))
    md = render_markdown(ir)
    assert "## Смета" in md and "## Лист2" in md
    assert "Рабочая преамбула к смете" in md and "Второй лист" in md
    payloads = [json.loads(ln) for ln in render_chunks(ir).splitlines() if ln.strip()]
    assert payloads
    assert all(p["embed"].strip() for p in payloads)
    assert all(p["metadata"]["src_tool"] == "xlsx_adapter" for p in payloads)
    joined = "\n".join(p["embed"] for p in payloads)
    assert "Цемент" in joined and "Второй лист" in joined


def test_convert_end_to_end(tmp_path: Path) -> None:
    md = convert(str(_control(tmp_path)))
    assert "## Смета" in md and "Второй лист" in md


def test_process_pdf_pure_run_writes_nothing(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)
    xlsx = _control(tmp_path)
    ir, report = process_pdf(str(xlsx))
    assert report.pages_total == 2
    assert report.pages_native == 2
    assert report.pages_failed == 0
    assert ir.metadata.source_format == "xlsx"
    assert ir.pages[0].images == []
    assert list(tmp_path.iterdir()) == [xlsx]


def test_cli_full_run_writes_three_renderers(tmp_path: Path, monkeypatch) -> None:
    from pdf_router import cli

    monkeypatch.chdir(tmp_path)
    xlsx = _control(tmp_path, name="смета.xlsx")
    out = tmp_path / "out"
    code = cli.main(["--out", str(out), str(xlsx)])
    assert code == 0
    assert (out / "смета.md").is_file()
    assert (out / "смета.chunks.jsonl").is_file()
    ir_json = json.loads((out / "смета.ir.json").read_text(encoding="utf-8"))
    assert ir_json["metadata"]["source_format"] == "xlsx"
    assert ir_json["ir_version"] == "0.6"


def test_xlsx_info_matches_ir(tmp_path: Path) -> None:
    xlsx = _control(tmp_path)
    info = xlsx_info(str(xlsx))
    ir = extract_ir(str(xlsx))
    assert info == {
        "source_format": "xlsx",
        "pdf_type": "text_based",
        "page_count": 2,
        "confidence": 1.0,
        "title": "Контрольный файл",
        "tables": 2,
        "images": 0,
    }
    assert info["tables"] == sum(
        isinstance(b, TableBlock) for p in ir.pages for b in p.blocks
    )


# ---------------------------------------------------------------------------
# Golden anchors (TKT-OI-07): corpus workbooks, exact counts, budgets
# ---------------------------------------------------------------------------

LSR_VOLUME = Path(
    "/home/scanum/kotel-zamechanna/archive/ВЫДАЧА_БМК Опухлики"
    "/ПД ред. формат/11. СМ ред. формат/ЛСР.xlsx"
)
SR2_VOLUME = Path(
    "/home/scanum/kotel-zamechanna/archive/ВЫДАЧА_БМК Горького"
    "/ПД ред. формат/11. СМ ред. формат/4. СР-2 (утилизация).xlsx"
)
SMALLEST_VOLUME = Path(
    "/home/scanum/kotel-zamechanna/examples/_Архив замечаний"
    "/Локальные замечания 26.06.2025 РЗ.xlsx"
)


def _corpus(path: Path) -> str:
    if not path.exists():
        pytest.skip(f"golden corpus anchor not available: {path}")
    return str(path)


def test_golden_lsr_structure() -> None:
    """ЛСР: 10 sheets, 878 tables, no staircase, merge invariants, fast.

    Width <= 19 kills the hidden far-region duplicate (research measured
    the staircase at col 236); "№ п/п" x3 = one vertical merge repeated
    down (rows 35-37); the signature caption stays one cell per line.
    Budget: measured 3.5-4.7 s warm on the dev box (first run ~7 s cold;
    machine load can double wall time) — the bar catches structural
    regressions, not scheduler noise.
    """
    path = _corpus(LSR_VOLUME)
    t0 = time.perf_counter()
    ir = extract_ir(path)
    elapsed = time.perf_counter() - t0
    assert ir.metadata.page_count == 10
    assert ir.metadata.pages_with_tables == list(range(1, 11))
    tables = [b for p in ir.pages for b in p.blocks if isinstance(b, TableBlock)]
    heads = [b for p in ir.pages for b in p.blocks if isinstance(b, HeadingBlock)]
    assert len(tables) == 878
    assert len(heads) == 10
    assert all(t.rows and all(len(r) == len(t.rows[0]) for r in t.rows) for t in tables)
    assert max(len(t.rows[0]) for t in tables) <= 19  # staircase dies
    for p in ir.pages:
        assert p.markdown.startswith("## ")
    # vertical merge A35:A37 repeats the anchor value down exactly three times
    assert ir.pages[0].markdown.count("№ п/п") == 3
    # horizontal merge: the caption never repeats inside one rendered line
    caption = "[должность, подпись (инициалы, фамилия)]"
    for line in ir.pages[0].markdown.splitlines():
        assert line.count(caption) <= 1
    assert elapsed < 10.0
    assert from_dict(to_dict(ir)) == ir


def test_golden_lsr_chunks_nonempty() -> None:
    path = _corpus(LSR_VOLUME)
    ir = extract_ir(path)
    lines = [ln for ln in render_chunks(ir).splitlines() if ln.strip()]
    assert lines
    payloads = [json.loads(ln) for ln in lines]
    assert all(p["embed"].strip() for p in payloads)


def test_golden_lsr_info_typing() -> None:
    path = _corpus(LSR_VOLUME)
    info = xlsx_info(path)
    assert info["source_format"] == "xlsx"
    assert info["pdf_type"] == "text_based"
    assert info["confidence"] == 1.0
    assert info["page_count"] == 10
    assert info["tables"] == 878
    assert info["images"] == 0


def test_golden_sr2_formula_cache() -> None:
    """СР-2: 16 cached formulas publish their values, repr-pinned:
    B10 == D10 == 40.20618 (raw 40.206180000000003), F10 keeps its
    round-trip repr, no formula text leaks into the markdown."""
    path = _corpus(SR2_VOLUME)
    t0 = time.perf_counter()
    ir = extract_ir(path)
    elapsed = time.perf_counter() - t0
    assert ir.metadata.page_count == 1
    assert ir.metadata.pages_with_tables == [1]
    md = ir.pages[0].markdown
    assert md.count("40.20618") == 2  # B10 and D10, repr-cleaned
    assert "65397.362079000006" in md
    assert "15.086" in md
    assert "<f>" not in md and "=СУММ" not in md
    assert elapsed < 5.0
    assert from_dict(to_dict(ir)) == ir


def test_golden_smallest_header_row() -> None:
    """Smallest xlsx (10 KB): 1 sheet, 1 table, header = row 1."""
    path = _corpus(SMALLEST_VOLUME)
    t0 = time.perf_counter()
    ir = extract_ir(path)
    elapsed = time.perf_counter() - t0
    assert ir.metadata.page_count == 1
    tables = [b for b in ir.pages[0].blocks if isinstance(b, TableBlock)]
    assert len(tables) == 1
    assert tables[0].header_rows == 1
    assert tables[0].rows[0][1] == "Номер замечания в проекте"
    assert "29.6" in ir.pages[0].markdown
    assert elapsed < 5.0
