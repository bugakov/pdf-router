# TKT-ST-01 — Research: local table-structure recognition landscape

## Question

What is the cheapest local way to recover table structure (grid + cell
assignment) and heading cues from an OCR page raster, given the locked
constraints (onnxruntime-scale OK, heavy parsers out, cloud opt-in only)?

Cover at minimum:

1. **RapidAI TableStructureRec** (user's first instinct):
   - model sizes, license, onnxruntime compatibility, Python API shape;
   - input contract (image? do we feed it our OCR line boxes, or raw raster?);
   - output contract (cell polygons? HTML? row/col indices to join with
     rapidocr line boxes?);
   - maintenance status, last release, known Cyrillic/scan-quality issues;
   - wired-ness with rapidocr (same vendor — is there a pipeline glue?).
2. **Alternatives, briefly**: PP-Structure(V3) table side (paddle deps?),
   microsoft table-transformer (torch? cost?), pure-CV line-projection
   heuristics (no ML) — anything that could win on the minimal-resources
   axis.
3. **Headings**: is there a cheap local model/heuristic for heading levels on
   scans (font-size via OCR boxes is the obvious one — what do others do)?
4. **glm_ocr layout_details as opt-in**: what exactly does it return for
   tables (HTML?) and titles, and what did it cost per page (the retired
   adapter notes say ~1.8k tokens/page for md_results)?

Output: evidence note `docs/wayfinder/research/ocr-structure-tools.md`,
verdict per option on the fit for our anchors (Russian text, 200-300 DPI
scans, CPU-only).

Blocked by: nothing — takeable now.
