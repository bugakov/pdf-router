# TKT-DJV-03 — Page semantics for djvu in the IR

## Question

Unlike docx, djvu has real physical pages. Presumably page = physical djvu
page — but what must hold for the page-keyed machinery?

- `PageResult.page` numbering (0-based, matching the pdf convention);
- `needs_ocr` / `ocr_reason` semantics for pages whose text came from the
  hidden layer vs from OCR vs none (interplay with TKT-DJV-04);
- report counters (`pages_native` / `pages_ocr` / `pages_failed`) honesty
  for djvu pages;
- 1-indexed metadata lists (`pages_with_tables`, `pages_needing_ocr`);
- per-page `error` / `images` keying — the docx single-page-0 precedent
  (TKT-DX-02) does NOT carry over; confirm.

Consequences to check: `chunks` renderer page independence, `json`
round-trip, ProcessingReport aggregation in `core.py`.

Blocked by: TKT-DJV-02.

## Resolution

**Resolved (grilling, 2026-09-12).** Confirmed as a single package:

1. **Numbering.** `PageResult.page` = 0-based physical djvu page (same as
   pdf); page count via `djvused -e n`. Metadata lists stay 1-indexed
   (locked convention). Per-page keying for `error` / `images` /
   `ocr_reasons_by_page` — the docx single-page-0 precedent (TKT-DX-02)
   does NOT carry over.
2. **needs_ocr flow.** The djvu inspector behaves as "a pdf-inspector that
   knows needs_ocr per page":
   - page WITH a hidden text layer → `markdown` = layer text,
     `needs_ocr=False`. The existing quality gate still applies
     (`looks_like_text`): a garbage layer → `ocr_reason="low_text_quality"`
     → OCR fallback. Zero new `ocr_reason` values.
   - page WITHOUT a layer → `markdown=""`, `needs_ocr=True` → the text pass
     OCRs it with `inspector_needs_ocr`, raster from the PageSource seam
     (TKT-DJV-11).
   - Layer-extraction mechanics (djvused/djvutxt) are implementation
     detail (TKT-DJV-10), not policy.
3. **Counters.** `core.py` aggregation unchanged: layer pages (passing the
   gate) → `pages_native`; layerless pages → `pages_ocr` (after OCR);
   failures → `pages_failed`. No third counter introduced.
4. **`Metadata.pdf_type` for djvu = always `"scanned"`, `confidence=1.0`.**
   djvu is scan-native; where the text came from is carried per page by
   `src_tool` (shape in TKT-DJV-04) and per file by `pages_needing_ocr` —
   not by the file-level type. Rejected: mirroring pdf semantics
   (all-layer files → `"text_based"`, partial → `"mixed"`) — that lies
   about born-digital for a scan format.
5. **`pages_with_tables` / `pages_with_columns` = empty** for djvu: OCR
   markdown carries no table structure and there are no blocks to detect.
6. **Text-pass gating.** The text pass runs for
   `source_format in {"pdf", "djvu"}` (today pdf-only,
   `src/pdf_router/core.py:93`); djvu pages flow through it exactly like
   pdf pages. The seam refactor itself is TKT-DJV-11; this ticket fixes
   semantics only.

IR bump shape (new `SrcTool` member for the text layer + `"djvu"` in
`source_format`) remains deliberately unspecified until TKT-DJV-04 closes.
