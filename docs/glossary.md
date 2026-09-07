# Glossary

The terms below are used throughout the codebase and the ADRs. Each term
points to the code that defines it.

## Adapter

A module that converts one upstream library's output into the IR. The
always-loaded adapter is `AdapterPdfInspector`
(`src/pdf_router/adapters/pdf_inspector.py:298`). OCR adapters (tesseract,
glm-ocr, deepseek-ocr, pdf_inspector_ocr) are loaded **only** when the
corresponding `[ocr.*]` section is present in `router.toml`. An adapter
that is not configured is not even imported (`src/pdf_router/core.py:84-93`).

## BBox

A 2-D axis-aligned rectangle in PDF points (1 pt = 1/72 inch). Four floats:
`x0, y0, x1, y1` in pdf-inspector's **CropBox-relative** frame (1.17.0+).
Defined at `src/pdf_router/ir.py:65`.

## Block

A structural element on a page: `HeadingBlock`, `TableBlock`, `FigureBlock`,
`FormulaBlock`. Lives on `PageResult.blocks` since IR 0.2. See
[ADR-0001](adr/0001-ir-structural-blocks.md).

## CropBox

A rectangle in PDF page-coordinate space defining the visible region of a
page. pdf-inspector 1.17+ returns positions relative to `MediaBox ∩
CropBox`; this changed in 1.17.0 (CHANGELOG).

## IR (Intermediate Representation)

The dataclass contract shared by adapters and renderers. Defined in
`src/pdf_router/ir.py`. `IR_VERSION` is a string and bumps on
non-backward-compatible changes; see ADR-0001 for the 0.1 → 0.2 bump.

## Markdown (page-level)

`PageResult.markdown: str` — the flat per-page markdown produced by
`pdf_inspector.extract_pages_markdown`. Authoritative for the `md` and
`chunks` renderers. The `blocks` field is **advisory** — it does not
override `markdown`.

## MCID (Marked Content ID)

An integer that PDF tagging uses to associate content streams with
structure-tree elements. `StructureElement.mcid` joins to `TextItem.mcid`
(`extract_text_with_positions`). Used to recover heading text from the
structure-tree when only roles are available.

## Persona

A target user type, named in the original spec: **RAG** (chunks renderer
for vector DB ingestion), **document** (md renderer for human reading),
**pipeline** (json renderer for machine consumption). IR 0.2 enables a
fourth emerging persona: **LLM-инжест** (semantic-chunked RAG) that needs
structural blocks, not just text.

## Renderer

A pure function `(ir, *, config) -> str`. Three renderers: `md`
(`src/pdf_router/renderers/md.py:12`), `chunks`
(`src/pdf_router/renderers/chunks.py:78`), `json`
(`src/pdf_router/renderers/json_renderer.py:11`). Renderers may not import
adapters (`tests/test_conventions.py:37` enforces this).

## Router

The orchestrator class (`src/pdf_router/core.py:59`) that loads a PDF once
via the always-loaded pdf-inspector adapter, fans OCR out per page over
the configured `[ocr.*]` chain, and returns a complete IR.

## SrcTool

The `SrcTool` enum records **which** tool produced a page's markdown
(`pdf_inspector` / `pdf_inspector_ocr` / `tesseract` / `glm_ocr` /
`deepseek_ocr`). Defined at `src/pdf_router/ir.py:28`.

## Structure-tree

The tagged-PDF accessibility tree. Returned by
`pdf_inspector.extract_structure_elements` as `list[StructureElement]`
with `page`, `mcid`, `role`. Roles observed in our corpus: `P` (paragraph),
`Span`, `H1`/`H2` (rare; H3..H6 not used in the corpus), `Figure`, `Link`,
`LBody`. Always `[]` for non-tagged PDF.

## Tagged PDF

A PDF that carries a structure-tree (a.k.a. "tagged" for accessibility).
Most modern scientific journals, monographs produced from LaTeX, and
project documentation exported from CAD are tagged. The corpus in
`downloads/` is majority-tagged; the toy test PDFs (`p5.pdf`, `p27.pdf`)
are not.

## TextItem

One text run in `pdf_inspector.extract_text_with_positions`. Fields used
in IR 0.2: `text`, `font`, `font_size`, `x`, `y`, `width`, `height`,
`page`, `mcid`, `is_bold`.
