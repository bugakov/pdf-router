"""OCR structure pass (ADR-0005) -- three quality tiers (ST-05 bar).

Fast tier: stub table engine (deterministic, no models) -- IR shape,
provenance tags, heading heuristic levels, table-zone masking, failure
degradation contract.
Middle tier: live lineless engine on the committed p14 table crop.
Slow tier: corpus anchors (Ryby p.14, Genetika p.34) -- full-page OCR +
structure pass, fuzzy-substring cell asserts per the ST-05 quality bar.
"""

from __future__ import annotations

import time
from difflib import SequenceMatcher
from pathlib import Path
import json
import tempfile

import numpy as np
import pytest

import pdf_router.structure as st
from pdf_router.config import RouterConfig
from pdf_router.ir import IR_VERSION, BBox, HeadingBlock, TableBlock
from pdf_router.structure import (
    StructureError,
    blocks_from_glm,
    blocks_from_pipe_markdown,
    structure_page,
)

FIXTURE = Path(__file__).parent / "fixtures" / "structure" / "ryby_p14_table.png"

# ST-05 perf gate: structure pass <= 15 s/page (measured 7.3-7.7 s idle).
STRUCT_BUDGET_S = 15.0

# Corpus anchors resolve by numeric glob -- keeps this file pure ASCII and
# immune to transliteration drift in the long Cyrillic filenames.
CORPUS_DIR = Path("/home/scanum/vbrdb/orchestrator/downloads")
RYBY = next(iter(sorted(CORPUS_DIR.glob("*11138.pdf"))), None)
GENETIKA = next(iter(sorted(CORPUS_DIR.glob("*11188.pdf"))), None)

# Assert words as escapes so the file stays ASCII (the editor write path
# mangled Cyrillic once already).
HEADER_WORD = "\u041f\u043e\u043a\u0430\u0437\u0430\u0442\u0435\u043b\u044c"  # "Indicator" header token (Ryby p14)
TRAIT_WORD = "\u041f\u0440\u0438\u0437\u043d\u0430\u043a\u0438"  # "Traits" first-column header (Genetika p34)
YOUNG_WORD = "\u043c\u043e\u043b\u043e\u0434\u0438"  # "young (fish)" body cell (Genetika p34)
PELAGIC_WORD = "\u041f\u0435\u043b\u0430\u0433\u0438\u0447\u0435\u0441\u043a\u0438\u0435"  # body cell word


# ---------------------------------------------------------------------------
# Stub engine + helpers (fast tier runs fully deterministic)
# ---------------------------------------------------------------------------


class _StubOut:
    """Engine output contract: pred_html + cell_bboxes + logic_points."""

    def __init__(self, pred_html, cell_bboxes, logic_points):
        self.pred_html = pred_html
        self.cell_bboxes = cell_bboxes
        self.logic_points = logic_points


class _StubEngine:
    """Deterministic 2x2 grid ('a b / c d') occupying y 90..230."""

    def __call__(self, raster, ocr_result=None):
        return _StubOut(
            pred_html="<table><tr><td>a</td><td>b</td></tr>"
            "<tr><td>c</td><td>d</td></tr></table>",
            cell_bboxes=[[100.0, 90.0, 500.0, 230.0]],
            # per-cell [row_start, row_end, col_start, col_end], ends inclusive
            logic_points=[[0, 0, 0, 0], [0, 0, 1, 1], [1, 1, 0, 0], [1, 1, 1, 1]],
        )


class _BoomEngine:
    def __call__(self, raster, ocr_result=None):
        raise RuntimeError("onnx session exploded")


@pytest.fixture
def stub_engine(monkeypatch):
    monkeypatch.setattr(st, "_get_table_engine", lambda kind: _StubEngine())
    monkeypatch.setattr(st, "_has_table_in_layout", lambda r: True)


@pytest.fixture
def boom_engine(monkeypatch):
    monkeypatch.setattr(st, "_get_table_engine", lambda kind: _BoomEngine())
    monkeypatch.setattr(st, "_has_table_in_layout", lambda r: True)


def _triple(text: str, x0: float, y0: float, x1: float, y1: float) -> tuple:
    """Box(4,2) quad + text + score -- the rapidocr/table-engine contract."""
    quad = np.array([[x0, y0], [x1, y0], [x1, y1], [x0, y1]], dtype=float)
    return (quad, text, 0.97)


