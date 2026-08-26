"""Ticket 08: TOML config loader."""

import logging

import pytest

from pdf_router.config import ConfigError, RendererConfig, RouterConfig


def test_no_file_gives_defaults(tmp_path) -> None:
    cfg = RouterConfig.from_toml(tmp_path / "nonexistent.toml")
    assert cfg.ocr_specs == []
    assert cfg.log_level == "warning"
    assert cfg.renderers.enabled == {"md", "chunks", "json"}
    assert cfg.renderers.chunks.max_tokens == 512
    assert cfg.renderers.chunks.overlap_tokens == 64


def test_none_gives_defaults() -> None:
    cfg = RouterConfig.from_toml(None)
    assert cfg.ocr_specs == []
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

[renderers.json]
include_page_bbox = true

[routing]
""",
        encoding="utf-8",
    )
    cfg = RouterConfig.from_toml(toml)

    assert cfg.log_level == "info"
    assert [s.name for s in cfg.ocr_specs] == ["tesseract", "glm_ocr"]
    assert cfg.ocr_specs[0].params["languages"] == ["rus", "eng"]
    assert cfg.ocr_specs[1].params["api_key"] == "sk-test"
    assert cfg.renderers.output_dir.as_posix() == "custom_out"
    assert cfg.renderers.page_markers is True
    assert cfg.renderers.chunks.max_tokens == 256
    assert cfg.renderers.chunks.overlap_tokens == 32
    assert cfg.renderers.json_include_page_bbox is True


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


def test_unknown_adapter_raises(tmp_path) -> None:
    toml = tmp_path / "router.toml"
    toml.write_text(
        """
[ocr.wat_ocr]
foo = "bar"
""",
        encoding="utf-8",
    )
    with pytest.raises(ConfigError, match="unknown OCR adapter"):
        RouterConfig.from_toml(toml)


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
    assert cfg.ocr_specs == []
    assert any("future_thing" in rec.message for rec in caplog.records)


def test_malformed_toml_raises(tmp_path) -> None:
    toml = tmp_path / "router.toml"
    toml.write_text("this is [not valid toml", encoding="utf-8")
    with pytest.raises(ConfigError, match="malformed TOML"):
        RouterConfig.from_toml(toml)
