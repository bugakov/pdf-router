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

_(open)_
