"""End-to-end CLI smoke on a real architectural PDF (pzu).

This is the only test that exercises the full binary path on a PDF with
embedded raster photos — exactly the path that was broken by the race
condition in process_pdf (RT-12 / extract_images regression).

Covers:
- CLI exits (we don't pin 0/1 because pdf_inspector_ocr fallback fails
  on pdfium-less environments, which is expected; the test asserts the
  side-effects rather than the exit code).
- IR.json has images on >= 2 pages (race condition regression).
- Sidecar PNGs exist on disk.
- chunks.jsonl propagates images per page.

Skipped if pzu_test.pdf is not available.
"""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest

from pdf_router.extract_images import doc_id_from_path


PZU = Path("/tmp/pzu_test.pdf")


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


def _expected_doc_id() -> str:
    return doc_id_from_path(str(PZU))


def test_cli_subprocess_writes_three_outputs(tmp_path, monkeypatch) -> None:
    """Invoke pdf-router as a real subprocess (the binary, not the Python API)."""
    monkeypatch.chdir(tmp_path)
    out_dir = tmp_path / "out"
    proc = subprocess.run(
        [
            "uv",
            "run",
            "--project",
            "/home/scanum/pdf-router",
            "pdf-router",
            "--out",
            str(out_dir),
            str(PZU),
        ],
        capture_output=True,
        text=True,
        timeout=120,
    )
    # Exit 0 (full success) or 1 (partial: OCR fallback fails in this env).
    # We don't assert exit code because the OCR failure is environmental,
    # not a regression. We assert side effects.
    assert (out_dir / "pzu_test.ir.json").is_file(), (
        f"IR.json missing. stdout={proc.stdout!r} stderr={proc.stderr!r}"
    )
    assert (out_dir / "pzu_test.md").is_file()
    assert (out_dir / "pzu_test.chunks.jsonl").is_file()


def test_cli_ir_json_has_images_on_multiple_pages(tmp_path, monkeypatch) -> None:
    """Race condition regression: `images` is populated on multiple pages,
    not just the last one."""
    monkeypatch.chdir(tmp_path)
    out_dir = tmp_path / "out"
    subprocess.run(
        [
            "uv",
            "run",
            "--project",
            "/home/scanum/pdf-router",
            "pdf-router",
            "--out",
            str(out_dir),
            str(PZU),
        ],
        capture_output=True,
        text=True,
        timeout=120,
    )
    ir = json.loads((out_dir / "pzu_test.ir.json").read_text(encoding="utf-8"))
    pages_with_imgs = [p for p in ir["pages"] if p.get("images")]
    assert len(pages_with_imgs) >= 2, (
        f"expected >= 2 pages with images in IR.json, got {len(pages_with_imgs)}: "
        f"{[p['page'] for p in pages_with_imgs]}"
    )


def test_cli_writes_sidecar_pngs(tmp_path, monkeypatch) -> None:
    """Sidecar layout from RT-12: <output_dir>/<doc-id>/images/*.png"""
    monkeypatch.chdir(tmp_path)
    out_dir = tmp_path / "out"
    subprocess.run(
        [
            "uv",
            "run",
            "--project",
            "/home/scanum/pdf-router",
            "pdf-router",
            "--out",
            str(out_dir),
            str(PZU),
        ],
        capture_output=True,
        text=True,
        timeout=120,
    )
    expected_doc_id = _expected_doc_id()
    img_dir = out_dir / expected_doc_id / "images"
    assert img_dir.is_dir(), f"sidecar dir not created: {img_dir}"
    pngs = sorted(img_dir.glob("page*_photo_*.png"))
    assert len(pngs) >= 1, "no PNGs written under sidecar dir"
    for p in pngs:
        assert p.stat().st_size > 0, f"empty PNG: {p}"


def test_cli_chunks_propagate_images(tmp_path, monkeypatch) -> None:
    """chunks.jsonl must include `images` for each chunk (RT-13)."""
    monkeypatch.chdir(tmp_path)
    out_dir = tmp_path / "out"
    subprocess.run(
        [
            "uv",
            "run",
            "--project",
            "/home/scanum/pdf-router",
            "pdf-router",
            "--out",
            str(out_dir),
            str(PZU),
        ],
        capture_output=True,
        text=True,
        timeout=120,
    )
    chunks_path = out_dir / "pzu_test.chunks.jsonl"
    assert chunks_path.is_file()
    chunks = [json.loads(ln) for ln in chunks_path.read_text(encoding="utf-8").splitlines() if ln.strip()]
    assert chunks, "chunks.jsonl is empty"
    pages_with_imgs = {c["page"] for c in chunks if c.get("images")}
    assert len(pages_with_imgs) >= 2, (
        f"expected chunks for >= 2 pages with images, got {len(pages_with_imgs)}: "
        f"{sorted(pages_with_imgs)}"
    )
    for c in chunks:
        assert "images" in c, f"chunk missing images field: {c}"
