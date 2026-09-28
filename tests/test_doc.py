"""Legacy .doc ingest tests (office-oxide, ADR-0008 / TKT-OI-04).

Two layers per TKT-OI-07:

- **fast, no corpus**: the OLE2 gate is proven elsewhere (test_formats);
  here the corrupt-container contract — oxide's typed error becomes a
  document-level ``ValueError`` (exit 3), not a format rejection.
- **corpus anchors** (skip-if-missing): F2 (274 KB, exact structure) in
  the default run; the 56 MB and 276 MB anchors are ``slow``-tier with
  their budgets (measured 8.43 s / 6.97 s on this box).

No committed .doc fixture — user ruling in TKT-OI-07: a clean machine
runs the gate + corrupt tests only.
"""

from __future__ import annotations

import json
import os
import re
import tempfile
import time
from pathlib import Path

import pytest

from pdf_router import (
    convert,
    from_dict,
    process_pdf,
    render_chunks,
    to_dict,
)
from pdf_router.adapters.doc import doc_info, extract_ir
from pdf_router.ir import FigureBlock, HeadingBlock, TableBlock

_OLE2_MAGIC = b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1"

F2_VOLUME = Path(
    "/home/scanum/kotel-zamechanna/archive/ВЫДАЧА_БМК Чижевщина"
    "/ИИ ред. формат/ИГДИ ред. формат/2. Приложения"
    "/Приложение Б. Программа работ/ПР ИГДИ (00845.2024.07).doc"
)
F3_VOLUME = Path(
    "/home/scanum/kotel-zamechanna/archive/ВЫДАЧА_БМК Лехово"
    "/ИИ ред. формат/ИГМИ ред. формат/00847.2024.07-ИГМИ_Изм.1.doc"
)
B276_VOLUME = Path(
    "/home/scanum/kotel-zamechanna/archive/ВЫДАЧА_БМК Опухлики"
    "/ПД ред. формат/8. ООС ред. формат"
    "/ООС Котельная Опухлики СЗЗ_ред 4_2.doc"
)


def _corpus(path: Path) -> str:
    if not path.exists():
        pytest.skip(f"golden corpus anchor not available: {path}")
    return str(path)


# ---------------------------------------------------------------------------
# Fast tier: corrupt container -> document-level failure (exit 3)
# ---------------------------------------------------------------------------


def test_corrupt_ole2_raises_document_error(tmp_path: Path) -> None:
    """Right magic, garbage body: the gate routes it as DOC, the adapter
    fails the *document* (ValueError -> exit 3) with oxide's typed reason
    surfacing in the message (TKT-OI-04 ruling 5)."""
    p = tmp_path / "broken.doc"
    p.write_bytes(_OLE2_MAGIC + b"\x00" * 64)
    with pytest.raises(ValueError, match="malformed doc"):
        extract_ir(str(p))


def test_missing_doc_raises_filenotfound(tmp_path: Path) -> None:
    """IO errors are not document errors (same taxonomy as docx)."""
    with pytest.raises(FileNotFoundError):
        extract_ir(str(tmp_path / "nope.doc"))


# ---------------------------------------------------------------------------
# Corpus tier: F2 — the small anchor (exact counts, TKT-OI-07)
# ---------------------------------------------------------------------------


def test_golden_f2_structure() -> None:
    """F2: 1 page, LibreOffice-exact tables, oxide's 7-of-9 headings.

    Heading pin is 7 (oxide `to_ir`), not the LibreOffice ground truth 9 —
    amended in TKT-OI-07 during OI-09; the 2-heading gap is oxide's honest
    known weakness (the same phenomenon as F3's 1-vs-42).
    """
    path = _corpus(F2_VOLUME)
    t0 = time.perf_counter()
    ir = extract_ir(path)
    elapsed = time.perf_counter() - t0
    m = ir.metadata
    assert m.page_count == 1
    assert m.source_format == "doc"
    assert m.pdf_type == "text_based"
    assert m.confidence == 1.0
    assert m.is_complex_layout is False
    assert m.pages_with_tables == [1]
    assert m.title and "Капитальный ремонт" in m.title
    page = ir.pages[0]
    assert page.needs_ocr is False
    assert page.src_tool.value == "doc_adapter"
    assert page.images == []
    tables = [b for b in page.blocks if isinstance(b, TableBlock)]
    assert len(tables) == 4
    assert [[len(t.rows), len(t.rows[0])] for t in tables] == [
        [1, 2],
        [16, 2],
        [5, 4],
        [10, 3],
    ]
    assert sum(len(t.rows) for t in tables) == 32
    heads = [b for b in page.blocks if isinstance(b, HeadingBlock)]
    assert len(heads) == 7
    assert "Топографо-геодезическая" in page.markdown
    assert "| --- |" in page.markdown  # pipe tables present in the md
    assert elapsed < 5.0
    assert from_dict(to_dict(ir)) == ir


