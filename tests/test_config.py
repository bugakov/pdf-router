"""Ticket 08: TOML config loader."""

import logging

import pytest

from pdf_router.config import ConfigError, RouterConfig


def test_no_file_gives_defaults(tmp_path) -> None:
    cfg = RouterConfig.from_toml(tmp_path / "nonexistent.toml")
    assert cfg.ocr_engines == {}
    assert cfg.log_level == "warning"
    assert cfg.renderers.enabled == {"md", "chunks", "json"}
    assert cfg.renderers.chunks.max_tokens == 512
    assert cfg.renderers.chunks.overlap_tokens == 64
    assert cfg.renderers.chunks.summary == "facts"
    assert cfg.renderers.chunks.embed_context is True


def test_none_gives_defaults() -> None:
    cfg = RouterConfig.from_toml(None)
    assert cfg.ocr_engines == {}
    assert cfg.log_level == "warning"


def test_full_valid_toml(tmp_path) -> None:
    toml = tmp_path / "router.toml"
    toml.write_text(
        """
[router]
log_level = "info"

[ocr.tesseract]
languages = ["rus", "eng"]

[ocr.glm_ocr]
api_key = "sk-test"
base_url = "https://api.z.ai/api/paas/v4"

[renderers]
output_dir = "custom_out"
page_markers = true

[renderers.chunks]
max_tokens = 256
overlap_tokens = 32
summary = "none"
embed_context = false

[renderers.json]
include_page_bbox = true

[routing]
""",
        encoding="utf-8",
    )
    cfg = RouterConfig.from_toml(toml)

    assert cfg.log_level == "info"
    assert set(cfg.ocr_engines) == {"tesseract", "glm_ocr"}
    assert cfg.ocr_engines["tesseract"]["languages"] == ["rus", "eng"]
    assert cfg.ocr_engines["glm_ocr"]["api_key"] == "sk-test"
    assert cfg.renderers.output_dir.as_posix() == "custom_out"
    assert cfg.renderers.page_markers is True
    assert cfg.renderers.chunks.max_tokens == 256
    assert cfg.renderers.chunks.overlap_tokens == 32
    assert cfg.renderers.chunks.summary == "none"
    assert cfg.renderers.chunks.embed_context is False
    assert cfg.renderers.json_include_page_bbox is True


def test_chunks_summary_glm_reserved_rejected(tmp_path) -> None:
    """``summary = "glm"`` is the reserved opt-in seam (map v0.7): named
    in the error, rejected at load until implemented (TKT-RC-01)."""
    toml = tmp_path / "router.toml"
    toml.write_text('[renderers.chunks]\nsummary = "glm"\n', encoding="utf-8")
    with pytest.raises(ConfigError, match="glm"):
        RouterConfig.from_toml(toml)


def test_unknown_log_level_raises(tmp_path) -> None:
    """Typo in [router].log_level fails at load, not a silent WARNING fallback."""
    toml = tmp_path / "router.toml"
    toml.write_text(
        '[router]\nlog_level = "verbos"\n',
        encoding="utf-8",
    )
    with pytest.raises(ConfigError, match=r'log_level.*"verbos".*debug'):
        RouterConfig.from_toml(toml)


def test_log_level_case_insensitive(tmp_path) -> None:
    toml = tmp_path / "router.toml"
    toml.write_text('[router]\nlog_level = "INFO"\n', encoding="utf-8")
    assert RouterConfig.from_toml(toml).log_level == "info"


def test_missing_required_key_raises(tmp_path) -> None:
    toml = tmp_path / "router.toml"
    toml.write_text(
        """
[ocr.glm_ocr]
api_key = "sk-test"
""",
        encoding="utf-8",
    )
    with pytest.raises(ConfigError, match="base_url"):
        RouterConfig.from_toml(toml)


def test_unknown_engine_section_warns_but_loads(tmp_path, caplog) -> None:
    """Unknown [ocr.*] name is a typo-catcher warning, not an error.

    Params are stored (forward-compat) but the engine cannot be selected.
    """
    toml = tmp_path / "router.toml"
    toml.write_text(
        """
[ocr.wat_ocr]
foo = "bar"
""",
        encoding="utf-8",
    )
    with caplog.at_level(logging.WARNING, logger="pdf_router"):
        cfg = RouterConfig.from_toml(toml)
    assert cfg.ocr_engines == {"wat_ocr": {"foo": "bar"}}
    assert any("unknown OCR engine section" in rec.message for rec in caplog.records)


