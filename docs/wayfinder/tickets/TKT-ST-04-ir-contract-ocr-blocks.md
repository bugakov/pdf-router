# TKT-ST-04 — IR contract for OCR-sourced blocks

## Question

What exactly changes in the IR so OCR pages can carry blocks without lying
about provenance (v0.5 lesson: honesty carriers over fake shapes):

1. `source` values for OCR-sourced blocks (new provenance strings? e.g.
   `table_structure_rec` / `glm_layout` / `pipe_fallback` vs the existing
   `structure_tree` / `font_size_fallback`);
2. bbox optionality — OCR cell/heading boxes are in raster pixels, not PDF
   points; do we scale to page points, or make bbox optional for these
   sources? (User: coordinates only if resource-cheap — semantic fidelity
   first.);
3. IR version bump 0.4 → 0.5? (new enum-ish values may justify it — same
   reasoning as TKT-DJV-04) — decide here, not at implementation time;
4. heading representation: `HeadingBlock.level` on scans (heuristic levels
   1–6? or a flatter honest model?);
5. does `TableBlock` grow anything (e.g. per-cell confidence) or stay as-is?

Output: locked IR deltas (fields, sources, version), ready for the ADR and
implementation tickets.

Blocked by: [TKT-ST-03](TKT-ST-03-structure-approach.md) (the approach fixes
which sources exist).

## Resolution

**Resolved (grilling, 2026-09-14).** Five deltas locked; implementation
lands in TKT-ST-07, not here:

1. **`source` provenance values** (new, alongside the existing
   `structure_tree` / `font_size_fallback`):
   - `ocr_table_lineless` / `ocr_table_wired` — table blocks from the
     local structure pass (engine name in the value; `auto` resolves to
     whichever ran);
   - `ocr_pipe_fallback` — pipe-tables parsed from markdown of boxless
     engines (tesseract / deepseek / pdf_inspector_ocr);
   - `ocr_glm_layout` — blocks short-circuited from glm
     `layout_details` (tables and titles alike);
   - `ocr_box_height` — headings from the box-height heuristic.
2. **bbox: optional, kept in raster pixels as-is** — no scaling to PDF
   points (user standing rule: coordinates only if resource-cheap; the
   boxes are already in hand, conversion spends precision and CPU for
   nothing). `bbox: tuple | None` documented per-source; renders/
   chunkers must treat it as a hint, not a contract.
3. **IR version 0.4 → 0.5** — new provenance values + optional bbox on
   OCR sources (same reasoning as the 0.3→0.4 bump in TKT-DJV-04: the
   IR's surface changed, say so).
4. **Headings: `HeadingBlock.level` restricted to {1, 2} for scan
   sources** — level 1 = large (height ≥ `structure.heading_big_ratio`
   × page-median box height), level 2 = small heading (height ≥
   `structure.heading_small_ratio`, below the big line); thresholds
   TOML-tunable. Honest granularity: the heuristic cannot support 1–6;
   the chapter/section split is what RAG renderers consume.
5. **`TableBlock` stays as-is** — no per-cell confidence field (the
   OCR scores live on the ocr_result, not in the IR; nothing consumes
   them yet). Row/col semantics come from `logic_points` at
   implementation time, not as a new IR field.

Frontier moves to [TKT-ST-05](TKT-ST-05-quality-bar.md) (its IR
dependency is satisfied) — TKT-ST-06 (ADR-0005) unblocked once 05
lands; TKT-ST-07 blocked only by 05+06.
