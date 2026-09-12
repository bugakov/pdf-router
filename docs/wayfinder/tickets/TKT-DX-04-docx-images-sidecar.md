# TKT-DX-04 — docx images to the sidecar

## Question

docx carries images in `word/media/*`. How do they reach the RAG pipeline?

Sub-decisions:

1. sidecar layout — reuse the RT-08 schema
   (`<output_dir>/<doc-id>/images/page{N:04d}_{kind}_{idx:02d}.{ext}`) with
   docx page semantics from TKT-DX-02?
2. `FigureBlock` for docx — what fills `bbox` (docx has no coordinates),
   `caption` (nearest paragraph matching `Рис. N`?), `image_index`?
3. inline images in page markdown — placeholder syntax
   (`![](images/...)`) so the `chunks` renderer carries image paths per chunk
   (PDF path does this via `page.images`)?
4. header/footer media — extract or skip?

Blocked by: TKT-DX-02 (page keying).

## Resolution

_Closed 2026-09-12 (grilling). All four approved, with the user's framing:_

> промежуточные результаты docx хранить в PDF-виде можно, но не обязательно —
> подгонка docx к PDF даже вредна; финальные таргеты те же — материалы для
> LLM/RAG/векторных баз.

I.e. the IR contract serves the **renderers** (the final RAG targets), not PDF
mimicry. docx fields stay honest (`None` where PDF has coordinates), never
faked to look PDF-shaped.

1. **Sidecar layout reuses the RT-08 schema** with page 0:
   `<output_dir>/<doc-id>/images/page0000_image_{idx:02d}.{ext}` — kind is
   `image` (no photo/diagram classification; that is a PDF-render heuristic,
   not a docx concept). Original extension preserved (png/jpeg/...).
2. **`FigureBlock`:** `bbox=None` (docx has no coordinates — honest None,
   not a fake box), `caption` = nearest paragraph matching `Рис. N` /
   `Fig. N`, `image_index=None` (no xref concept).
3. **Markdown placeholders:** `![](images/page0000_image_00.png)` inline in
   page markdown, so `chunks` carries image paths per chunk (same consumer
   contract as PDF's `page.images`).
4. **Header/footer media: extracted** (corpus F2 has a header logo; rels of
   body + headers both walked — TKT-DX-01 finding).
