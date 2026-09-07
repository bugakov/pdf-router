# ADR-0001: IR 0.2 — Structural blocks (tables, headings, figures, formulas)

- **Status:** Accepted
- **Date:** 2026-09-03
- **Supersedes:** IR 0.1 (flat `markdown: str` per page, no structural decomposition)

## Context

The IR 0.1 contract was a flat `markdown: str` per page, with document-level
metadata about which pages had tables/columns (`pages_with_*`) but no
per-page structural decomposition. Empirical probing against the real corpus
(`/home/scanum/kotel-zamechanna/Путейцев Обь/0. ЗАГРУЖЕННОЕ В ГЭ/3.1_00909.2025.04-ТКР1.pdf`,
927 PDF in `downloads/`) showed:

- 96% of pages in project-documentation PDF (`ТКР1`, 140 pages) have
  markdown-pipe-tables; the same is true for `Рыбоводные_нормы` (120 pages).
- Tagged-PDF (e.g. `Труды_ИБВВ`, `ТКР1`, `Рыбоводные_нормы`) carries
  structure-tree with `H1`/`H2` roles — 4462 elements in `Труды`, 18404 in
  `Рыбоводные_нормы`.
- `extract_text_with_positions` returns `TextItem` with full font / bbox /
  mcid metadata — a font-size outlier detector can recover headings even
  on non-tagged PDF.

The markdown-`str`-only shape loses structure (merged cells, header rows,
per-page headings, figure/table provenance) on every PDF in the corpus. The
structure is **available** in pdf-inspector 1.17 Python bindings; IR 0.1
chose not to carry it.

The personas from the original spec (RAG, document, pipeline) and the
emerging fourth persona (LLM/RAG-инжест с семантическим разбиением) all
benefit from a structural decomposition. DocLang as a public document
format will need this too.

## Decision

IR 0.2 adds a `blocks: list[HeadingBlock | TableBlock | FigureBlock | FormulaBlock]`
field on `PageResult`. The `markdown: str` field is preserved verbatim and
remains the authoritative text for the `md` and `chunks` renderers. The
`json` renderer serializes blocks alongside `markdown`.

Block types:

| Type | Source in pdf-inspector 1.17 | bbox | Notes |
|---|---|---|---|
| `HeadingBlock` | `extract_structure_elements` (tagged) + `extract_text_with_positions` (mcid-join) **or** font-size fallback (non-tagged) | yes | `source` field records detector; font-size fallback has known false positives (page numbers, captions) |
| `TableBlock` | `extract_pages_markdown` (markdown-pipe-table, parsed) | none in v0.2 | per-cell bbox requires Rust bindings (PR #481 may add it) |
| `FigureBlock` | reserved | n/a | no first-class upstream API in 1.17 Python; list is always empty in v0.2 |
| `FormulaBlock` | reserved | n/a | no upstream API; list is always empty in v0.2 |

`IR_VERSION` bumps from `"0.1"` to `"0.2"`. `to_dict`/`from_dict` round-trip
is preserved; `blocks` carry a `kind` tag for forward-compat.

`Bounding box` is a new `BBox` dataclass with `x0, y0, x1, y1` in PDF points,
CropBox-relative (the 1.17.0 frame; cf. CHANGELOG breaking change in
`extract_text_with_positions`).

## Alternatives considered

1. **Replace `markdown: str` with blocks-as-source-of-truth.**
   Rejected. Would require rewriting `chunks` renderer and breaking all
   existing golden-anchors (`test_p5.py`, `test_p27.py`,
   `test_renderers.py`). The user explicitly approved "any needed expansion
   of dataclass-IR, change boldly, no one is using it" — but expanding
   without breaking is the cheaper path and keeps the door open for
   blocks-as-source in a later version.

2. **Adopt DocLang as IR.**
   Rejected (see [doclang-investigation](../adr/0002-doclang-not-as-ir.md)
   in the same directory; this ADR does not duplicate that argument).

3. **Native Rust bridge via PyO3 to access `extract_tables_in_regions`
   (per-cell table bbox).**
   Deferred. pdf-inspector upstream is actively broadening the Python
   bindings (PR #446 mcid+role, PR #458 strip_headers_footers,
   PR #481 layout blocks); the right moment for our own PyO3 crate is
   after the upstream Python story stabilises, not before.

## Consequences

Positive:

- DocLang-style structural renderers (planned) have first-class data to
  work with, without re-parsing markdown.
- `chunks` renderer can be extended to split on `HeadingBlock` boundaries
  instead of `## ` regex — strictly better chunking for RAG.
- golden-anchor on structural blocks catches extraction regressions that
  pure markdown-anchor misses.

Negative:

- Adapter now does **3** pdf-inspector calls per document instead of 2
  (`process_pdf` + `extract_pages_markdown` + `extract_structure_elements`
  + `extract_text_with_positions`). On `ТКР1` (140 pages) the extra
  calls add ~10s. Acceptable for v0.2; revisit if the corpus shifts to
  >10k pages.
- font-size fallback for headings has false positives (page numbers, large
  captions). Documented in the field `source="font_size_fallback"` so
  downstream tools can filter. Mitigation: heuristic for "is this a page
  number?" in v0.3.
- `IR_VERSION` bump is a **breaking change** for any consumer holding
  `to_dict` output. There are no external consumers (project is pre-release);
  recorded here for honesty.

## Implementation

- `src/pdf_router/ir.py` — adds `BBox`, `HeadingBlock`, `TableBlock`,
  `FigureBlock`, `FormulaBlock`; `PageResult.blocks`; `IR_VERSION = "0.2"`.
- `src/pdf_router/adapters/pdf_inspector.py` — populates `headings` and
  `tables` from pdf-inspector 1.17 API; figures and formulas stay empty.
- `src/pdf_router/renderers/{md,chunks,json}.py` — unchanged for v0.2.
  `json` picks up blocks via `to_dict` automatically. `md` and `chunks`
  read `page.markdown` and ignore `blocks` (legacy behaviour preserved).
- `tests/test_ir_structural_blocks.py` — new; L1+L2+L3 anchors on p5 and
  `ТКР1` (slow marker).
- `tests/test_conventions.py:15` — `IR_VERSION == "0.2"`.

## References

- pdf-inspector 1.17.0 Python API: `extract_pages_markdown`,
  `extract_structure_elements`, `extract_text_with_positions`,
  `extract_text_in_regions` — shape verified at `/tmp/forge_fetch_*.txt`
  during the upstream audit.
- `tests/test_ir.py:54-61` — `page_bbox` opt-in pattern, mirrored for
  `blocks` (always present, opt-in only via `to_dict(include_page_bbox=…)`
  for the existing `page_bbox` field).
- `DESIGN.md` (legacy) — the spec that introduced IR 0.1; this ADR
  supersedes §4 (Intermediate Representation).
