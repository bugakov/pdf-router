"""Chunks renderer quality bar (map v0.7, TKT-RC-07) -- three tiers.

Fast tier: synthetic IR, fully deterministic -- envelope contract, caption
demotion, walk-back across page boundaries, row-split invariants, KV form
of the table embed, pipe-table subtraction (native exact / OCR any-pipe /
no-block degradation), summary + embed_context toggles, doc_id identity,
per-page table ids, determinism. A shared validator (``_validate``) checks
every emitted line; the slow tiers reuse it as the smoke gate.

Middle tier (slow): live lineless engine on the committed Рыбы p.14 crop --
the chunk contract must hold on a real recovered grid.

Slow tier (slow): corpus anchors -- Рыбы p.14 and Генетика p.34 through the
real OCR+structure path, and a native text_based journal (one photo page may
route to OCR) through process_pdf. Rendering must complete, every chunk must
pass the validator; content asserts are fuzzy (scan noise, per the ST-05 bar).
"""

from __future__ import annotations

import json
import re
from difflib import SequenceMatcher
from pathlib import Path

import pytest

from pdf_router.config import RendererConfig
from pdf_router.extract_images import doc_id_from_path
from pdf_router.ir import (
    IR,
    HeadingBlock,
    Metadata,
    PageResult,
    SrcTool,
    TableBlock,
)
from pdf_router.renderers import chunks as chunks_renderer

# Corpus anchors resolve by numeric glob (same discipline as
# tests/test_structure.py -- keeps paths immune to transliteration drift).
CORPUS_DIR = Path("/home/scanum/vbrdb/orchestrator/downloads")
RYBY = next(iter(sorted(CORPUS_DIR.glob("*11138.pdf"))), None)
GENETIKA = next(iter(sorted(CORPUS_DIR.glob("*11188.pdf"))), None)
JOURNAL = next(iter(sorted(CORPUS_DIR.glob("*9716.pdf"))), None)

FIXTURE = Path(__file__).parent / "fixtures" / "structure" / "ryby_p14_table.png"

HEADER_WORD = "Показатель"
YOUNG_WORD = "молоди"


def _fuzzy(cell: str, word: str, floor: float = 0.80) -> bool:
    """ST-05 bar: fuzzy-substring assert (OCR-noise tolerant)."""
    if word.lower() in cell.lower():
        return True
    return SequenceMatcher(None, cell.lower(), word.lower()).ratio() >= floor


# ---------------------------------------------------------------------------
# Shared validator: fast-tier invariants over emitted JSONL
# ---------------------------------------------------------------------------

_ENVELOPE_KEYS = {"chunk_id", "chunk_version", "type", "embed", "markdown", "metadata", "images"}
_META_KEYS = {"doc_id", "doc_title", "source_format", "page", "section", "src_tool", "error"}
_TABLE_KEYS = {"table_id", "part", "parts", "n_rows", "n_cols", "header", "source"}


def _validate(jsonl: str) -> list[str]:
    """Return a list of contract violations (empty == clean)."""
    problems: list[str] = []
    seen: set[str] = set()
    groups: dict[str, set[int]] = {}
    for ln in jsonl.splitlines():
        if not ln.strip():
            continue
        c = json.loads(ln)
        cid = str(c.get("chunk_id", "<no-id>"))
        for key in sorted(_ENVELOPE_KEYS - set(c)):
            problems.append(f"{cid}: missing envelope key {key!r}")
        if c.get("chunk_version") != 2:
            problems.append(f"{cid}: chunk_version != 2")
        if c.get("type") not in {"text", "table", "table_part"}:
            problems.append(f"{cid}: bad type {c.get('type')!r}")
        if cid in seen:
            problems.append(f"{cid}: duplicate chunk_id")
        seen.add(cid)
        if not str(c.get("embed", "")).strip():
            problems.append(f"{cid}: empty embed")
        if not str(c.get("markdown", "")).strip():
            problems.append(f"{cid}: empty markdown")
        m = c.get("metadata")
        if not isinstance(m, dict):
            problems.append(f"{cid}: metadata is not an object")
            continue
        for key in sorted(_META_KEYS - set(m)):
            problems.append(f"{cid}: missing metadata key {key!r}")
        doc_id = m.get("doc_id")
        if doc_id is not None and not re.fullmatch(r"[0-9a-f]{12}", str(doc_id)):
            problems.append(f"{cid}: doc_id not 12-hex: {doc_id!r}")
        if c.get("type") in {"table", "table_part"}:
            t = m.get("table")
            if not isinstance(t, dict):
                problems.append(f"{cid}: metadata.table is missing")
                continue
            for key in sorted(_TABLE_KEYS - set(t)):
                problems.append(f"{cid}: missing table key {key!r}")
            if c["type"] == "table_part":
                tid, part, parts = t.get("table_id"), t.get("part"), t.get("parts")
                if not (isinstance(part, int) and isinstance(parts, int) and 1 <= part <= parts):
                    problems.append(f"{cid}: bad part numbering {part!r}/{parts!r}")
                elif tid:
                    groups.setdefault(str(tid), set()).add(part)
    for tid, part_set in groups.items():
        need = set(range(1, max(part_set) + 1))
        if part_set != need:
            problems.append(f"{tid}: part numbers {sorted(part_set)} not contiguous 1..N")
    return problems


