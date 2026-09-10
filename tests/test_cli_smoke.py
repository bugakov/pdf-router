"""End-to-end CLI smoke on a real architectural PDF (pzu).

This is the only test that exercises the full binary path on a PDF with
embedded raster photos — exactly the path that was broken by the race
condition in process_pdf (RT-12 / extract_images regression).

Covers:
- CLI exits 0 (default rapidocr OCR succeeds; historical premise that
  pdf_inspector_ocr fallback fails without system libs is long gone).
- IR.json has images on >= 2 pages (race condition regression).
- Sidecar PNGs exist on disk.
- chunks.jsonl propagates images per page.

One CLI subprocess per session (fixture) — a full run takes minutes
(2 OCR pages + 18 native + model init), so running it four times would
quadruple the slow suite for no extra coverage.

The subprocess is `python -m pdf_router.cli`, NOT `uv run`: when a
timeout kills `uv`, its grandchild python survives as an orphan and
keeps burning CPU for hours (observed). `python -m` is the direct
child, so a timeout kill actually reclaims the machine.

Skipped if pzu_test.pdf is not available.
"""

from __future__ import annotations

import json
import shutil
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

from pdf_router.extract_images import doc_id_from_path


PZU = Path("/tmp/pzu_test.pdf")
CLI_TIMEOUT = 900  # measured ~3 min; generous headroom so a hang fails loudly


@pytest.fixture(scope="session", autouse=True)
def ensure_pzu() -> None:
    """Make sure the test PDF is available; copy from kotel archive if missing."""
    if PZU.exists():
        return
    src = Path(
        "/home/scanum/kotel-zamechanna/archive/ВЫДАЧА_БМК Лехово/2. ПД/"
        "Проектная документация/Раздел 2. Схема планировочной организации земельного у~"
        "/Раздел ПД 2-ПЗУ.pdf"
    )
    if not src.exists():
        pytest.skip(f"pzu_test.pdf not found at {PZU} and source {src} missing")
    PZU.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy(str(src), str(PZU))


@pytest.fixture(scope="session")
def cli_run(tmp_path_factory) -> SimpleNamespace:
    """Run the CLI once per session; all smoke tests assert on its artifacts."""
    out_dir = tmp_path_factory.mktemp("cli-smoke") / "out"
    proc = subprocess.run(
        [sys.executable, "-m", "pdf_router.cli", "--out", str(out_dir), str(PZU)],
        capture_output=True,
        text=True,
        timeout=CLI_TIMEOUT,
    )
    assert proc.returncode == 0, (
        f"CLI exited {proc.returncode}\nstdout:\n{proc.stdout}\nstderr:\n{proc.stderr}"
    )
    return SimpleNamespace(proc=proc, out_dir=out_dir)


def _expected_doc_id() -> str:
    return doc_id_from_path(str(PZU))


@pytest.mark.slow
def test_cli_subprocess_writes_three_outputs(cli_run) -> None:
    """The binary run produced ir.json + md + chunks.jsonl."""
    out_dir = cli_run.out_dir
    assert (out_dir / "pzu_test.ir.json").is_file()
    assert (out_dir / "pzu_test.md").is_file()
    assert (out_dir / "pzu_test.chunks.jsonl").is_file()


@pytest.mark.slow
def test_cli_ir_json_has_images_on_multiple_pages(cli_run) -> None:
    """Race condition regression: `images` is populated on multiple pages,
    not just the last one."""
    ir = json.loads(
        (cli_run.out_dir / "pzu_test.ir.json").read_text(encoding="utf-8")
    )
    pages_with_imgs = [p for p in ir["pages"] if p.get("images")]
    assert len(pages_with_imgs) >= 2, (
        f"expected >= 2 pages with images in IR.json, got {len(pages_with_imgs)}: "
        f"{[p['page'] for p in pages_with_imgs]}"
    )


@pytest.mark.slow
def test_cli_writes_sidecar_pngs(cli_run) -> None:
    """Sidecar layout from RT-12: <output_dir>/<doc-id>/images/*.png"""
    img_dir = cli_run.out_dir / _expected_doc_id() / "images"
    assert img_dir.is_dir(), f"sidecar dir not created: {img_dir}"
    pngs = sorted(img_dir.glob("page*_photo_*.png"))
    assert len(pngs) >= 1, "no PNGs written under sidecar dir"
    for p in pngs:
        assert p.stat().st_size > 0, f"empty PNG: {p}"


@pytest.mark.slow
def test_cli_chunks_propagate_images(cli_run) -> None:
    """chunks.jsonl must include `images` for each chunk (RT-13)."""
    chunks_path = cli_run.out_dir / "pzu_test.chunks.jsonl"
    assert chunks_path.is_file()
    chunks = [
        json.loads(ln)
        for ln in chunks_path.read_text(encoding="utf-8").splitlines()
        if ln.strip()
    ]
    assert chunks, "chunks.jsonl is empty"
    pages_with_imgs = {c["page"] for c in chunks if c.get("images")}
    assert len(pages_with_imgs) >= 2, (
        f"expected chunks for >= 2 pages with images, got {len(pages_with_imgs)}: "
        f"{sorted(pages_with_imgs)}"
    )
    for c in chunks:
        assert "images" in c, f"chunk missing images field: {c}"
