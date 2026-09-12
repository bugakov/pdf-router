# TKT-DX-02 — Page semantics for docx in the IR

## Question

docx has no physical pages. What is `PageResult.page` (the extraction unit)
for a docx document, and what must hold for the page-keyed machinery around
it?

Options to decide between:

1. **whole document = single page** (page 0);
2. **logical sections** — split at top-level headings (`Heading1` style);
3. **emulate physical pagination** from explicit breaks
   (`w:br w:type="page"`, section breaks) — noting Word repaginates
   dynamically, so explicit breaks are sparse and unreliable.

Consequences to check: `chunks` renderer (splits markdown by `## ` headings —
does page granularity matter for chunk boundaries?), `json` renderer
round-trip, 1-indexed metadata lists (`pages_with_tables`,
`pages_needing_ocr`), per-page `error`/`images` keying, and the
architecture-report candidate-2 framing ("page = extraction unit"; pdf →
physical page, docx → logical piece decided by ADR).

Blocked by: TKT-DX-01 (the chosen tool's natural output granularity feeds the
option choice).

## Resolution

_(open)_
