# TKT-DJV-11 — PageSource seam: format-aware raster acquisition

## Question

One raster-acquisition step dispatching on the source format (pdf →
pymupdf, djvu → `ddjvu` subprocess), so every OCR engine — and the image
pass — gets page rasters without knowing formats:

- **where the dispatch lives**: inside `ocr_page` (it already dispatches
  engines), a shared helper consumed by `ocr.py` and the image pass, or
  both — so PDF code paths stay byte-identical (regression containment);
- **the raster contract per consumer**: numpy RGB (rapidocr renders
  internally today, `ocr.py:193–204`), PNG bytes (tesseract/deepseek via
  `rasterize_page`, `ocr.py:313/431`), single-page PDF (glm builds one
  via pymupdf, `ocr.py:367–375` — a djvu page must either be wrapped
  raster→PDF or glm honestly refuses djvu with a typed error);
- **`pdf_inspector_ocr` stays PDF-only** — typed `OcrError` for djvu, not
  a silent failure;
- **failure semantics**: ddjvu nonzero exit / timeout / empty raster →
  per-page `OcrError` (never a document-level abort);
- **PDF regression containment**: the PDF branch of the dispatch must
  produce byte-identical behavior (golden anchors + the existing slow
  suite prove it).

The djvu branch: `ddjvu -page=N -scale=S -format=ppm` → stdout → decode
(PNG conversion or direct numpy), resolution matched to each engine's dpi
knob.

Blocked by: TKT-DJV-03 (page semantics fix where rasters are consumed),
TKT-DJV-06 (the image pass's djvu semantics).

## Resolution

**Resolved (grilling, 2026-09-13).** Confirmed:

1. **One function in `rasterize.py`: "give me page N's raster"** —
   dispatching on source format: pdf → pymupdf (byte-identical to
   today), djvu → ddjvu (`-page=N -scale -format=ppm` → stdout → decode).
   Engines call it; engine signatures unchanged. PDF regression
   containment: the pdf branch is the same code as today, proven by
   golden anchors (TKT-DJV-08).
2. **Per-engine djvu support**: rapidocr — numpy (PPM → numpy direct);
   tesseract / deepseek — PNG (converted from PPM);
   **pdf_inspector_ocr — typed honest refusal** (`OcrError`: engine is
   PDF-only by nature).
3. **glm_ocr fork → (a): djvu supported** — raster wrapped into a
   single-page PDF (~5 lines, same as it already builds for pdf pages).
   Keeps the facade promise "hand over a file, the router figures it
   out"; refusal (b) rejected as needlessly breaking it for a cloud
   engine.
4. **ddjvu failure = per-page error** (fixed by TKT-DJV-05); no
   document-level aborts.
5. **Image-pass consumer dissolved** (TKT-DJV-06): OCR is the only
   raster consumer — the seam is smaller than originally feared.