def test_golden_f2_sidecar_via_pipeline(tmp_path: Path) -> None:
    """One image: pure run writes nothing; ``output_dir`` writes the
    sidecar file and records it on ``page.images`` (core dispatch for
    ``source_format == "doc"``)."""
    path = _corpus(F2_VOLUME)

    # pure run: no sidecar IO, no directories created
    monkey_cwd = tmp_path / "cwd"
    monkey_cwd.mkdir()
    old = os.getcwd()
    os.chdir(monkey_cwd)
    try:
        ir, report = process_pdf(path)
        assert report.pages_total == 1
        assert ir.pages[0].images == []
        assert list(monkey_cwd.iterdir()) == []
    finally:
        os.chdir(old)

    # sidecar run
    out = tmp_path / "out"
    ir, _report = process_pdf(path, output_dir=out)
    page = ir.pages[0]
    figs = [b for b in page.blocks if isinstance(b, FigureBlock)]
    assert len(figs) == 1 and figs[0].image_index == 0
    assert page.images, "core must record the written sidecar path"
    written = out / page.images[0]
    assert written.is_file() and written.stat().st_size > 1000
    assert written.read_bytes()[:3] == b"\xff\xd8\xff"  # jpeg payload
    # the markdown placeholder resolves against <output_dir>/<doc-id>/
    placeholder = "images/page0001_image_00.jpeg"
    assert placeholder in page.markdown
    assert (out / Path(page.images[0]).parts[0] / placeholder).is_file()
    assert f"![]({placeholder})" in page.markdown


def test_golden_f2_convert_and_chunks() -> None:
    """Renderers flow through untouched: md + chunks, embeds non-empty."""
    path = _corpus(F2_VOLUME)
    md = convert(path)
    assert "## 3.1 Топографо-геодезическая" in md
    assert "| --- |" in md
    payloads = [json.loads(ln) for ln in render_chunks(
        extract_ir(path)
    ).splitlines() if ln.strip()]
    assert payloads
    assert all(p["embed"].strip() for p in payloads)
    assert all(p["metadata"]["src_tool"] == "doc_adapter" for p in payloads)
    joined = "\n".join(p["embed"] for p in payloads)
    assert "Топографо-геодезическая" in joined


def test_golden_f2_info() -> None:
    path = _corpus(F2_VOLUME)
    info = doc_info(path)
    assert info == {
        "source_format": "doc",
        "pdf_type": "text_based",
        "page_count": 1,
        "confidence": 1.0,
        "title": extract_ir(path).metadata.title,
        "tables": 4,
        "images": 1,
    }


# ---------------------------------------------------------------------------
# Slow tier: the big anchors (-m slow; budgets from TKT-OI-07)
# ---------------------------------------------------------------------------


@pytest.mark.slow
def test_golden_f3_56mb_slow() -> None:
    """56 MB: LibreOffice-exact 184 rows, honest 1-heading gap, 74 images
    with resolvable sidecars. Measured 8.43 s / 406 MB RSS (2026-09-28)."""
    path = _corpus(F3_VOLUME)
    t0 = time.perf_counter()
    ir = extract_ir(path)
    elapsed = time.perf_counter() - t0
    page = ir.pages[0]
    tables = [b for b in page.blocks if isinstance(b, TableBlock)]
    assert sum(len(t.rows) for t in tables) == 184
    assert len(tables) == 79
    assert len([b for b in page.blocks if isinstance(b, HeadingBlock)]) >= 1
    figs = [b for b in page.blocks if isinstance(b, FigureBlock)]
    assert len(figs) == 74
    assert page.markdown.count("![](images/") == 74
    assert len(page.markdown) > 50_000  # sidecar placeholders, not base64 (RT-08)
    assert "СПИСОК ИСПОЛНИТЕЛЕЙ" in page.markdown
    assert elapsed < 15.0
    assert from_dict(to_dict(ir)) == ir

    # every placeholder resolves to a written file
    with tempfile.TemporaryDirectory() as d:
        out = Path(d)
        ir2, _r = process_pdf(path, output_dir=out)
        page2 = ir2.pages[0]
        assert len(page2.images) == 74
        did = Path(page2.images[0]).parts[0]
        placeholders = re.findall(r"!\[\]\((images/[^)]+)\)", page2.markdown)
        assert len(placeholders) == 74
        for ph in placeholders:
            assert (out / did / ph).is_file(), ph


@pytest.mark.slow
def test_golden_276mb_slow() -> None:
    """276 MB main ООС text: parses in one page, no cap (OI-04 ruling 4).
    Measured 6.97 s / 454 MB RSS (2026-09-28 — the TKT-OI-04 measurement
    promise, recorded in TKT-OI-07)."""
    path = _corpus(B276_VOLUME)
    t0 = time.perf_counter()
    ir = extract_ir(path)
    elapsed = time.perf_counter() - t0
    page = ir.pages[0]
    assert ir.metadata.page_count == 1
    assert ir.metadata.source_format == "doc"
    assert len(page.markdown) > 400_000  # measured 490,316
    assert "Котельная" in page.markdown
    assert "Опухлики" in page.markdown
    tables = [b for b in page.blocks if isinstance(b, TableBlock)]
    assert sum(len(t.rows) for t in tables) == 1820
    assert elapsed < 60.0
    assert from_dict(to_dict(ir)) == ir