def _fuzzy(cell: str, word: str, floor: float = 0.80) -> bool:
    """ST-05 bar: fuzzy-substring cell assert (OCR-noise tolerant)."""
    if word.lower() in cell.lower():
        return True
    return SequenceMatcher(None, cell.lower(), word.lower()).ratio() >= floor


def _wired_cfg() -> RouterConfig:
    cfg = RouterConfig()
    cfg.structure.table_engine = "wired"  # dataclass verified mutable
    return cfg


_EMPTY_RASTER = np.zeros((10, 10, 3), dtype=np.uint8)


# ---------------------------------------------------------------------------
# Fast tier -- pure paths: pipe fallback, glm layout, IR version
# ---------------------------------------------------------------------------


def test_pipe_fallback_tags_provenance() -> None:
    blocks = blocks_from_pipe_markdown("| A | B |\n| --- | --- |\n| 1 | 2 |\n")
    assert len(blocks) == 1
    tb = blocks[0]
    assert isinstance(tb, TableBlock)
    assert tb.source == "ocr_pipe_fallback"  # native 'markdown_pipe' retagged
    assert tb.rows == [["A", "B"], ["1", "2"]]


def test_pipe_fallback_empty_and_garbage() -> None:
    assert blocks_from_pipe_markdown("") == []
    assert blocks_from_pipe_markdown("just plain text\nno tables here") == []


def test_blocks_from_glm_title_and_table() -> None:
    # layout_details is a list of per-page block lists (glm contract).
    layout = [
        [
            {
                "index": 2,
                "label": "table",
                "native_label": "table",
                "bbox_2d": [10, 20, 300, 400],
                "content": "<table><tr><th>H1</th><th>H2</th></tr>"
                "<tr><td>a</td><td>b</td></tr></table>",
            },
            {
                "index": 1,
                "label": "text",
                "native_label": "vision_title",
                "bbox_2d": [0, 0, 200, 30],
                "content": "# Intro heading",
            },
        ]
    ]
    blocks = blocks_from_glm(layout)
    assert [type(b).__name__ for b in blocks] == ["HeadingBlock", "TableBlock"]
    h, t = blocks
    assert isinstance(h, HeadingBlock) and h.level == 1  # glm gives no deeper levels
    assert h.text == "Intro heading" and h.source == "ocr_glm_layout"
    assert isinstance(t, TableBlock) and t.source == "ocr_glm_layout"
    assert t.rows == [["H1", "H2"], ["a", "b"]]
    assert t.bbox == BBox(x0=10.0, y0=20.0, x1=300.0, y1=400.0)  # pixels verbatim


def test_blocks_from_glm_empty_layout() -> None:
    assert blocks_from_glm([]) == []
    assert blocks_from_glm(None) == []


def test_ir_version_is_05() -> None:
    assert IR_VERSION == "0.6"


# ---------------------------------------------------------------------------
# Fast tier -- stub engine: provenance, grid, masking, failure contract
# ---------------------------------------------------------------------------


def test_stub_lineless_provenance_and_grid(stub_engine) -> None:
    blocks = structure_page(_EMPTY_RASTER, [_triple("a", 0, 0, 1, 1)], RouterConfig())
    tables = [b for b in blocks if isinstance(b, TableBlock)]
    assert len(tables) == 1
    tb = tables[0]
    assert tb.source == "ocr_table_lineless"  # default engine
    assert tb.rows == [["a", "b"], ["c", "d"]]
    assert tb.header_rows == 1  # first row is the header by contract
    assert tb.markdown.startswith("| a | b |")
    assert tb.bbox == BBox(x0=100.0, y0=90.0, x1=500.0, y1=230.0)


def test_stub_wired_provenance(stub_engine) -> None:
    blocks = structure_page(_EMPTY_RASTER, [_triple("a", 0, 0, 1, 1)], _wired_cfg())
    tables = [b for b in blocks if isinstance(b, TableBlock)]
    assert tables and tables[0].source == "ocr_table_wired"


