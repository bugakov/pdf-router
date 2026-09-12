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

_(open)_
