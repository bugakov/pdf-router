# TKT-RT-10 — One raster per page, one OCR call per page

## Question

When OCR fallback fires for a page, do we rasterize once and call OCR once on the whole page, or do we rasterize per-block and call OCR per-block?

## Resolution

One rasterize, one OCR call, both per page. `rasterize_page(path, page_idx)` returns the page as a single image; `engine_ocr_page(path, page_idx, params)` consumes the page and returns markdown.

Per-block rasterize+OCR was rejected for v0.3 because:
1. It's strictly more expensive (more raster calls, more OCR calls).
2. v0.3 has already settled on `looks_like_text` per-page (TKT-RT-02) — the decision to fall back is made at page granularity, so per-block OCR would need a second decision layer we don't have.
3. Per-block is deferred to v0.4+ and will get its own ticket then.