def test_stub_engine_failure_raises(boom_engine) -> None:
    # ocr.py catches StructureError and degrades to flat text; the seam
    # itself must surface the failure, never swallow it.
    with pytest.raises(StructureError):
        structure_page(_EMPTY_RASTER, [_triple("a", 0, 0, 1, 1)], RouterConfig())


def test_empty_ocr_lines_short_circuit(stub_engine) -> None:
    assert structure_page(_EMPTY_RASTER, [], RouterConfig()) == []


def test_heading_heuristic_levels(stub_engine) -> None:
    # median height = 20 px (7 lines); small 1.3 -> >= 26.000000000000004 is
    # a heading (so use h=28, not 26); big 1.8 -> >= 36 is level 1.
    # Headings live below the stub table zone (y 90..230).
    lines = [
        _triple("Chapter One", 100.0, 300.0, 400.0, 340.0),  # h=40 -> level 1
        _triple("Section 1.1", 100.0, 400.0, 300.0, 428.0),  # h=28 -> level 2
        _triple("Regular body line.", 100.0, 500.0, 500.0, 520.0),  # h=20 + terminator
        _triple("f1", 100.0, 550.0, 140.0, 570.0),  # fillers anchor the median
        _triple("f2", 200.0, 600.0, 240.0, 620.0),
        _triple("f3", 300.0, 650.0, 340.0, 670.0),
        _triple("f4", 100.0, 700.0, 140.0, 720.0),
    ]
    blocks = structure_page(_EMPTY_RASTER, lines, RouterConfig())
    heads = [b for b in blocks if isinstance(b, HeadingBlock)]
    assert [(h.text, h.level, h.source) for h in heads] == [
        ("Chapter One", 1, "ocr_box_height"),
        ("Section 1.1", 2, "ocr_box_height"),
    ]


def test_table_cells_masked_from_headings(stub_engine) -> None:
    # Tall line inside the stub table zone (y 90..230) must not pose as a heading.
    lines = [
        _triple("Big cell text", 120.0, 100.0, 400.0, 140.0),  # h=40, inside zone
        _triple("f1", 100.0, 400.0, 140.0, 420.0),
        _triple("f2", 200.0, 450.0, 240.0, 470.0),
        _triple("f3", 300.0, 500.0, 340.0, 520.0),
        _triple("f4", 100.0, 550.0, 140.0, 570.0),
    ]
    blocks = structure_page(_EMPTY_RASTER, lines, RouterConfig())
    assert [b for b in blocks if isinstance(b, HeadingBlock)] == []


def test_structure_page_orders_tables_first(stub_engine) -> None:
    lines = [
        _triple("Heading below the table", 100.0, 300.0, 400.0, 330.0),  # h=30 -> level 2
        _triple("f1", 100.0, 400.0, 140.0, 420.0),
        _triple("f2", 200.0, 450.0, 240.0, 470.0),
        _triple("f3", 300.0, 500.0, 340.0, 520.0),
    ]
    blocks = structure_page(_EMPTY_RASTER, lines, RouterConfig())
    kinds = [type(b).__name__ for b in blocks]
    assert kinds[0] == "TableBlock" and "HeadingBlock" in kinds


def test_too_few_lines_yields_no_headings(stub_engine) -> None:
    blocks = structure_page(
        _EMPTY_RASTER, [_triple("Lonely", 0.0, 0.0, 100.0, 200.0)], RouterConfig()
    )
    assert [b for b in blocks if isinstance(b, HeadingBlock)] == []


# ---------------------------------------------------------------------------
# Middle tier -- live lineless on the committed crop fixture
# ---------------------------------------------------------------------------


@pytest.mark.slow
def test_crop_fixture_live_lineless() -> None:
    if not FIXTURE.exists():
        pytest.skip("crop fixture not committed")
    pytest.importorskip("lineless_table_rec")

    from PIL import Image

    from pdf_router.ocr import _rapidocr_get_engine, _rapidocr_triples

    img = np.asarray(Image.open(FIXTURE).convert("RGB"))
    cfg = RouterConfig()
    triples = _rapidocr_triples(_rapidocr_get_engine(cfg)(img))
    assert len(triples) >= 30, "crop must OCR to a realistic number of lines"

    t0 = time.monotonic()
    blocks = structure_page(img, triples, cfg)
    elapsed = time.monotonic() - t0
    assert elapsed < STRUCT_BUDGET_S, f"structure pass took {elapsed:.1f}s"

    tables = [b for b in blocks if isinstance(b, TableBlock)]
    assert tables, "lineless must find the crop's table"
    tb = tables[0]
    assert tb.source == "ocr_table_lineless"
    # Crop = caption + header + most body rows. The span-aware grid yields
    # 25x7 here (the old zip-based build measured 21x7 on the full page —
    # dead measurement, not a bound). Header must land in rows[0].
    assert len(tb.rows) >= 5 and len(tb.rows[0]) >= 5
    assert _fuzzy(" ".join(tb.rows[0]), HEADER_WORD), "header token lost"
    assert _fuzzy(" ".join(" ".join(r) for r in tb.rows), PELAGIC_WORD)


