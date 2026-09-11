"""TOML config loader and Config dataclasses.

Precedence (locked): CLI flag > TOML file > built-in default. There is no
env-var lookup for keys. OCR model: ``[ocr.<engine>]`` sections STORE engine
params; exactly ONE engine runs, picked by ``[routing].ocr_fallback_engine``.
Sections of unselected engines are validated but otherwise ignored. Unknown
sections/keys log a WARNING and are ignored (forward-compat).

The library never writes ``os.environ``. pdf-inspector's Rust core reads its
two library paths (``PDFIUM_LIB_PATH``/``ORT_DYLIB_PATH``) from the process
env; ``pdf_router.wire_pdf_inspector_env()`` is the sanctioned bridge from
the ``[ocr.pdf_inspector_ocr]`` section (the CLI calls it automatically).
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

logger = logging.getLogger("pdf_router")

#: Required keys per OCR engine section. Engines not listed here (rapidocr)
#: have no required keys and work on built-in defaults.
OCR_ENGINE_REQUIRED_KEYS: dict[str, tuple[str, ...]] = {
    "pdf_inspector_ocr": ("pdfium_lib_path", "ort_dylib_path"),
    "tesseract": ("languages",),
    "glm_ocr": ("api_key", "base_url"),
    "deepseek_ocr": ("api_key", "base_url"),
}

#: All selectable engine names for ``routing.ocr_fallback_engine``.
_KNOWN_OCR_ENGINES = {"rapidocr", *OCR_ENGINE_REQUIRED_KEYS}

_KNOWN_TOP_LEVEL = {"router", "ocr", "renderers", "routing"}
_KNOWN_ROUTER_KEYS = {"log_level"}
#: Accepted values for ``[router].log_level`` (case-insensitive).
_VALID_LOG_LEVELS = ("debug", "info", "warning", "error")
_KNOWN_RENDERERS_KEYS = {"output_dir", "page_markers", "chunks", "json"}
_KNOWN_CHUNKS_KEYS = {"max_tokens", "overlap_tokens"}
_KNOWN_JSON_KEYS = {"include_page_bbox"}
_KNOWN_ROUTING_KEYS = {
    "language",
    "text_alpha_threshold",
    "workers",
    "extract_raster_photos",
    "extract_vector_diagrams",
    "ocr_fallback_engine",
}


class ConfigError(Exception):
    """Raised when the TOML config is malformed or misses required keys."""


@dataclass
class ChunksConfig:
    max_tokens: int = 512
    overlap_tokens: int = 64


@dataclass
class RendererConfig:
    """Everything the renderers need (DESIGN §7). Shared by all three.

    ``output_dir`` is the **CLI's** default output directory (``--out`` flag
    > this key > ``./out``). The library core never reads it:
    :func:`pdf_router.convert` and :func:`pdf_router.process_pdf` write
    nothing unless the caller passes ``output_dir=`` explicitly — an
    embedded run stays in memory (README contract: "no files written").
    """

    enabled: set[str] = field(default_factory=lambda: {"md", "chunks", "json"})
    output_dir: Path | None = None
    page_markers: bool = False
    chunks: ChunksConfig = field(default_factory=ChunksConfig)
    json_include_page_bbox: bool = False


@dataclass
class RoutingConfig:
    language: str | None = None
    text_alpha_threshold: float = 0.30
    workers: int = 2
    extract_raster_photos: bool = True
    extract_vector_diagrams: bool = False
    # Default rapidocr: PP-OCRv6 small det + PP-OCRv5 mobile rec is the
    # only combination that actually recognizes Cyrillic (validated in
    # TKT-OC-02; pdf_inspector_ocr's dictionary has zero Cyrillic tokens).
    ocr_fallback_engine: str = "rapidocr"


@dataclass
class RouterConfig:
    """Everything the pipeline needs.

    ``ocr_engines`` maps engine name -> params dict, 1:1 with the ``[ocr.*]``
    TOML sections. The engine actually used is the single
    ``routing.ocr_fallback_engine``; params of unselected engines are stored
    but ignored (keep keys for several engines, switch by changing one line).
    """

    ocr_engines: dict[str, dict[str, Any]] = field(default_factory=dict)
    log_level: str = "warning"
    renderers: RendererConfig = field(default_factory=RendererConfig)
    routing: RoutingConfig = field(default_factory=RoutingConfig)

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
        log_level = str(router_section.get("log_level", "warning")).lower()
        if log_level not in _VALID_LOG_LEVELS:
            raise ConfigError(
                f'[router].log_level: unknown value "{log_level}" '
                f"(valid: {', '.join(_VALID_LOG_LEVELS)})"
            )

        engines: dict[str, dict[str, Any]] = {}
        ocr_section = data.get("ocr", {})
        if not isinstance(ocr_section, dict):
            raise ConfigError("[ocr] must be a table of engine sub-tables")
        for name, params in ocr_section.items():
            if not isinstance(params, dict):
                raise ConfigError(f"[ocr.{name}] must be a table")
            if name not in _KNOWN_OCR_ENGINES:
                logger.warning(
                    "unknown OCR engine section [ocr.%s] (known: %s); params "
                    "stored, but the engine cannot be selected",
                    name,
                    ", ".join(sorted(_KNOWN_OCR_ENGINES)),
                )
            else:
                missing = [
                    k for k in OCR_ENGINE_REQUIRED_KEYS.get(name, ()) if not params.get(k)
                ]
                if missing:
                    raise ConfigError(
                        f"[ocr.{name}] missing required key(s): {', '.join(missing)}"
                    )
            engines[name] = dict(params)

        renderers = _parse_renderers(data.get("renderers", {}))
        routing = _parse_routing(data.get("routing", {}))
        _validate_ocr_selection(engines, routing)
        return cls(ocr_engines=engines, log_level=log_level, renderers=renderers, routing=routing)


def _validate_ocr_selection(
    engines: dict[str, dict[str, Any]], routing: RoutingConfig
) -> None:
    """Fail fast on the OCR selector: known name, configured section.

    Catches at load time what otherwise surfaces as ``OcrError`` on the
    first page that needs OCR. rapidocr is exempt from the section check —
    it has no required keys and works on built-in defaults.
    """
    engine = routing.ocr_fallback_engine
    if engine not in _KNOWN_OCR_ENGINES:
        raise ConfigError(
            f"[routing] unknown ocr_fallback_engine {engine!r}; "
            f"expected one of {sorted(_KNOWN_OCR_ENGINES)}"
        )
    if engine != "rapidocr" and engine not in engines:
        required = ", ".join(OCR_ENGINE_REQUIRED_KEYS[engine])
        raise ConfigError(
            f"[routing] ocr_fallback_engine = {engine!r} but no [ocr.{engine}] "
            f"section; add one (required key(s): {required})"
        )
    if engine == "pdf_inspector_ocr":
        # pdf-inspector's Rust core dlopens these two files; a typo'd path
        # would otherwise surface as OcrError on the first OCR page. Unselected
        # sections are not path-checked (they are stored, not used).
        for key in ("pdfium_lib_path", "ort_dylib_path"):
            p = Path(str(engines[engine][key]))
            if not p.is_file():
                raise ConfigError(f"[ocr.pdf_inspector_ocr] {key} not found: {p}")


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


def _parse_routing(sec: Any) -> RoutingConfig:
    if not isinstance(sec, dict):
        raise ConfigError("[routing] must be a table")
    _warn_unknown(sec, _KNOWN_ROUTING_KEYS, "[routing]")

    cfg = RoutingConfig()
    if "language" in sec:
        cfg.language = str(sec["language"]) if sec["language"] is not None else None
    if "text_alpha_threshold" in sec:
        cfg.text_alpha_threshold = float(sec["text_alpha_threshold"])
    if "workers" in sec:
        cfg.workers = int(sec["workers"])
    if "extract_raster_photos" in sec:
        cfg.extract_raster_photos = bool(sec["extract_raster_photos"])
    if "extract_vector_diagrams" in sec:
        cfg.extract_vector_diagrams = bool(sec["extract_vector_diagrams"])
    if "ocr_fallback_engine" in sec:
        cfg.ocr_fallback_engine = str(sec["ocr_fallback_engine"])

    return cfg