def test_unknown_selector_raises(tmp_path) -> None:
    """Typo in ocr_fallback_engine is a ConfigError at load, not at OCR time."""
    toml = tmp_path / "router.toml"
    toml.write_text(
        """
[routing]
ocr_fallback_engine = "rapidoocr"
""",
        encoding="utf-8",
    )
    with pytest.raises(ConfigError, match="unknown ocr_fallback_engine"):
        RouterConfig.from_toml(toml)


def test_selected_engine_without_section_raises(tmp_path) -> None:
    """Selecting an engine without its [ocr.*] section fails at load."""
    toml = tmp_path / "router.toml"
    toml.write_text(
        """
[routing]
ocr_fallback_engine = "glm_ocr"
""",
        encoding="utf-8",
    )
    with pytest.raises(ConfigError, match=r"no \[ocr\.glm_ocr\]"):
        RouterConfig.from_toml(toml)


def test_pdf_inspector_paths_missing_section_raises(tmp_path):
    """pdf_inspector_ocr section without the two library paths → ConfigError."""
    cfg_file = tmp_path / "cfg.toml"
    cfg_file.write_text(
        '[routing]\nocr_fallback_engine = "pdf_inspector_ocr"\n\n'
        '[ocr.pdf_inspector_ocr]\ndpi = 200.0\n'
    )
    with pytest.raises(ConfigError, match=r"pdfium_lib_path.*ort_dylib_path"):
        RouterConfig.from_toml(cfg_file)


def test_pdf_inspector_paths_not_on_disk_raises(tmp_path):
    """Paths pointing at nonexistent files → ConfigError at load (fail fast)."""
    cfg_file = tmp_path / "cfg.toml"
    cfg_file.write_text(
        '[routing]\nocr_fallback_engine = "pdf_inspector_ocr"\n\n'
        "[ocr.pdf_inspector_ocr]\n"
        'pdfium_lib_path = "/nonexistent/libpdfium.so"\n'
        'ort_dylib_path = "/nonexistent/libonnxruntime.so"\n'
    )
    with pytest.raises(ConfigError, match="pdfium_lib_path not found"):
        RouterConfig.from_toml(cfg_file)


def test_pdf_inspector_paths_ok_when_files_exist(tmp_path):
    """Dummy files on disk pass load validation (runtime may still fail)."""
    pdfium = tmp_path / "libpdfium.so"
    ort = tmp_path / "libonnxruntime.so"
    pdfium.write_bytes(b"x")
    ort.write_bytes(b"x")
    cfg_file = tmp_path / "cfg.toml"
    cfg_file.write_text(
        '[routing]\nocr_fallback_engine = "pdf_inspector_ocr"\n\n'
        f"[ocr.pdf_inspector_ocr]\n"
        f'pdfium_lib_path = "{pdfium}"\n'
        f'ort_dylib_path = "{ort}"\n'
    )
    cfg = RouterConfig.from_toml(cfg_file)
    assert cfg.ocr_engines["pdf_inspector_ocr"]["pdfium_lib_path"] == str(pdfium)


def test_rapidocr_selected_without_section_loads(tmp_path) -> None:
    """rapidocr needs no [ocr.*] section — built-in defaults are enough."""
    toml = tmp_path / "router.toml"
    toml.write_text(
        """
[routing]
ocr_fallback_engine = "rapidocr"
""",
        encoding="utf-8",
    )
    cfg = RouterConfig.from_toml(toml)
    assert cfg.ocr_engines == {}
    assert cfg.routing.ocr_fallback_engine == "rapidocr"


def test_unknown_section_warns_but_loads(tmp_path, caplog) -> None:
    toml = tmp_path / "router.toml"
    toml.write_text(
        """
[future_thing]
key = "value"
""",
        encoding="utf-8",
    )
    with caplog.at_level(logging.WARNING, logger="pdf_router"):
        cfg = RouterConfig.from_toml(toml)
    assert cfg.ocr_engines == {}
    assert any("future_thing" in rec.message for rec in caplog.records)


def test_malformed_toml_raises(tmp_path) -> None:
    toml = tmp_path / "router.toml"
    toml.write_text("this is [not valid toml", encoding="utf-8")
    with pytest.raises(ConfigError, match="malformed TOML"):
        RouterConfig.from_toml(toml)


def test_garbled_text_policy_read_from_toml(tmp_path) -> None:
    """The policy is a RoutingConfig field, so TOML must be able to set it."""
    toml = tmp_path / "router.toml"
    toml.write_text(
        """
[routing]
garbled_text_policy = "ocr"
""",
        encoding="utf-8",
    )
    cfg = RouterConfig.from_toml(toml)
    assert cfg.routing.garbled_text_policy == "ocr"