# ---------------------------------------------------------------------------
# Slow tier -- corpus anchors, real full-page OCR + structure (ST-05 bar)
# ---------------------------------------------------------------------------


def _run_anchor(path: Path, page_idx: int) -> tuple[list, float]:
    """Full-page rapidocr + structure pass; returns (blocks, struct_seconds)."""
    from pdf_router.ocr import _rapidocr_get_engine, _rapidocr_triples
    from pdf_router.rasterize import page_raster_array

    cfg = RouterConfig()
    img = page_raster_array(str(path), page_idx, dpi=150)
    triples = _rapidocr_triples(_rapidocr_get_engine(cfg)(img))
    t0 = time.monotonic()
    blocks = structure_page(img, triples, cfg)
    return blocks, time.monotonic() - t0


@pytest.mark.slow
def test_corpus_ryby_p14_table() -> None:
    """Ryby p.14 (1-indexed): ruled header + unruled body -- the hard anchor."""
    if RYBY is None:
        pytest.skip("corpus anchor not available")
    blocks, elapsed = _run_anchor(RYBY, 13)
    assert elapsed < STRUCT_BUDGET_S, f"structure pass took {elapsed:.1f}s"
    tables = [b for b in blocks if isinstance(b, TableBlock)]
    assert tables, "the p14 table must be recovered"
    tb = tables[0]
    assert tb.source == "ocr_table_lineless"
    # Span-aware grid measures 42x6 live; the caption line ("Таблица 2.
    # Экологические показатели...") is placed by the engine as row 0 --
    # so the real header lives in rows[1].
    assert len(tb.rows) >= 15 and len(tb.rows[0]) >= 5
    assert _fuzzy(" ".join(tb.rows[1]), HEADER_WORD), "header row lost from rows[1]"
    assert any(_fuzzy(cell, PELAGIC_WORD) for row in tb.rows for cell in row), (
        "body cells must survive the grid"
    )


@pytest.mark.slow
def test_corpus_genetika_p34_table() -> None:
    """Genetika p.34 (1-indexed): fully ruled table with merged subcolumns."""
    if GENETIKA is None:
        pytest.skip("corpus anchor not available")
    blocks, _elapsed = _run_anchor(GENETIKA, 33)
    tables = [b for b in blocks if isinstance(b, TableBlock)]
    assert tables, "the p34 table must be recovered"
    tb = tables[0]
    assert tb.source == "ocr_table_lineless"
    # Live grid 41x7: rows 0..3 are the heading/caption band the engine
    # places first; the real column header ("Признаки V,% h2 ...") sits
    # at rows[4]. Header + body words must survive the span-aware grid.
    assert len(tb.rows) >= 8 and len(tb.rows[0]) >= 3
    header_at = next(
        (i for i, r in enumerate(tb.rows[:8]) if _fuzzy(" ".join(r), TRAIT_WORD)),
        None,
    )
    assert header_at is not None, "column header row lost from the grid"
    assert any(
        _fuzzy(cell, YOUNG_WORD) for row in tb.rows for cell in row
    ), "body words (молоди) must survive"


@pytest.mark.slow
def test_corpus_ryby_p14_headings() -> None:
    """No-false-positives bar (ST-05): p14 is a full-page table page.

    The page opens directly with the table caption -- there are no chapter
    headings in its data (probed pages 12/13/15: none carry one either).
    The heading heuristic must stay silent rather than invent structure;
    positive heading cases are covered by the fast-tier stub tests.
    """
    if RYBY is None:
        pytest.skip("corpus anchor not available")
    blocks, _elapsed = _run_anchor(RYBY, 13)
    heads = [b for b in blocks if isinstance(b, HeadingBlock)]
    assert heads == [], "full-page table page must not yield invented headings"


