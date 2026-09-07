# TKT-RT-09 — `extract_page_images` and `process_text_for_page` run in parallel

## Question

Should image extraction and text routing for a given page run sequentially or in parallel?

## Resolution

In parallel. `process_pdf` submits both `extract_images_for_page` and `process_text_for_page` per page to a single `ThreadPoolExecutor(max_workers=cfg.routing.workers)`, then joins per page. Default `workers = 2`.

Rationale: image extraction is I/O-bound (writing PNGs to disk) and CPU-light; text routing is CPU-light when text is native (just `looks_like_text`) and OCR-bound when fallback fires. They don't share state, so there's no reason to serialize them. Workers=2 keeps memory and OCR concurrency bounded.