# ---------------------------------------------------------------------------
# Synthetic IR helpers (fast tier)
# ---------------------------------------------------------------------------


def _page(
    idx: int,
    markdown: str = "",
    *,
    tool: SrcTool = SrcTool.PDF_INSPECTOR,
    error: str | None = None,
    blocks: list | None = None,
) -> PageResult:
    return PageResult(page=idx, markdown=markdown, src_tool=tool, error=error, blocks=blocks or [])


def _pipe(rows: list[list[str]]) -> str:
    """Pipe markdown the way the adapters emit it (renderer-normalizable)."""
    width = max(len(r) for r in rows)
    norm = [r + [""] * (width - len(r)) for r in rows]
    lines = ["| " + " | ".join(r) + " |" for r in norm]
    lines.insert(1, "| " + " | ".join("---" for _ in range(width)) + " |")
    return "\n".join(lines)


def _table_page(idx: int = 0) -> PageResult:
    """Caption-row table (the Рыбы p.14 shape): row 0 is a caption, the real
    header is row 1, source tags it as an OCR lineless recovery."""
    rows = [
        ["Таблица 2. Экологические показатели"],
        ["Признак", "Значение"],
        ["длина", "10"],
        ["масса", "200"],
    ]
    tb = TableBlock(markdown=_pipe(rows), rows=rows, header_rows=1, source="ocr_table_lineless")
    return _page(idx, "", tool=SrcTool.RAPIDOCR, blocks=[tb])


def _chunks(out: str) -> list[dict]:
    return [json.loads(ln) for ln in out.splitlines() if ln.strip()]


# ---------------------------------------------------------------------------
# Fast tier
# ---------------------------------------------------------------------------


def test_text_chunk_envelope_and_metadata() -> None:
    ir = IR(
        metadata=Metadata(source_format="pdf", title="Отчёт", source_path="/tmp/Отчёт.pdf"),
        pages=[_page(0, "## Раздел\n\nтело абзаца")],
    )
    out = chunks_renderer.render(ir, config=RendererConfig())
    chunks = _chunks(out)
    assert chunks, "text page must yield chunks"
    c = chunks[0]
    assert c["type"] == "text"
    assert set(c) == _ENVELOPE_KEYS
    assert set(c["metadata"]) == _META_KEYS
    assert c["metadata"]["doc_id"] == doc_id_from_path("/tmp/Отчёт.pdf")
    assert c["metadata"]["doc_title"] == "Отчёт"
    assert c["metadata"]["section"] == "Раздел"
    assert c["metadata"]["src_tool"] == "pdf_inspector"
    assert c["metadata"]["error"] is None
    assert c["images"] == []
    assert _validate(out) == []


def test_caption_demotion_keys_from_second_row() -> None:
    out = chunks_renderer.render(IR(pages=[_table_page()]), config=RendererConfig())
    chunks = _chunks(out)
    assert [c["type"] for c in chunks] == ["table"]
    c = chunks[0]
    # keys come from rows[1], never from the caption row
    assert c["metadata"]["table"]["header"] == ["Признак", "Значение"]
    # caption rides the embed context; data rides as KV pairs
    assert "Таблица 2. Экологические показатели" in c["embed"]
    assert "Признак: длина, Значение: 10" in c["embed"]
    assert "Признак: масса, Значение: 200" in c["embed"]
    # facts summary is the default engine
    assert "Сводка:" in c["embed"]
    # markdown field: generation-ready hybrid (title + grid + summary)
    assert c["markdown"].startswith("# ")
    assert "| Признак | Значение |" in c["markdown"]
    assert "| --- | --- |" in c["markdown"]
    assert _validate(out) == []


