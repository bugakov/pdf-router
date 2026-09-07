# TKT-RT-06 — Single OCR engine via config, default `pdf_inspector_ocr`

## Question

How is the OCR fallback engine chosen? Multiple chained OCRs (like v0.2), or one configurable engine?

## Resolution

One engine, configurable via `[routing].ocr_fallback_engine` in TOML. Default: `"pdf_inspector_ocr"`. Valid values: `"tesseract"`, `"pdf_inspector_ocr"`, `"glm_ocr"`, `"deepseek_ocr"`. If the value is anything else, raise `ConfigError` with the offending value.

No fallback chain. v0.2's `ocr_specs: list[OCRAdapterSpec]` model (in `core.py:84-93`) is replaced by a single `(engine_name, params)` pair. The dispatcher in `reroute_text_for_page` is a direct `if/elif` on the engine name — see TKT-RT-05.

The OCR engine's per-call parameters (`languages` for tesseract, `api_key` / `base_url` for cloud, etc.) live in `[routing].ocr_fallback_params` as a free-form dict; the engine-specific function reads what it needs.