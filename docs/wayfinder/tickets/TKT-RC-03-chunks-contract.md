# TKT-RC-03 — Chunks contract: envelope schema + context walk-back

## Question

Fix the single JSONL envelope every chunk shares (the wire contract of
this whole map) and the deterministic context rules. Decide nothing
here that RC-01 already ruled; write down the exact shapes:

- envelope keys and types (`chunk_id`, `chunk_version: 2`, `type` ∈
  {text, table, table_part}, `embed`, `markdown`, `metadata{...}`,
  `images`), including the `metadata.table` sub-shape for
  `table`/`table_part` (`table_id`, `part`, `parts`, `n_rows`,
  `n_cols`, `header`, `source`);
- `chunk_id` scheme (stable, collision-free, deterministic — e.g.
  `{page}-t{n}` for tables, `{page}-{seq}` for text; docx = page 0
  only; decide the exact format here);
- `doc_title` fallback chain (Metadata.title → basename of
  source_path);
- context walk-back: order blocks per page by geometry (raster pixels
  on OCR pages, PDF points on native — both top-left-ish y; decide and
  document the sort key per provenance), inherit the nearest previous
  HeadingBlock across page boundaries, breadcrumbs
  (`{deepest heading} › {doc_title}`), fallbacks when no heading
  exists (section-from-markdown regex → doc_title);
- `embed_context` flag semantics for text chunks (preamble joins
  doc_title + deepest heading; counts against max_tokens).

Output of this ticket: the contract, stated in the ticket resolution
and then in ADR-0006 (RC-06) — no production code beyond what RC-02
landed. The renderer consumes the contract in RC-04.

Blocked by: [TKT-RC-02](TKT-RC-02-source-path-doc-id.md).

## Resolution

Done 2026-09-14 — contract as implemented in
`src/pdf_router/renderers/chunks.py` (module docstring is the living
reference) and recorded in ADR-0006:

- **Envelope**: `chunk_id: str`, `chunk_version: 2`, `type ∈ {text,
  table, table_part}`, `embed: str`, `markdown: str`,
  `metadata{doc_id, doc_title, source_format, page, section, src_tool,
  error[, table]}`, `images: list`. `metadata.table = {table_id,
  part, parts, n_rows, n_cols, header, source}` on table chunks.
- **chunk_id scheme**: tables `{page}-t{n}` (per-page counter in
  reading order; `-{k:02d}` suffix on split parts, `part/parts`
  filled), text `{page}-{seq:03d}` (page-local sequence after
  subtraction). Deterministic; docx = page 0 only.
- **doc_title fallback**: `Metadata.title` → basename of
  `source_path` → `None` (hand-built IR).
- **Walk-back**: per page, blocks in reading order — OCR pages sort by
  raster-pixel `(y0, x0)` (bbox-less blocks keep IR order, stable
  sort), native pages keep IR order (already reading order).
  HeadingBlocks push an open stack (pop ≥ level, push); a table/text
  chunk inherits the deepest open heading, across page boundaries.
- **Section fallback chain**: deepest heading → first `##`–`####`
  heading of the page markdown → `None`.
- **embed_context**: preamble line `doc_title › heading` opens the
  embed of every chunk (tables: caption rides as an extra line);
  counts against `max_tokens` with a 64-char body floor.