def test_regular_header_no_demotion() -> None:
    rows = [["Квартал", "Выручка"], ["Q1", "150"], ["Q2", "180"]]
    tb = TableBlock(markdown=_pipe(rows), rows=rows, header_rows=1)
    ir = IR(pages=[_page(0, "", blocks=[tb])])
    out = chunks_renderer.render(ir, config=RendererConfig())
    c = _chunks(out)[0]
    assert c["metadata"]["table"]["header"] == ["Квартал", "Выручка"]
    assert c["markdown"].startswith(tb.markdown)
    assert "Квартал: Q1, Выручка: 150" in c["embed"]
    assert _validate(out) == []


def test_walk_back_across_page_boundary() -> None:
    rows = [["A", "B"], ["1", "2"]]
    tb = TableBlock(markdown=_pipe(rows), rows=rows, header_rows=1)
    pages = [
        _page(
            0,
            "## Миграция сиговых\n\nтекст",
            blocks=[HeadingBlock(level=2, text="Миграция сиговых", source="structure_tree")],
        ),
        _page(1, "", blocks=[tb]),
    ]
    ir = IR(metadata=Metadata(source_format="pdf", title="Том"), pages=pages)
    out = chunks_renderer.render(ir, config=RendererConfig())
    table_chunks = [c for c in _chunks(out) if c["type"].startswith("table")]
    assert len(table_chunks) == 1
    assert table_chunks[0]["metadata"]["section"] == "Миграция сиговых"
    # embed preamble: doc_title › deepest heading (embed_context default on)
    assert table_chunks[0]["embed"].startswith("Том › Миграция сиговых")
    assert _validate(out) == []


def test_row_split_repeats_header_and_numbers_parts() -> None:
    rows = [["Таблица 5. Длинная"], ["Год", "Улов, т"]] + [
        [str(y), str(1000 + y)] for y in range(2000, 2020)
    ]
    tb = TableBlock(markdown=_pipe(rows), rows=rows, header_rows=1)
    ir = IR(pages=[_page(3, "", blocks=[tb])])
    cfg = RendererConfig()
    cfg.chunks.max_tokens = 60
    cfg.chunks.summary = "none"
    cfg.chunks.embed_context = False
    out = chunks_renderer.render(ir, config=cfg)
    chunks = _chunks(out)
    assert len(chunks) >= 3, "the 20-row table must split"
    assert {c["type"] for c in chunks} == {"table_part"}
    n = chunks[0]["metadata"]["table"]["parts"]
    assert [c["metadata"]["table"]["part"] for c in chunks] == list(range(1, n + 1))
    assert {c["metadata"]["table"]["table_id"] for c in chunks} == {"3-t1"}
    for c in chunks:
        # every part repeats the caption context and the header band
        assert "Таблица 5. Длинная" in c["embed"]
        assert c["embed"].splitlines()[1].startswith("Год: ")
        assert "| Год | Улов, т |" in c["markdown"]
        assert "| --- | --- |" in c["markdown"]
        # budget respected (heuristic token = 4 chars)
        assert len(c["embed"]) <= 60 * 4 + 64
    assert _validate(out) == []


def test_summary_facts_and_none() -> None:
    rows = [["Месяц", "Объём"], ["Янв", "10"], ["Фев", "20"]]
    tb = TableBlock(markdown=_pipe(rows), rows=rows, header_rows=1)
    ir = IR(pages=[_page(0, "", blocks=[tb])])
    cfg = RendererConfig()
    out_facts = chunks_renderer.render(ir, config=cfg)
    assert "Сводка: 2 кол., 2 строк данных." in out_facts
    assert "Объём: мин 10, макс 20, сумма 30." in out_facts
    cfg.chunks.summary = "none"
    out_none = chunks_renderer.render(ir, config=cfg)
    assert "Сводка" not in out_none
    # with summary=none the whole-table markdown field is the grid verbatim
    assert _chunks(out_none)[0]["markdown"] == tb.markdown


def test_embed_context_toggle_text_chunks() -> None:
    ir = IR(
        metadata=Metadata(source_format="pdf", title="Отчёт"),
        pages=[_page(0, "## Раздел А\n\nтело документа")],
    )
    cfg = RendererConfig()
    out_on = chunks_renderer.render(ir, config=cfg)
    on_chunks = _chunks(out_on)
    assert all(c["embed"].startswith("Отчёт › Раздел А\n") for c in on_chunks)
    assert any("тело документа" in c["embed"] for c in on_chunks)
    cfg.chunks.embed_context = False
    out_off = chunks_renderer.render(ir, config=cfg)
    off_chunks = _chunks(out_off)
    assert any(c["embed"] == "тело документа" for c in off_chunks)
    assert all(not c["embed"].startswith("Отчёт") for c in off_chunks)


