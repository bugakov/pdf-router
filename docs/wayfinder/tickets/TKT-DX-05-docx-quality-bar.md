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

_(open)_
