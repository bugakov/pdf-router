# TKT-DX-08 — Implement the docx inspector (stdlib) + docs

## Question

_(task — the final build; every decision is already recorded.)_

Implement `src/pdf_router/adapters/docx.py` (native stdlib parser, ~100–150
lines) and wire it through the locked seam:

1. `src/pdf_router/ir.py` — `Metadata.source_format`, `SrcTool.DOCX_ADAPTER`,
   `IR_VERSION = "0.3"`, round-trip for the new key (TKT-DX-03).
2. `src/pdf_router/adapters/docx.py` — walk `w:body` in document order
   (`w:p`/`w:tbl`); heading level via `w:pStyle` → `word/styles.xml` →
   builtIn map; exact 2D `TableBlock.rows` with **vMerge = repeat value**;
   pipe-markdown from the same rows; `word/media/*` via body+header rels →
   sidecar `images/page0000_image_{idx:02d}.{ext}` (kind=`image`) +
   `![](images/...)` placeholders + `FigureBlock` (honest Nones, caption
   `Рис. N`); title from `docProps/core.xml`; `Metadata` per TKT-DX-03;
   one `PageResult(page=0)` (TKT-DX-02/04).
3. `src/pdf_router/formats.py` — `_INSPECTORS[Format.DOCX] = _docx_inspector`
   (lazy import, heavy-deps rule unaffected — stdlib only).
4. `src/pdf_router/cli.py` — `--info` through the front door (TKT-DX-06).
5. Tests (all fast, TKT-DX-05): golden anchors on ИГИ 00845 + ПР ИГМИ
   Урицкого (skip-if-missing via conftest), протокол F2, synthetic control;
   invariants: rows exact, vMerge repeat, images on disk + in markdown,
   chunks non-empty, title, `source_format="docx"`, round-trip.
6. Docs: README format paragraph + `docs/glossary.md` terms.
7. Verify: `uv run pytest -q -m "not slow"` green; e2e smoke on both volumes;
   embedding-style import test still clean.

Blocked by: TKT-DX-07 (ADR must exist first — formats.py docstring contract).

## Resolution

**Done 2026-09-12. Implemented, tested, verified — the destination is reached.**

- `src/pdf_router/adapters/docx.py` (~630 lines incl. docstrings): body walk
  (w:p/w:tbl/w:sdt), styles.xml resolution (outlineLvl → built-in name →
  basedOn chain, cycle-guarded), exact 2D rows (gridSpan expand, vMerge
  repeat), pipe markdown from the same rows, canonical image enumeration
  (body → headers/footers, dedup) shared by placeholders and
  `extract_docx_images`, captions (`Рис./Fig./Табл. N`), title from core.xml,
  `docx_info` for --info. Plus the IR bump, registry entry, core dispatch
  (docx skips the PDF text/OCR pass), and the --info door.
- Tests: `tests/test_docx.py` — 25 tests, all fast (1.9 s): synthetic OOXML
  control (obfuscated styles, basedOn chain, cycle guard, vMerge/gridSpan,
  body+header images, caption, title, malformed package, round-trip,
  renderers, facade/CLI doors) + golden volumes ИГИ 00845 (23 tables, 57
  headings, <1 s) and ПР ИГМИ Урицкого (8 media parts, placeholder→file
  agreement). Full fast suite: **160 passed**.
- Docs: README (PDF/DOCX header, docx paragraph, architecture, IR 0.3
  contract, layout) + glossary (Adapter, docx native parse, Inspector, IR,
  Markdown, Sidecar images, SrcTool).
- e2e smoke: CLI full run on ПР ИГМИ Урицкого → md/chunks/json + 8 sidecar
  images (jpeg/png), pipe tables from the согласование block.
- Deviations from the ticket text: sidecar page component is
  `page0001` (1-indexed, PDF convention) not `page0000`; anchors are ИГИ +
  Урицкого (TKT-DX-05 dropped ФНС); `to_dict` was found never tested with
  TableBlock/FigureBlock and was generalized (round-trip now block-generic).