# ---------------------------------------------------------------------------
# E2E smoke (ST-07): full pipeline carries blocks into the JSON payload
# ---------------------------------------------------------------------------


@pytest.mark.slow
def test_e2e_scan_page_blocks_in_json() -> None:
    """One scanned page through process_pdf: OCR table lands in page.blocks.

    Uses the first page of the Ryby anchor (1-indexed p.14, 0-indexed 13)
    via a 1-page PDF cut from the corpus file — the same page the slow-tier
    anchors assert on.
    """
    if RYBY is None:
        pytest.skip("corpus anchor not available")

    from pdf_router.api import process_pdf
    from pdf_router.rasterize import page_single_pdf

    pytest.importorskip("lineless_table_rec")
    pdf_bytes = page_single_pdf(str(RYBY), 13)
    tmp_pdf = Path(tempfile.mkdtemp()) / "ryby_p14.pdf"
    tmp_pdf.write_bytes(pdf_bytes)
    try:
        ir, report = process_pdf(tmp_pdf)
        assert report.pages_ocr >= 1, "the cut page must route to OCR"
        tables = [
            b
            for p in ir.pages
            for b in p.blocks
            if isinstance(b, TableBlock) and b.source == "ocr_table_lineless"
        ]
        assert tables, "e2e: OCR table must land in page.blocks"
        assert len(tables[0].rows) >= 5
        assert _fuzzy(" ".join(tables[0].rows[1]), HEADER_WORD)

        # The JSON payload (pipeline persona) must carry the same blocks.
        payload = json.loads(json.dumps(ir, default=lambda o: getattr(o, "__dict__", str(o))))
        flat = json.dumps(payload, ensure_ascii=False)
        assert "ocr_table_lineless" in flat, "json dump must expose block provenance"
    finally:
        tmp_pdf.unlink(missing_ok=True)
        tmp_pdf.parent.rmdir()


def test_table_pass_skips_when_engine_is_none(monkeypatch) -> None:
    """table_engine='none' short-circuits table extraction."""
    from pdf_router.config import RouterConfig
    from pdf_router.structure import _table_pass
    import numpy as np

    cfg = RouterConfig()
    cfg.structure.table_engine = "none"
    raster = np.zeros((100, 100, 3), dtype=np.uint8)
    blocks, bbox = _table_pass(raster, [(np.zeros((4, 2)), "test", 0.9)], cfg)
    assert blocks == []
    assert bbox is None


def test_table_pass_skips_when_layout_detects_no_table(monkeypatch) -> None:
    """_table_pass fast-gates out when layout engine confirms no tables on the page."""
    from pdf_router.config import RouterConfig
    from pdf_router.structure import _table_pass
    import numpy as np

    cfg = RouterConfig()
    cfg.structure.table_engine = "lineless"
    raster = np.zeros((100, 100, 3), dtype=np.uint8)

    monkeypatch.setattr("pdf_router.structure._has_table_in_layout", lambda r: False)
    blocks, bbox = _table_pass(raster, [(np.zeros((4, 2)), "test", 0.9)], cfg)
    assert blocks == []
    assert bbox is None


def test_structure_missing_dependency_raises_actionable_error() -> None:
    """ADR-0011: when ocr-local extra is not installed, fail with actionable message."""
    from unittest.mock import patch
    from pdf_router.structure import _get_table_engine, StructureError, _STRUCTURE_ENGINE_CACHE

    _STRUCTURE_ENGINE_CACHE.clear()
    with patch.dict("sys.modules", {"lineless_table_rec": None, "lineless_table_rec.main": None}):
        with pytest.raises(StructureError, match="ocr-local"):
            _get_table_engine("lineless")
    with patch.dict("sys.modules", {"wired_table_rec": None, "wired_table_rec.main": None}):
        with pytest.raises(StructureError, match="ocr-local"):
            _get_table_engine("wired")
    with patch.dict("sys.modules", {"onnxruntime": None}):
        with pytest.raises(StructureError, match="ocr-local"):
            _get_table_engine("lineless")