def test_subtraction_native_exact_match() -> None:
    rows = [["A", "B"], ["1", "2"]]
    tb = TableBlock(markdown=_pipe(rows), rows=rows, header_rows=1)
    md = "Вводный абзац.\n\n" + _pipe(rows) + "\n\nХвост."
    ir = IR(pages=[_page(0, md, blocks=[tb])])
    out = chunks_renderer.render(ir, config=RendererConfig())
    text = [c for c in _chunks(out) if c["type"] == "text"]
    assert text
    joined = "\n".join(c["embed"] for c in text)
    assert "| A | B |" not in joined, "blocked pipe table must not double into the text"
    assert "| 1 | 2 |" not in joined
    assert "Вводный абзац." in joined and "Хвост." in joined
    assert any(c["type"] == "table" for c in _chunks(out))
    assert _validate(out) == []


def test_subtraction_ocr_drops_any_pipe_region() -> None:
    # The OCR markdown twin is the scrambled flattening of the block --
    # textually DIFFERENT from TableBlock.markdown, yet it must go.
    scrambled = _pipe([["Бассейн", "Показатель"], ["Дон", "12"]])
    rows = [["Показатель", "Бассейн"], ["12", "Дон"]]
    tb = TableBlock(markdown=_pipe(rows), rows=rows, header_rows=1, source="ocr_table_lineless")
    ir = IR(pages=[_page(0, "Листовой текст.\n\n" + scrambled, tool=SrcTool.RAPIDOCR, blocks=[tb])])
    out = chunks_renderer.render(ir, config=RendererConfig())
    text = [c for c in _chunks(out) if c["type"] == "text"]
    joined = "\n".join(c["embed"] for c in text)
    assert "| " not in joined, "scrambled pipe twin must not leak into text chunks"
    assert "Листовой текст." in joined
    assert any(c["type"] == "table" for c in _chunks(out))


def test_pipe_table_without_block_stays_in_text() -> None:
    md = "Абзац.\n\n" + _pipe([["A", "B"], ["1", "2"]])
    ir = IR(pages=[_page(0, md)])  # no blocks: old IR / failed structure pass
    out = chunks_renderer.render(ir, config=RendererConfig())
    text = [c for c in _chunks(out) if c["type"] == "text"]
    joined = "\n".join(c["embed"] for c in text)
    assert "| A | B |" in joined, "unblocked pipe table must degrade into the text, not vanish"
    assert _validate(out) == []


def test_table_only_page_yields_no_text_chunk() -> None:
    rows = [["A", "B"], ["1", "2"]]
    tb = TableBlock(markdown=_pipe(rows), rows=rows, header_rows=1)
    ir = IR(pages=[_page(0, _pipe(rows), blocks=[tb])])
    out = chunks_renderer.render(ir, config=RendererConfig())
    assert [c["type"] for c in _chunks(out)] == ["table"]


def test_table_ids_numbered_per_page() -> None:
    rows = [["A", "B"], ["1", "2"]]
    t1 = TableBlock(markdown=_pipe(rows), rows=rows, header_rows=1)
    t2 = TableBlock(markdown=_pipe(rows), rows=rows, header_rows=1)
    ir = IR(pages=[_page(7, _pipe(rows), blocks=[t1, t2])])
    out = chunks_renderer.render(ir, config=RendererConfig())
    chunks = _chunks(out)
    assert [c["metadata"]["table"]["table_id"] for c in chunks] == ["7-t1", "7-t2"]
    assert [c["chunk_id"] for c in chunks] == ["7-t1", "7-t2"]


def test_doc_id_identity_and_title_fallback() -> None:
    path = "/data/Том 2.pdf"
    ir = IR(metadata=Metadata(source_format="pdf", source_path=path), pages=[_page(0, "текст")])
    out = chunks_renderer.render(ir, config=RendererConfig())
    c = _chunks(out)[0]
    assert c["metadata"]["doc_id"] == doc_id_from_path(path)
    assert re.fullmatch(r"[0-9a-f]{12}", c["metadata"]["doc_id"])
    assert c["metadata"]["doc_title"] == "Том 2.pdf"  # basename fallback


