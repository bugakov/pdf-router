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

_(open)_
