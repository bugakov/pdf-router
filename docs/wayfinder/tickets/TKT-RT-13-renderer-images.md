# TKT-RT-13 — Renderer changes to expose `page.images`

## Status: DONE

## Resolution (filled in 2026-09-06)

Implemented as recommended in the design above:

- **`md.py`** — no change (Option 2). `md.py` stays human-focused; RAG users use `chunks.py` or `json_renderer.py`.
- **`chunks.py`** — added `images: list[str]` to each chunk JSON object, populated from `page.images` (the page the chunk came from). Empty list when no images.
- **`ir.py`** — `PageResult.images: list[str] = field(default_factory=list)` (already added in RT-05). `from_dict` now reads it back. `to_dict` works via `asdict`.
- **`json_renderer.py`** — no change (uses `to_dict` which now covers `images`).

**Implementation:**

- `src/pdf_router/renderers/chunks.py:78-99` — `images` field in chunk JSON.
- `src/pdf_router/ir.py:316-318` — `from_dict` reads `images`.
- `tests/test_renderers.py:55` — `test_chunks_jsonl_structure` updated to include `images` in expected schema.
- `tests/test_renderers.py:107-118` — `test_chunks_include_images` (new): verifies chunks carry `page.images`.
- `tests/test_renderers.py:121-129` — `test_json_round_trip_images` (new): verifies `to_dict` → `from_dict` preserves `images`.

**Tests:** 33 unit tests pass; full suite 59/59 + 2 skipped (slow).
