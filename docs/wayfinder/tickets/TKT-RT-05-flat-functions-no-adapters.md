# TKT-RT-05 — Flat functions, no Adapter classes

## Question

How should the v0.3 routing code be organized at the module level? Keep the existing `Adapter*` class hierarchy, or replace it with top-level functions?

## Resolution
## Resolution

Resolved: **A1** — `_ocr_pdf_inspector` dispatches to `pdf_inspector.process_pdf_with_ocr(mode='force', page_numbers=[page_idx + 1])`. Signature unchanged. Runtime paths plumbed from `cfg.routing.ocr_fallback_params`.

**Performance note:** ONNX model loading is CPU-bound. Per-page A1 is slow (>5 min/page in this environment). Batch A2 (`ocr_pages(path, page_numbers=[...])` — one call for all OCR pages) is the next optimization. For fast local processing, `tesseract` is available as alternative engine.
- `extract_ir(path)` — replaces `AdapterPdfInspector().process(path)`.
- `tesseract_ocr_page(path, page_idx, params)` — replaces `TesseractAdapter(spec).ocr_page(...)`.
- `pdf_inspector_ocr_page(path, page_idx, params)`.
- `glm_ocr_page(path, page_idx, params)`.
- `deepseek_ocr_page(path, page_idx, params)`.
- `extract_raster_photos(page, images_dir)`.
- `extract_vector_diagrams(page, images_dir)`.
- `looks_like_text(text, language, threshold)`.
- `rasterize_page(path, page_idx)`.

Each function is named after what it actually does, in plain English. The dispatcher in `reroute_text_for_page` is a direct `if/elif` on the engine name — no `get_adapter()` indirection.

The existing `Adapter*` classes in `core.py` and `adapters/pdf_inspector.py` get deleted in this effort. They were vestigial Java-style patterns from v0.2; v0.3 doesn't need them.

## Status: DONE

**Implementation:**

- `src/pdf_router/quality.py` — `looks_like_text(text, language, threshold)` — per-page text quality check
- `src/pdf_router/rasterize.py` — `rasterize_page(pdf_path, page_idx, dpi=200)` — shared rasterize helper
- `src/pdf_router/extract_images.py` — `extract_raster_photos()`, `extract_vector_diagrams()` — flat functions, no classify_page
- `src/pdf_router/ocr.py` — `ocr_page(pdf_path, page_idx, config) -> (markdown, engine)` — flat dispatcher, one if/elif per engine
- `src/pdf_router/core.py` — `process_pdf(path, config)` — parallel text+images per-page, `ProcessingReport` dataclass, `looks_like_text` + OCR fallback per-page
- `src/pdf_router/adapters/pdf_inspector.py` — `extract_ir(pdf_path)` top-level added, `AdapterPdfInspector` kept as compat shim for existing tests
- `src/pdf_router/ir.py` — `PageResult.images: list[str]` added
- `src/pdf_router/config.py` — `RoutingConfig` dataclass with full [routing] parsing
- `src/pdf_router/cli.py` — updated to use `process_pdf()`, exit codes: 0=ok, 1=page failed
- Tests: all 59 pass (2 @pytest.mark.slow skipped — require .runtime binaries)
