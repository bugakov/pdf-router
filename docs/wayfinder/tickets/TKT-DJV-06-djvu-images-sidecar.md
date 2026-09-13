# TKT-DJV-06 — Images & sidecar semantics for djvu

## Question

What is "an image" for a scan-native format, under the locked sidecar
convention (TKT-RT-08)?

- whole-page rasters (every page IS an image — extracting "images" per
  page is semantically odd: the page raster is the page, not a figure);
- vs embedded image chunks from `djvudump` (IW44 background/stencil split,
  JB2 bitonal masks) — likely useless individually;
- the `output_dir=None` purity invariant (no sidecar IO, `page.images ==
  []`, `core.py`) must hold for djvu too;
- applicability of `routing.extract_raster_photos` /
  `extract_vector_diagrams` flags — honest no-op, or meaningful?

Blocked by: TKT-DJV-02.

## Resolution

**Resolved (grilling, 2026-09-13).** Confirmed:

1. **`page.images = []` for djvu, always — the image pass does not run.**
   Same rule the router already applies to scanned pdf pages: an image
   covering ≥80% of the page is filtered out as a page-background scan,
   "not useful for RAG" (`src/pdf_router/extract_images.py:106-111`,
   filter at `:127-128`). A djvu page IS that scan. The dispatch in
   `core.py:108-122` becomes three-way: pdf → `extract_page_images`,
   docx → `extract_docx_images`, djvu → skip.
2. **No page-strip export.** Exporting page rasters as sidecar images
   duplicates the page (the same raster OCR consumes), bloats disk
   (209-page book × MBs), and contradicts the 80% filter's intent.
   Page visuals as an opt-in feature = out of this map (note in
   ADR-0004).
3. **No `djvudump` chunks** (IW44 background, JB2 stencils) — decoder
   internals, not figures; individually useless for RAG.
4. **`extract_raster_photos` / `extract_vector_diagrams` flags are an
   honest no-op for djvu** (documented in config/README).
5. **Purity invariant holds trivially**: even with `output_dir` set,
   djvu writes nothing under `images/`.
6. **Side effect for TKT-DJV-11**: its image-pass consumer dissolves —
   OCR is the only raster consumer left.
