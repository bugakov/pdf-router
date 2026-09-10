"""Ticket 10: CLI — version, --info, full run, exit codes."""

import json

from pdf_router import cli


def test_version(capsys) -> None:
    try:
        cli.main(["--version"])
    except SystemExit as e:
        assert e.code == 0
    out = capsys.readouterr().out
    assert "pdf-router" in out


def test_missing_file_returns_1(capsys, tmp_path) -> None:
    assert cli.main([str(tmp_path / "nope.pdf")]) == 1


def test_invalid_config_returns_1(capsys, tmp_path, p5_pdf) -> None:
    bad = tmp_path / "router.toml"
    bad.write_text("[[broken", encoding="utf-8")
    assert cli.main(["--config", str(bad), str(p5_pdf)]) == 1


def test_info_on_real_pdf(p5_pdf, capsys, tmp_path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)  # avoid picking up a real router.toml in CWD
    code = cli.main(["--info", str(p5_pdf)])
    assert code == 0
    out = capsys.readouterr().out
    data = json.loads(out)
    assert data["pdf_type"] == "text_based"


def test_full_run_three_files_exit_0(p5_pdf, tmp_path, monkeypatch, capsys) -> None:
    monkeypatch.chdir(tmp_path)
    out_dir = tmp_path / "out"
    code = cli.main(["--out", str(out_dir), str(p5_pdf)])
    assert code == 0
    assert (out_dir / "p5.md").is_file()
    assert (out_dir / "p5.chunks.jsonl").is_file()
    assert (out_dir / "p5.ir.json").is_file()
    md = (out_dir / "p5.md").read_text(encoding="utf-8")
    assert "Обозначение" in md  # golden anchor
    assert "ИЛО9.1" in md


def test_render_subset_writes_only_requested(p5_pdf, tmp_path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)
    out_dir = tmp_path / "out_only_md"
    code = cli.main(["--render", "md", "--out", str(out_dir), str(p5_pdf)])
    assert code == 0
    assert (out_dir / "p5.md").is_file()
    assert not (out_dir / "p5.chunks.jsonl").exists()
    assert not (out_dir / "p5.ir.json").exists()


def test_exit_2_when_page_fails(p27_pdf, tmp_path, monkeypatch, capsys) -> None:
    """p27's single page triggers OCR fallback. We force pdf_inspector_ocr via
    --config (the new default is rapidocr, which would succeed and exit 0).
    Partial success → exit 2, failed page listed on stderr."""
    import textwrap
    # Paths must exist on disk (load-time validation): the engine is
    # configured, but its binaries are dummies, so the OCR call fails at
    # runtime (dlopen of a non-library file) → partial success.
    pdfium = tmp_path / "libpdfium.so"
    ort = tmp_path / "libonnxruntime.so"
    pdfium.write_bytes(b"dummy")
    ort.write_bytes(b"dummy")
    cfg = tmp_path / "test.toml"
    cfg.write_text(textwrap.dedent(f"""\
        [routing]
        ocr_fallback_engine = "pdf_inspector_ocr"

        [ocr.pdf_inspector_ocr]
        pdfium_lib_path = "{pdfium}"
        ort_dylib_path = "{ort}"
    """))
    # cli.main wires these env vars from the TOML; register the undo first
    # so the leak does not outlive the test.
    monkeypatch.delenv("PDFIUM_LIB_PATH", raising=False)
    monkeypatch.delenv("ORT_DYLIB_PATH", raising=False)
    monkeypatch.chdir(tmp_path)
    out_dir = tmp_path / "out27"
    code = cli.main(["--config", str(cfg), "--out", str(out_dir), str(p27_pdf)])
    assert code == 2
    ir_json = json.loads((out_dir / "p27.ir.json").read_text(encoding="utf-8"))
    # v0.3: pdf_inspector_ocr is configured but fails. Error != "no_ocr_configured"
    assert ir_json["pages"][0]["error"] != "no_ocr_configured"
    err = capsys.readouterr().err
    assert "page 1:" in err
    assert "outputs written" in err


def test_unknown_renderer_returns_1(p5_pdf, tmp_path, monkeypatch, capsys) -> None:
    monkeypatch.chdir(tmp_path)
    assert cli.main(["--render", "md,wat", str(p5_pdf)]) == 1
