"""IR 0.2 golden anchors for structural blocks (tables, headings).

L1 anchor: the adapter must produce at least the expected block types on the
reference PDF. This is a coarse regression gate (any extraction failure on
the baseline surfaces here).

L2 anchor: a known-good substring in the parsed table content survives. This
catches header-row regression and markdown-table parsing drift.

L3 anchor: at least one TableBlock has a parsed ``rows`` 2-D list. This is
what DocLang / RAG / chunks renderers consume.

The test is split into ``test_structural_blocks_p5`` (fast) and
``test_structural_blocks_tkp1`` (slow: 40s on ТКР1).
"""

from __future__ import annotations

import pytest

from pdf_router.adapters.pdf_inspector import _parse_markdown_table, extract_ir
from pdf_router.ir import HeadingBlock, TableBlock


# --- p5.pdf: 1 page, 1 table (L1 + L2) ---------------------------------------


def test_structural_blocks_p5(p5_pdf) -> None:
    ir = extract_ir(str(p5_pdf))

    assert len(ir.pages) == 1
    page = ir.pages[0]
    tables = [b for b in page.blocks if isinstance(b, TableBlock)]
    headings = [b for b in page.blocks if isinstance(b, HeadingBlock)]

    # L1: at least one table, no headings (p5 has no structure tree)
    assert len(tables) == 1, f"expected 1 table on p5, got {len(tables)}"
    assert headings == [], f"expected no headings on p5, got {len(headings)}"

    # L2: golden anchor strings from p5 survive in the table markdown
    t = tables[0]
    assert "Обозначение" in t.markdown
    assert "ИЛО9.1" in t.markdown

    # L3: rows is a non-empty 2-D list of strings
    assert t.rows, "rows must be parsed"
    assert all(isinstance(row, list) for row in t.rows)
    assert all(isinstance(cell, str) for row in t.rows for cell in row)
    assert t.header_rows >= 1


# --- ТКР1: 140 pages, 123 tables, 2223 headings (L1 only, slow) --------------


@pytest.mark.slow
def test_structural_blocks_tkp1() -> None:
    """ТКР1 is the structural baseline for the project-document persona.

    The adapter should report at least one TableBlock and one HeadingBlock.
    Numbers are a *floor*, not an exact count, so this catches gross regressions
    (e.g. structure_elements not consumed, font-size fallback broken) without
    breaking on pdf-inspector 1.18+ if extraction broadens.
    """
    tkp1 = "/home/scanum/kotel-zamechanna/Путейцев Обь/0. ЗАГРУЖЕННОЕ В ГЭ/3.1_00909.2025.04-ТКР1.pdf"
    import os
    if not os.path.exists(tkp1):
        pytest.skip(f"baseline PDF not available: {tkp1}")

    ir = extract_ir(tkp1)
    all_tables = [b for pg in ir.pages for b in pg.blocks if isinstance(b, TableBlock)]
    all_headings = [b for pg in ir.pages for b in pg.blocks if isinstance(b, HeadingBlock)]

    assert len(all_tables) >= 50, f"expected >=50 tables on ТКР1, got {len(all_tables)}"
    assert len(all_headings) >= 100, f"expected >=100 headings on ТКР1, got {len(all_headings)}"

    # L3: at least one table has parsed rows
    assert any(t.rows for t in all_tables), "at least one TableBlock must have parsed rows"

    # L2: at least one heading text is non-empty and non-numeric-only
    real = [h for h in all_headings if h.text and not h.text.strip().isdigit()]
    assert real, "expected at least one non-numeric heading (excludes page-number false positives)"


# --- Table parser: header-row heuristic (regression: duplicated header line) --


def test_parse_markdown_table_single_header() -> None:
    """Plain 1-line markdown header: header + body rows, no duplication."""
    rows, hdr = _parse_markdown_table(
        "| A | B |\n|---|---|\n| 1 | 2 |\n| 3 | 4 |"
    )
    assert hdr == 1
    assert rows == [["A", "B"], ["1", "2"], ["3", "4"]]


def test_parse_markdown_table_two_line_header_no_duplicate() -> None:
    """Two-line wrapped header: second line counted, but NOT duplicated.

    Regression guard for the hardening pass: the old implementation fed the
    second header line into ``rows`` twice (loop + ``rows.insert(0, ...)``).
    """
    rows, hdr = _parse_markdown_table(
        "| A | B |\n|---|---|\n| Голова | Шапка |\n| 1 | 2 |\n| 3 | 4 |"
    )
    assert hdr == 2
    assert rows == [["A", "B"], ["Голова", "Шапка"], ["1", "2"], ["3", "4"]]


def test_parse_markdown_table_text_body_not_header() -> None:
    """Conservative guard: a lone body row never triggers the 2-line heuristic.

    The heuristic (a text-only row right after the separator = wrapped header)
    fires only when at least two body rows follow the separator.
    """
    rows, hdr = _parse_markdown_table("| A | B |\n|---|---|\n| Голова | Шапка |")
    assert hdr == 1
    assert rows == [["A", "B"], ["Голова", "Шапка"]]
    # ...but with >= 2 body rows the documented heuristic fires (by design).
    rows2, hdr2 = _parse_markdown_table(
        "| A | B |\n|---|---|\n| Голова | Шапка |\n| 1 | 2 |"
    )
    assert hdr2 == 2
    assert rows2 == [["A", "B"], ["Голова", "Шапка"], ["1", "2"]]


def test_parse_markdown_table_rejects_garbage() -> None:
    """Non-table input returns ([], 0); separators never become rows."""
    assert _parse_markdown_table("plain text") == ([], 0)
    assert _parse_markdown_table("| A | B |\nno separator\n| 1 | 2 |") == ([], 0)
    rows, hdr = _parse_markdown_table("| A | B |\n|---|---|\n|---|---|\n| 1 | 2 |")
    assert hdr == 1
    assert rows == [["A", "B"], ["1", "2"]]


# --- IR round-trip with blocks ----------------------------------------------


def test_block_round_trip() -> None:
    """to_dict / from_dict must preserve all block fields including bbox."""
    from pdf_router.ir import BBox, IR, PageResult, TableBlock, from_dict, to_dict

    page = PageResult(
        page=0,
        markdown="|a|b|\n|---|---|\n|1|2|",
        blocks=[
            TableBlock(
                markdown="|a|b|\n|---|---|\n|1|2|",
                rows=[["a", "b"], ["1", "2"]],
                header_rows=1,
                bbox=BBox(0.0, 0.0, 100.0, 50.0),
            ),
        ],
    )
    ir = IR(pages=[page])
    d = to_dict(ir)
    assert d["ir_version"] == "0.5"
    # kind tag is preserved
    assert d["pages"][0]["blocks"][0]["kind"] == "TableBlock"
    # bbox is expanded
    assert d["pages"][0]["blocks"][0]["bbox"] == {
        "x0": 0.0, "y0": 0.0, "x1": 100.0, "y1": 50.0,
    }
    # round-trip
    ir2 = from_dict(d)
    assert ir2 == ir