def test_page_error_and_src_tool_ride_through() -> None:
    ir = IR(pages=[_page(0, "текст", tool=SrcTool.TESSERACT, error="rapidocr_failed: boom")])
    out = chunks_renderer.render(ir, config=RendererConfig())
    c = _chunks(out)[0]
    assert c["metadata"]["error"] == "rapidocr_failed: boom"
    assert c["metadata"]["src_tool"] == "tesseract"


def test_validator_clean_on_mixed_document() -> None:
    ir = IR(
        metadata=Metadata(source_format="pdf", title="Сводный", source_path="/x/y.pdf"),
        pages=[
            _page(
                0,
                "## Вводная\n\nабзац один\n\nабзац два",
                blocks=[HeadingBlock(level=2, text="Вводная", source="structure_tree")],
            ),
            _table_page(1),
        ],
    )
    out = chunks_renderer.render(ir, config=RendererConfig())
    assert _validate(out) == []
    assert {c["type"] for c in _chunks(out)} == {"text", "table"}


def test_determinism_same_ir_byte_identical() -> None:
    ir = IR(pages=[_table_page(), _page(1, "## Голова\n\nхвост")])
    a = chunks_renderer.render(ir, config=RendererConfig())
    b = chunks_renderer.render(ir, config=RendererConfig())
    assert a == b


# ---------------------------------------------------------------------------
# Middle tier (slow) -- live lineless on the committed crop fixture
# ---------------------------------------------------------------------------


def _norm_ws(text: str) -> str:
    return "\n".join(ln.strip() for ln in text.strip().splitlines())


@pytest.mark.slow
def test_crop_fixture_chunks_contract() -> None:
    if not FIXTURE.exists():
        pytest.skip("crop fixture not committed")
    pytest.importorskip("lineless_table_rec")

    import numpy as np
    from PIL import Image

    from pdf_router.config import RouterConfig
    from pdf_router.ocr import _rapidocr_get_engine, _rapidocr_triples
    from pdf_router.structure import structure_page

    img = np.asarray(Image.open(FIXTURE).convert("RGB"))
    rcfg = RouterConfig()
    triples = _rapidocr_triples(_rapidocr_get_engine(rcfg)(img))
    assert len(triples) >= 30, "crop must OCR to a realistic number of lines"
    blocks = structure_page(img, triples, rcfg)
    tables = [b for b in blocks if isinstance(b, TableBlock)]
    assert tables, "lineless must find the crop's table"

    # The crop carries header + body rows (row 0 IS the header here).
    page = PageResult(page=0, markdown=tables[0].markdown, src_tool=SrcTool.RAPIDOCR, blocks=blocks)
    cfg = RendererConfig()
    out = chunks_renderer.render(
        IR(metadata=Metadata(source_format="pdf"), pages=[page]), config=cfg
    )
    assert _validate(out) == []
    table_chunks = [c for c in _chunks(out) if c["type"] in {"table", "table_part"}]
    assert table_chunks
    tc = table_chunks[0]
    # header survives into the embed as KV keys (fuzzy: OCR noise)
    assert _fuzzy(" ".join(tc["metadata"]["table"]["header"]), HEADER_WORD)
    # markdown field is a valid pipe table
    assert tc["markdown"].lstrip().startswith("|")
    assert "| --- |" in tc["markdown"]
    # every part within the embed budget
    for c in table_chunks:
        assert len(c["embed"]) <= cfg.chunks.max_tokens * 4 + 256
    # provenance rides through
    assert tc["metadata"]["src_tool"] == "rapidocr"
    assert tc["metadata"]["table"]["source"] == "ocr_table_lineless"


# ---------------------------------------------------------------------------
# Slow tier -- corpus anchors (real OCR+structure; native journal, no OCR)
# ---------------------------------------------------------------------------


def _scan_page_blocks(path: Path, page_idx: int) -> list:
    """Full-page raster + rapidocr + structure pass (the ST-05 path)."""
    from pdf_router.config import RouterConfig
    from pdf_router.ocr import _rapidocr_get_engine, _rapidocr_triples
    from pdf_router.rasterize import page_raster_array
    from pdf_router.structure import structure_page

    cfg = RouterConfig()
    img = page_raster_array(path.as_posix(), page_idx, dpi=150)
    triples = _rapidocr_triples(_rapidocr_get_engine(cfg)(img))
    return structure_page(img, triples, cfg)


