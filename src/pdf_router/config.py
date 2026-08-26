"""TOML config loader and Config dataclasses. DESIGN.md §6.

Precedence (locked): CLI flag > TOML file > built-in default. There is no
env-var lookup for keys. All OCR engines are opt-in: a spec absent from the
config means the adapter is not even imported. Unknown sections/keys log a
WARNING and are ignored (forward-compat).
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

logger = logging.getLogger("pdf_router")

#: Known OCR adapter names and their required spec keys (DESIGN §6).
OCR_SPEC_REQUIRED_KEYS: dict[str, tuple[str, ...]] = {
    "pdf_inspector_ocr": ("pdfium_lib_path", "ort_dylib_path"),
    "tesseract": ("languages",),
    "glm_ocr": ("api_key", "base_url"),
    "deepseek_ocr": ("api_key", "base_url"),
}

_KNOWN_TOP_LEVEL = {"router", "ocr", "renderers", "routing"}
_KNOWN_ROUTER_KEYS = {"log_level"}
_KNOWN_RENDERERS_KEYS = {"output_dir", "page_markers", "chunks", "json"}
_KNOWN_CHUNKS_KEYS = {"max_tokens", "overlap_tokens"}
_KNOWN_JSON_KEYS = {"include_page_bbox"}


class ConfigError(Exception):
    """Raised when the TOML config is malformed or misses required keys."""


@dataclass(frozen=True)
class OCRAdapterSpec:
    """Tagged union of an OCR adapter selection: ``(name, params)``.

    ``name`` is one of :data:`OCR_SPEC_REQUIRED_KEYS`; ``params`` holds the
    per-adapter options from the TOML section (paths, languages, api keys).
    """

    name: str
    params: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if self.name not in OCR_SPEC_REQUIRED_KEYS:
            raise ConfigError(
                f"unknown OCR adapter {self.name!r}; expected one of {sorted(OCR_SPEC_REQUIRED_KEYS)}"
            )
        missing = [k for k in OCR_SPEC_REQUIRED_KEYS[self.name] if not self.params.get(k)]
        if missing:
            raise ConfigError(f"[ocr.{self.name}] missing required key(s): {', '.join(missing)}")


@dataclass
class ChunksConfig:
    max_tokens: int = 512
    overlap_tokens: int = 64


@dataclass
class RendererConfig:
    """Everything the renderers need (DESIGN §7). Shared by all three."""

    enabled: set[str] = field(default_factory=lambda: {"md", "chunks", "json"})
    output_dir: Path = Path("out")
    page_markers: bool = False
    chunks: ChunksConfig = field(default_factory=ChunksConfig)
    json_include_page_bbox: bool = False


@dataclass
class RouterConfig:
    """Everything the Router needs. Order of ``ocr_specs`` = fallback order."""

    ocr_specs: list[OCRAdapterSpec] = field(default_factory=list)
    log_level: str = "warning"
    renderers: RendererConfig = field(default_factory=RendererConfig)

    @classmethod
    def from_toml(cls, path: str | Path | None) -> "RouterConfig":
        """Load config from a TOML file; missing file → built-in defaults."""
        if path is None:
            return cls()
        p = Path(path)
        if not p.exists():
            return cls()
        import tomllib

        try:
            with p.open("rb") as fh:
                data = tomllib.load(fh)
        except tomllib.TOMLDecodeError as exc:
            raise ConfigError(f"malformed TOML in {p}: {exc}") from exc
        return cls.from_mapping(data)

    @classmethod
    def from_mapping(cls, data: dict[str, Any]) -> "RouterConfig":
        """Build a RouterConfig from a parsed TOML mapping (validated)."""
        _warn_unknown(data.keys(), _KNOWN_TOP_LEVEL, "top-level")

        router_section = _section(data, "router", _KNOWN_ROUTER_KEYS)
        log_level = str(router_section.get("log_level", "warning"))

        specs: list[OCRAdapterSpec] = []
        ocr_section = data.get("ocr", {})
        if not isinstance(ocr_section, dict):
            raise ConfigError("[ocr] must be a table of adapter sub-tables")
        for name, params in ocr_section.items():
            if not isinstance(params, dict):
                raise ConfigError(f"[ocr.{name}] must be a table")
            specs.append(OCRAdapterSpec(name=name, params=dict(params)))

        renderers = _parse_renderers(data.get("renderers", {}))
        return cls(ocr_specs=specs, log_level=log_level, renderers=renderers)


def _warn_unknown(present: Any, known: set[str], where: str) -> None:
    if isinstance(present, dict):
        present = present.keys()
    unknown = set(present or ()) - known
    for key in sorted(unknown):
        logger.warning("unknown %s key %r ignored (forward-compat)", where, key)


def _section(data: dict[str, Any], name: str, known_keys: set[str]) -> dict[str, Any]:
    sec = data.get(name, {})
    if not isinstance(sec, dict):
        raise ConfigError(f"[{name}] must be a table")
    _warn_unknown(sec, known_keys, f"[{name}]")
    return sec


def _parse_renderers(sec: Any) -> RendererConfig:
    if not isinstance(sec, dict):
        raise ConfigError("[renderers] must be a table")
    _warn_unknown(sec, _KNOWN_RENDERERS_KEYS, "[renderers]")

    cfg = RendererConfig()
    if "output_dir" in sec:
        cfg.output_dir = Path(str(sec["output_dir"]))
    if "page_markers" in sec:
        cfg.page_markers = bool(sec["page_markers"])

    chunks_sec = sec.get("chunks", {})
    if not isinstance(chunks_sec, dict):
        raise ConfigError("[renderers.chunks] must be a table")
    _warn_unknown(chunks_sec, _KNOWN_CHUNKS_KEYS, "[renderers.chunks]")
    if "max_tokens" in chunks_sec:
        cfg.chunks.max_tokens = int(chunks_sec["max_tokens"])
    if "overlap_tokens" in chunks_sec:
        cfg.chunks.overlap_tokens = int(chunks_sec["overlap_tokens"])

    json_sec = sec.get("json", {})
    if not isinstance(json_sec, dict):
        raise ConfigError("[renderers.json] must be a table")
    _warn_unknown(json_sec, _KNOWN_JSON_KEYS, "[renderers.json]")
    if "include_page_bbox" in json_sec:
        cfg.json_include_page_bbox = bool(json_sec["include_page_bbox"])

    return cfg
