# ADR-0004: djvu — native render + PageSource seam, ddjvu subprocess

- **Status:** Accepted
- **Date:** 2026-09-13
- **Supersedes:** the deferred decision in `src/pdf_router/formats.py`
  ("DJVU detected, but the router does not support it yet")
- **Decided via:** wayfinder map `docs/wayfinder/v0.5-djvu-ingest.md`
  (tickets TKT-DJV-01..12); research findings:
  [djvu-tools.md](../wayfinder/research/djvu-tools.md)

## Context

The dispatch seam recognizes DJVU by magic bytes (`AT&TFORM`) but no
inspector exists. The downstream is PDF-shaped: the OCR pass signature is
`(pdf_path, page_number)` and image extraction goes through pymupdf —
exactly why the docx map (ADR-0003) deferred djvu ("needs the PageSource
seam").

djvu differs from docx fundamentally: it is **scan-native** (JB2/IW44
rasters, optional hidden text layer). There is no structure to flatten, so
ADR-0003's anti-conversion arguments mostly do not transfer; the OCR path
is the natural destination for its pages.

Corpus ground truth (TKT-DJV-01, 33 files in `~/vbrdb/orchestrator/uploads/`):
median 209 pages / 7.9 MB; hidden text layers in **3/33 files** (high
quality, word-level, 97–98% alnum); **ddjvu's PDF output is image-only**
(verified in djvulibre source — no code path transfers TXTz chunks);
pymupdf cannot open djvu; python-djvulibre is sdist-only and needs a
`cython<3` pin on Python 3.12.

Three strategies were on the table: **A** — caller converts djvu→PDF
upstream; **B** — the djvu inspector converts via ddjvu into a temp PDF and
reuses the pdf pipeline; **C** — the inspector renders pages natively
(ddjvu per page) and the downstream grows one format-aware raster seam.

## Decision

**Strategy C: native render + PageSource seam, via the `ddjvu` subprocess
per page.** The djvu inspector produces real per-page IR; page rasters are
acquired through one dispatch point; zero new Python dependencies.

Locked sub-decisions (details in the wayfinder tickets):

1. **Rendering mechanism (TKT-DJV-02, TKT-DJV-12):** `ddjvu` subprocess per
   page (`-page=N -scale -format=ppm` → stdout). Measured: median 162
   ms/page, no whole-file re-decode penalty, ~14 MB transient per raster —
   noise next to the 24–40 s/page OCR budget. In-memory bytes, no temp
   files.
2. **Page semantics (TKT-DJV-03):** 0-based physical pages like pdf (the
   docx page-0 precedent does not carry over); the djvu inspector is "a
   pdf-inspector that knows needs_ocr per page" — layer page → native text
   + quality gate, layerless page → `needs_ocr=True` → OCR; counters
   unchanged; `pdf_type` always `"scanned"` with `confidence=1.0`;
   `pages_with_tables`/`pages_with_columns` empty; the text pass gates on
   `source_format ∈ {pdf, djvu}`.
3. **Text layer policy (TKT-DJV-04):** new `SrcTool.DJVU_TEXT_LAYER`
   (reuse of any existing member would lie); layerless pages get
   `SrcTool.DJVU_ADAPTER` — same docx precedent, honest provenance on
   every page (an empty page is the adapter's product, not "the text
   layer's"); honesty via existing carriers, zero new metadata fields
   (`source_format="djvu"`, per-page `src_tool`,
   `pages_needing_ocr` excludes layer pages); `blocks=[]` always; a layer
   failing `looks_like_text` falls back to OCR as usual
   (`low_text_quality`).
4. **IR bump 0.3 → 0.4 (TKT-DJV-04):** `source_format` gains `"djvu"`;
   `SrcTool` gains `DJVU_TEXT_LAYER` and `DJVU_ADAPTER`. An old reader
   crashes on the new enum values (`SrcTool(value)` → `ValueError`) —
   the bump makes that contract visible.
5. **Artifacts (TKT-DJV-05):** no temp files, no cache — the page raster
   lives in memory and dies with the page; ddjvu failure (nonzero exit /
   timeout / empty output) is a per-page error; one subprocess per page,
   concurrency-safe by construction.
6. **Images (TKT-DJV-06):** `page.images = []` for djvu, always — the
   image pass does not run. This is the same rule scanned pdf pages
   already follow (≥80%-of-page images are filtered as page-background,
   `src/pdf_router/extract_images.py:127`); a djvu page IS that scan. No
   page strips, no `djvudump` chunks; the raster/vector flags are a
   documented no-op for djvu.
7. **Raster seam (TKT-DJV-11):** one function in `rasterize.py` — "give me
   page N's raster" — dispatching on source format: pdf → pymupdf
   (byte-identical to today), djvu → ddjvu PPM→stdout. Engines keep their
   signatures: rapidocr gets numpy, tesseract/deepseek get PNG, glm gets a
   raster wrapped into a single-page PDF (support, not refuse — the facade
   promise holds), `pdf_inspector_ocr` honestly refuses (PDF-only engine).
8. **CLI (TKT-DJV-07):** `djvu_info()` modeled on `docx_info`
   (same-parser-no-drift): source_format / page_count / pdf_type /
   pages-with-layer JSON, exit 0; the exit-code table is unchanged.
9. **External binary ruling:** `djvulibre-bin` (ddjvu, djvused — small,
   deterministic, already proven by the reference pipeline
   `~/vbrdb/src/preprocessing/djvu_to_pdf.py`) is admitted into the djvu
   dependency path. ADR-0003's "no external binaries" ruling was
   docx-specific (LibreOffice: hundreds of MB + structure flattening) and
   does not generalize.
10. **Quality bar (TKT-DJV-08):** 4 golden anchors on the vbrdb corpus
    (small / typical / richest-text-layer / largest); fast tier = tiny
    committed fixtures + stubbed OCR; slow tier (`-m slow`) = real-OCR
    determinism on the small anchor + the text-path anchor; error-honesty
    and purity invariants in both tiers.

## Alternatives considered

1. **Strategy A — upstream conversion (caller runs ddjvu).** Rejected —
   breaks the facade promise ("hand over a file, the router figures it
   out"); every embedding application would re-learn djvu.
2. **Strategy B — in-router ddjvu → temp PDF → pdf pipeline.** Rejected —
   whole-file temp PDF per book (209 pp median), loses hidden text layers
   (ddjvu's PDF is image-only), double encoding (djvu → PDF rasters →
   OCR/inspection of rasters).
3. **python-djvulibre** (in-memory rendering without subprocess).
   Rejected — sdist-only, no wheels, stock build breaks on Python 3.12
   (needs `cython<3` + `--no-build-isolation`), GPL-2, upstream dormant
   since 2022. A permanent build fragility in the deploy path.
4. **Re-OCR layer pages for uniformity.** Rejected — 42–70 min of OCR per
   book to discard free high-quality text (3/33 files) violates
   determinism and minimal-resources.
5. **Exporting page strips / djvudump chunks as sidecar images.**
   Rejected — page strips duplicate the page the OCR raster already
   covers and bloat disk; chunks are decoder internals, not figures.

## Consequences

Positive:

- The facade promise extends to djvu: `convert("книга.djvu")` emits the
  same RAG material as PDF/DOCX.
- Free high-quality text on the 3/33 layer files — no OCR spent there.
- Zero new Python dependencies; the one system dependency (djvulibre-bin)
  is tiny, deterministic, and already in the project's stack.
- Per-page rendering fits the existing ThreadPoolExecutor design
  unchanged.

Negative:

- **A system package enters the djvu dependency path** — deploy-anywhere
  now means "plus djvulibre-bin" (documented in README; absent binary →
  typed per-page errors, not silent breakage).
- `pdf_inspector_ocr` cannot serve djvu (PDF-only engine) — typed refusal;
  other four engines work.
- OCR cost for layerless djvu equals scanned-pdf OCR (inherent — djvu
  pages are scans).
- `IR_VERSION` 0.3 → 0.4 is a breaking change for consumers of `to_dict`
  output (no external consumers; recorded for honesty, as in ADR-0001/0003).

## Implementation

- `src/pdf_router/ir.py` — `IR_VERSION = "0.4"`, `SrcTool.DJVU_TEXT_LAYER`,
  module docstring updated.
- `src/pdf_router/formats.py` — `_INSPECTORS[Format.DJVU]` (the magic-byte
  branch already exists).
- `src/pdf_router/adapters/djvu.py` — the inspector (djvused for page
  count + text layers; per-page `PageResult`s) + `djvu_info()`.
- `src/pdf_router/rasterize.py` — the format-aware raster seam (pdf branch
  byte-identical).
- `src/pdf_router/ocr.py` — engines consume the seam; glm wraps djvu
  rasters into a single-page PDF; `pdf_inspector_ocr` refuses djvu with a
  typed error.
- `src/pdf_router/core.py` — three-way image dispatch (pdf/docx/djvu-skip);
  text pass gates on `{pdf, djvu}`.
- `src/pdf_router/cli.py` — `--info` djvu branch.
- Tests: tiny committed fixtures (c44/djvm; one with a text layer) + stub
  OCR fast tier; `-m slow` determinism + text-path anchors; lying-extension
  cases. See TKT-DJV-08.
- Docs: README format paragraph; `docs/glossary.md`.

## References

- Wayfinder map `docs/wayfinder/v0.5-djvu-ingest.md`; tickets TKT-DJV-01..12.
- Research: `docs/wayfinder/research/djvu-tools.md` (toolchain survey,
  corpus facts, ddjvu cost baseline, golden-anchor shortlist,
  2026-09-12/13).
- ADR-0001 (IR structural blocks), ADR-0002 (DocLang not as IR),
  ADR-0003 (docx native parse — the precedent this ADR mirrors; its
  external-binary ruling is docx-specific, see Decision §9).
- Reference pipeline: `~/vbrdb/src/preprocessing/djvu_to_pdf.py`.