def _page_from_blocks(idx: int, blocks: list) -> PageResult:
    """Rebuild the page material from structure blocks: table markdown rides
    as the page markdown (subtraction then drops it from the text half --
    the exact shape the OCR pipeline produces)."""
    table_md = [b.markdown for b in blocks if isinstance(b, TableBlock)]
    return PageResult(
        page=idx,
        markdown="\n\n".join(table_md),
        src_tool=SrcTool.RAPIDOCR,
        blocks=blocks,
    )


@pytest.mark.slow
def test_anchor_ryby_p14_chunks() -> None:
    """Рыбы p.14 (1-indexed): the caption-row anchor through real OCR.
    Caption must demote, header must become KV keys, body must reach embed."""
    if RYBY is None:
        pytest.skip("corpus anchor not available")
    pytest.importorskip("lineless_table_rec")

    blocks = _scan_page_blocks(RYBY, 13)
    tables = [b for b in blocks if isinstance(b, TableBlock)]
    assert tables, "the p14 table must be recovered"
    out = chunks_renderer.render(
        IR(metadata=Metadata(source_format="pdf"), pages=[_page_from_blocks(13, blocks)]),
        config=RendererConfig(),
    )
    assert _validate(out) == [], "anchor chunks violate the contract"
    tc = next(c for c in _chunks(out) if c["type"] in {"table", "table_part"})
    # caption demoted from row 0 and riding the context (embed line 1)
    assert _fuzzy(tc["embed"].splitlines()[0], "Таблица")
    # header (row 1) became the KV keys, not the caption text
    assert _fuzzy(" ".join(tc["metadata"]["table"]["header"]), HEADER_WORD)
    # body cells survive into the embed as KV values: take the first
    # meaningful cell of the first non-empty body row (0=caption, 1=header,
    # 2 may be a spacer) and find it verbatim in the embed
    cell = next(
        (
            " ".join(c.split())
            for row in tables[0].rows[2:]
            for c in row
            if re.search(r"[А-Яа-яA-Za-z0-9]{4}", c)
        ),
        None,
    )
    assert cell, "a meaningful body cell must exist below header"
    assert cell in " ".join(tc["embed"].split()), (
        f"body cell {cell!r} must reach the embed as a KV value"
    )


@pytest.mark.slow
def test_anchor_genetika_p34_chunks() -> None:
    """Генетика p.34: the engine places a heading/caption band (rows 0..3)
    ahead of the real header; the contract must still hold and body words
    must reach the embed."""
    if GENETIKA is None:
        pytest.skip("corpus anchor not available")
    pytest.importorskip("lineless_table_rec")

    blocks = _scan_page_blocks(GENETIKA, 33)
    tables = [b for b in blocks if isinstance(b, TableBlock)]
    assert tables, "the p34 table must be recovered"
    out = chunks_renderer.render(
        IR(metadata=Metadata(source_format="pdf"), pages=[_page_from_blocks(33, blocks)]),
        config=RendererConfig(),
    )
    assert _validate(out) == [], "anchor chunks violate the contract"
    tc = next(c for c in _chunks(out) if c["type"] in {"table", "table_part"})
    assert _fuzzy(tc["embed"], YOUNG_WORD), "body words (молоди) must reach the embed"


@pytest.mark.slow
def test_native_journal_chunks_no_ocr() -> None:
    """Native text_based journal: process_pdf end-to-end, chunks from the
    native structure tree (one photo page may route to OCR — fine). The
    no-doubles invariant: a pipe table that has a TableBlock must not leak
    verbatim into text chunks."""
    if JOURNAL is None:
        pytest.skip("corpus anchor not available")

    from pdf_router.api import process_pdf

    ir, report = process_pdf(JOURNAL)
    assert report.pages_failed == 0
    assert report.pages_native >= report.pages_total - 1, "journal must stay native"
    assert report.pages_native >= 1, "native pages must exist"
    out = chunks_renderer.render(ir, config=RendererConfig())
    assert _validate(out) == [], "journal chunks violate the contract"
    # sanity: the journal has tables and they produced chunks
    table_chunks = [c for c in _chunks(out) if c["type"] in {"table", "table_part"}]
    assert table_chunks, "native tables must yield table chunks"
    # text chunks must not carry any blocked pipe table verbatim
    known = {_norm_ws(b.markdown) for p in ir.pages for b in p.blocks if isinstance(b, TableBlock)}
    for c in (c for c in _chunks(out) if c["type"] == "text"):
        for md in known:
            assert _norm_ws(md) not in c["markdown"], "blocked table leaked into text chunk"
