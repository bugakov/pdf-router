# TKT-RC-05 — Text chunks + double-emission subtraction

## Question

The text half of the renderer:

1. **Pipe-table subtraction.** Text blocks come from `page.markdown`
   (doctrine unchanged). Any pipe-table region in the markdown is
   removed when the page has a corresponding `TableBlock`: on
   native/pipe-provenance pages match exactly against
   `TableBlock.markdown`; on OCR pages (sources `ocr_*`) ANY pipe-table
   region in markdown is considered the flattened twin of a block —
   remove all and render tables from blocks only. Decide the exact
   markdown pipe-table region detector (reuse
   `_extract_tables_from_markdown`'s scanner or a leaner line-based
   one — check what the native adapter already exposes). Pipe-tables
   left in markdown with NO block (old IR, failed structure pass) stay
   in the text chunk verbatim — honest degradation.
2. **Text chunk emission**: blocks/paragraph splitting as today
   (heading-started sections, blank-line flush), token-budget split
   with overlap unchanged, `embed` = context preamble (per
   `embed_context`) + text, `markdown` = the text itself (decide: raw
   or preamble-annotated — recommend raw), metadata without the
   `table` sub-object.

Also settle: what happens to pages whose markdown is empty but blocks
exist (pure-table scan pages, e.g. Рыбы p.14) — table chunks only, no
empty text chunk.

Blocked by: [TKT-RC-03](TKT-RC-03-chunks-contract.md).

## Resolution

Done 2026-09-14:

- **Subtraction** (`_strip_tables` + `_pipe_regions`): lean line-based
  detector (row line = `|...|` followed by a separator line; renderers
  may not import adapters, so the two regexes live in chunks.py mirroring
  the native scanner). Regions drop when the page has TableBlocks AND
  (OCR page → ANY pipe region — the markdown twin is always the
  scrambled flattening of a block; native page → whitespace-normalized
  exact match against some `TableBlock.markdown`). Unmatched regions
  stay verbatim — honest degradation, pinned by tests on all three
  branches.
- **Text emission**: page markdown (post-subtraction) splits into
  (section, text) blocks — `##`–`####` headings start a block and stay
  as its first line, blank lines flush; over-budget blocks hard-split
  with overlap, preamble chars counted against `max_tokens` (64-char
  body floor); `embed` = preamble (`doc_title › block-section-or-stack`)
  + text per `embed_context`; `markdown` = the raw text piece (no
  annotation); metadata without the `table` sub-object; empty page
  markdown → zero text chunks (pure-table pages emit tables only —
  Рыбы p.14 shape, pinned by a fast-tier test).
