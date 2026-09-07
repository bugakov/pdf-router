# TKT-RT-07 — `extract_raster_photos` drops background images

## Question

When extracting raster images via `extract_raster_photos(page)`, do we save every Image XObject on the page, or filter some out?

## Resolution

Filter out background images: any raster whose bounding box covers ≥ 80% of the page area is treated as a scan background, not a content photo, and is **not** saved. Saved images get a path like `images/page{N}_photo_{idx}.png`.

Rationale: scanned monographs (one JPEG for the whole page, text drawn over) produce one big image per page. If we save it as a "photo", downstream CLIP-based RAG embedding will index the entire page as a photograph — which is exactly the wrong signal. The text path already covers those pages.

The threshold (0.8) lives in code as a constant, not in config — there's no scenario where a user wants to index scan backgrounds as photos.