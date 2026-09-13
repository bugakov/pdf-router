# TKT-DJV-10 — Implement the djvu path end-to-end + docs

## Question

Land the chosen strategy per ADR-0004:

- the djvu inspector/adapter + `_INSPECTORS` registration (one registry
  entry — the locked seam claim, finally exercised);
- `core.py` wiring for the strategy's passes (B: none needed downstream;
  C: the PageSource seam per its graduated ticket);
- CLI `--info` per TKT-DJV-07; IR bump per TKT-DJV-03/04 if any;
- tests: golden anchors per TKT-DJV-08; the fast suite stays fast;
- docs: README format paragraph, `docs/glossary.md`;
- e2e smoke on the anchors; destination reached (commit pending — the user
  commits via `gaa && forge commit && gp`).

Blocked by: TKT-DJV-09.

## Resolution

**(done)** — 2026-09-13. All ADR-0004 checklist items landed:

- **IR 0.4** (`src/pdf_router/ir.py`): `IR_VERSION = "0.4"`, `source_format`
  gains `"djvu"`, `SrcTool` gains `DJVU_TEXT_LAYER` **and** `DJVU_ADAPTER`
  (the second member emerged during implementation: claiming
  `djvu_text_layer` on an empty layerless page lied — the corpus anchor's
  page 1 caught it; empty pages are the adapter's product, docx precedent).
- **Adapter** (`src/pdf_router/adapters/djvu.py`): `extract_ir` +
  `djvu_info` via `djvused`/`djvudump` subprocesses; per-page hidden-text
  extraction (whole-doc output is only an emptiness probe — internal `\f`
  observed in the corpus); page-range validation (ddjvu silently clamps
  out-of-range pages — a bug trap, guarded); stored-DPI-aware render rule
  `-scale=min(target, stored)`.
- **Raster seam** (`src/pdf_router/rasterize.py`): `rasterize_page` /
  `page_raster_array` / `page_single_pdf` dispatch pdf→pymupdf,
  djvu→ddjvu (PPM→stdout, in-memory); engines keep their signatures.
- **OCR** (`src/pdf_router/ocr.py`): all engines flow through the seam;
  glm_ocr wraps the raster into a 1-page PDF; `pdf_inspector_ocr` refuses
  non-PDF **before** env checks (format mismatch is a routing bug — must
  surface regardless of deployment state).
- **Core** (`src/pdf_router/core.py`): three-way image dispatch
  (pdf→extract, docx→docx, djvu→`images=[]`); text pass gates on
  `source_format ∈ {pdf, djvu}`.
- **CLI** (`src/pdf_router/cli.py`): `--info` for djvu through the shared
  `_info_payload` door; corrupt document under `--info` → exit 3 (was an
  uncaught traceback); djvu now routable so "unsupported" exit-1 tests
  re-subjected to unrecognized bytes / corrupt-djvu-exit-3.
- **Fixtures**: `tests/fixtures/djvu/plain.djvu` + `text_layer.djvu`
  (2 KB, built with c44+djvm+djvused, committed).
- **Tests**: 185 fast green (incl. seam, layer policy, counters,
  magic-bytes-beat-extension, output cleanliness, CLI codes);
  slow tier real-corpus: OCR determinism (two identical runs) + text-layer
  anchor (`DJVU_TEXT_LAYER`, no OCR on layered pages).
- **Docs**: README (3 formats, djvu paragraph, IR 0.4 contract, exit
  codes), glossary (djvu native ingest, hidden text layer, raster seam,
  SrcTool/Adapter/Format-dispatch updates), ADR-0004 amended with the
  two-member provenance policy.
- **E2E smoke**: `convert()` → layer text without OCR;
  `process_pdf(output_dir=)` → counters honest, `images/` never created;
  CLI run → three renderers, partial-failure exit table respected;
  `--info` on the real 25-page anchor → cheap JSON.

Destination reached. Commit pending — the user commits via
`gaa && forge commit && gp`.
