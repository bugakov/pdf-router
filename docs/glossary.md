# Glossary

The terms below are used throughout the codebase and the ADRs. Each term
points to the code that defines it.

## Adapter

A module that converts one upstream library's output into the IR. Two
adapters today: the pdf-inspector text path
(`src/pdf_router/adapters/pdf_inspector.py`, `extract_ir()`), always
loaded, and the native docx parser
(`src/pdf_router/adapters/docx.py`, ADR-0003 — stdlib `zipfile` +
ElementTree, zero dependencies). OCR engines are **flat functions** in
`src/pdf_router/ocr.py` (rapidocr, pdf_inspector_ocr, tesseract, glm_ocr,
deepseek_ocr) dispatched by `ocr_page()`; the retired `Adapter*` classes
were deleted per TKT-RT-14 (their empirical notes live in the flat
functions' docstrings).

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

## Format dispatch

The multi-format seam (`src/pdf_router/formats.py`): `detect_format()`
sniffs **magic bytes** (never the file extension), `get_inspector()` maps a
recognized `Format` to its inspector, `route()` is the single gate every
entry point funnels through. Unroutable input (djvu today, unrecognized
bytes) raises `UnsupportedFormatError` before any page work. Adding a
format = one enum member + one magic-byte branch + one registry entry;
nothing downstream changes.

## docx (native parse)

The DOCX strategy since [ADR-0003](adr/0003-docx-native-parse.md): a
stdlib parser (`zipfile` + ElementTree) reads the OOXML package directly —
no LibreOffice/pandoc conversion, no third-party library (research ticket
TKT-DX-01 disqualified every candidate on quality or dependency weight).
Structural claims are stated, not recovered: `w:pStyle` resolves through
`word/styles.xml` (built-in name or `outlineLvl`, `basedOn` chain walked
with a cycle guard — raw styleIds are never matched, the corpus
obfuscates them), `w:tbl` yields exact 2D rows (gridSpan expanded,
vMerge continuations **repeat** the restart value), `word/media/*`
becomes sidecar images. The document is pageless: one IR page
(`page=0`), `source_format="docx"`, `pdf_type="text_based"`, confidence
1.0. Limitations (accepted, absent from the corpus): no OCR for
scanned docx, no pagination, nested tables flatten into their parent
cell, EMF/WMF media stored verbatim.

## Inspector

The per-format adapter contract: one callable `path -> IR`
(`src/pdf_router/formats.py`, `Inspector`). Both inspectors conform —
pdf-inspector's `extract_ir()` and the docx adapter's `extract_ir()` —
registered in the same dict; `core.py` never imports an adapter directly.
Flat functions by design (RT-14 deleted the adapter classes).

## IR (Intermediate Representation)

The dataclass contract shared by adapters and renderers. Defined in
`src/pdf_router/ir.py`. `IR_VERSION` is a string and bumps on
non-backward-compatible changes; see ADR-0001 for the 0.1 → 0.2 bump and
[ADR-0003](adr/0003-docx-native-parse.md) for the 0.2 → 0.3 bump
(`Metadata.source_format`, `SrcTool.DOCX_ADAPTER` — the IR became
format-neutral).

## Markdown (page-level)

`PageResult.markdown: str` — the flat per-page markdown produced by the
format's inspector (PDF: `pdf_inspector.extract_pages_markdown`; DOCX:
the native adapter's body walk). Authoritative for the `md` and `chunks`
renderers. The `blocks` field is **advisory** — it does not override
`markdown`.

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

The orchestrator: flat `process_pdf(pdf_path, config)` in
`src/pdf_router/core.py:47` — detects the input format via
`formats.route()` and loads the document through the format's registered
inspector (PDF → the always-loaded pdf-inspector adapter), fans text/OCR
and image extraction out per page over a thread pool, and returns a
complete IR plus a `ProcessingReport`.

## Public API

The embedding surface: everything re-exported by `pdf_router/__init__.py`
from `src/pdf_router/api.py` — `convert` (one call → Markdown),
`process_pdf` (IR + report), `render_markdown`/`render_chunks`/`render_json`,
`write_outputs`, `load_config`, plus the IR/config types and the typed
errors (`ConfigError`, `ProcessingError`, `UnsupportedFormatError`).
Submodules are implementation details; `tests/test_api.py` pins the
contract (including the no-heavy-imports-at-startup rule).

## Sidecar images

Raster/vector images extracted next to the rendered material under
`<output_dir>/<doc-id>/images/page{N:04d}_{kind}_{idx:02d}.{ext}` where
doc-id = 12-hex SHA-256 of the absolute document path (TKT-RT-12 schema).
PDF: `extract_images.extract_page_images` (pymupdf, per page). DOCX:
`adapters/docx.py::extract_docx_images` (media parts copied verbatim,
whole document = page 1). Markdown placeholders (`![](images/...)`) and
written files come from one canonical enumeration — they cannot drift.
Paths stored in `PageResult.images` are relative to `output_dir`.

## SrcTool

The `SrcTool` enum records **which** tool produced a page's markdown
(`pdf_inspector` / `docx_adapter` / `pdf_inspector_ocr` / `tesseract` /
`glm_ocr` / `deepseek_ocr`). `docx_adapter` is tool-**category**-neutral:
the native stdlib implementation may be swapped without an IR bump
(TKT-DX-03). Defined at `src/pdf_router/ir.py:28`.

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
