# TKT-DX-05 — Quality bar: golden anchors on real docx

## Question

What measurably defines "качественный материал для LLM/RAG" for docx output
(user's destination, 2026-09-12)?

Sub-decisions:

1. which corpus files become golden anchors (candidates: the ФНС table-heavy
   document, a протокол with media, a титул);
2. which invariants are anchored: heading levels exact from styles, tables
   survive as pipe-tables with real rows, lists preserved, no empty chunks,
   images keyed to pages;
3. merged-cell policy for `TableBlock.rows` (OOXML `vMerge`/`gridSpan` → what
   row/cell content?);
4. fast/slow test split for docx tests (docx parse is cheap — all fast?).

Blocked by: TKT-DX-02, TKT-DX-03 (anchoring needs the contract fixed).

## Resolution

_Closed 2026-09-12 (grilling). vMerge repeat policy approved; corpus choice
corrected by the user: tests anchor on **project-documentation volumes
(большие тома)** — the ФНС file is not relevant to our tasks._

1. **Golden anchors** (probed 2026-09-12, real ground truth):
   - `kotel-zamechanna/archive/ВЫДАЧА_БМК Чижевщина/ИИ ред. формат/ИГИ ред.
     формат/1. Текст/Архив/00845.2024.07 - ИГИ.docx` — project-documentation
     volume: 25 tables / 112 rows, 9 media, 1.6 MB document.xml;
   - `kotel-zamechanna/archive/ВЫДАЧА_БМК Урицкого/ИИ ред. формат/ИГМИ ред.
     формат/ПР ИГМИ Урицкого.docx` — media-heavy volume: 8 media (12.2 MB),
     4 tables;
   - the протокол F2 (header-media case) + synthetic control (headings +
     custom style + table) as small unit-level anchors.
   ФНС file dropped (irrelevant per user). Note: tests resolve corpus paths
   like the PDF fixtures in `tests/conftest.py` — skip when missing.
2. **Invariants anchored:** table rows exact (25×… on ИГИ), vMerge unwrap
   correct, image files exist on disk and paths appear in markdown/chunks,
   chunks non-empty, `title` from `core.xml`, `source_format="docx"`.
3. **vMerge policy: repeat the merged value** in continuation cells — every
   row self-contained for retrieval (user-approved).
4. **All docx tests fast** (stdlib parse is cheap); slow marker reserved for
   any future OCR-adjacent work only.
