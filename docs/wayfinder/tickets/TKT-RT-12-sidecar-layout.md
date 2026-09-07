# TKT-RT-12 — Sidecar layout for extracted images

## Question

What is the exact on-disk path schema for the PNG files written by `extract_raster_photos` and `extract_vector_diagrams`? This needs to be locked before any renderer reads `page.images`, because the path format appears in `PageResult.images: list[str]`.

## Resolution

**Schema:** `<output_dir>/<doc-id>/images/page{N:04d}_{kind}_{idx:02d}.{ext}`

**Confirmed specifics:**
- `<doc-id>` = SHA-256 of the absolute PDF path, first 12 hex chars. No title-based slug (decided against — titles can be non-deterministic, non-unique, or contain characters problematic for filenames).
- `<kind>` ∈ `{photo, diagram}`. No third kind — `FormulaBlock` in IR 0.2 is always empty (no pdf-inspector 1.17 API), so nothing to extract.
- `<ext>` matches the **source format** from `pymupdf.Page.extract_image(xref).ext` (typically `png`, `jpeg`, `jp2`). Diagram outputs are always rendered as `png` regardless of source.
- `PageResult.images` stores **relative** paths from `output_dir` (e.g. `abcdef123456/images/page0001_photo_00.png`), not absolute. Movable.

**Correction vs original decision:** the original schema said "always `.png`". Changed after empirical testing on `ПЗУ.pdf` (pzu): JPEG source images would otherwise be transcoded to PNG (lossy→lossless) wasting disk. Now we keep the source format; diagrams stay `.png` since they are rendered, not decoded.

**Not resolved (deferred to v0.4+):**
- `<doc-id>` slug-from-title as an alternative when title is available. Not needed now; SHA-256 is stable and collision-resistant.

**RT-12 is resolved.**

## Approach

Three candidates, each with concrete trade-offs:

### A. Flat under `images/`

```
<output_dir>/images/page{N}_photo_{idx}.png
<output_dir>/images/page{N}_diagram_{idx}.png
```

- **Pro:** simplest possible path; one directory; easy to glob.
- **Con:** if the source PDF has no title or has a generic title (`doc.pdf`), all output for all PDFs collides in the same `images/` directory unless we prefix with the doc id.

### B. Per-document subdirectory

```
<output_dir>/<doc-id>/images/page{N:04d}_photo_{idx:02d}.png
<output_dir>/<doc-id>/images/page{N:04d}_diagram_{idx:02d}.png
```

where `<doc-id>` is either the PDF's metadata title (slug-ified) or a hash of the file path when title is missing.

- **Pro:** collisions impossible across documents; zero-padded numbers sort lexically.
- **Con:** slug-from-title is heuristic; empty title → fallback to hash.

### C. Per-page subdirectory

```
<output_dir>/<doc-id>/images/page{N:04d}/{kind}_{idx:02d}.png
```

- **Pro:** very clean per-page listing.
- **Con:** many small directories; tools that walk `images/*.png` need a recursive glob.

## Recommendation

Start with **B** (per-document subdirectory, zero-padded). The cost is one extra `mkdir -p` per document; the benefit is correctness under concurrent or batched use, which is the actual deployment shape for a RAG indexer.

Lock the following specifics:
- Page number: zero-padded to 4 digits (`page0042`), so filenames sort correctly even across documents with different page counts.
- Index within page: zero-padded to 2 digits (`_00`, `_01`, ...). 99 images on a single page is absurd; 2 digits is enough headroom.
- Kind: literal `photo` for raster, `diagram` for vector. No `image` (too generic), no `fig` (ambiguous with figure caption text).
- Extension: always `.png`. No `.jpg` even if the source was JPEG — PNG is lossless and avoids a quality question.
- `<doc-id>` derivation:
  1. If `ir.metadata.title` is non-empty and slug-safe: `<title-slug>`.
  2. Else: SHA-256 of the absolute file path, first 12 hex chars.
- `PageResult.images` stores **relative** paths from `output_dir`, not absolute paths. Rationale: `output_dir` may move (renamed, mounted on a different volume); relative paths survive that.

## Notes

- This is a **grilling** ticket (HITL). The path schema is small but sticky — once renderers start emitting paths and downstream RAG indexers start consuming them, changing the format breaks every saved artifact. Lock it before any render-side code writes these paths.
- The `doc-id` slug-from-title rule is itself a small sub-decision: is slug-ification just `re.sub(r"\W+", "-", title.lower()).strip("-")[:80]` or do we need unicode-folding? Russian titles have cyrillic; some tools choke on non-ASCII filenames. This deserves its own 30-second grill once B is confirmed.

## Blocking

None — takeable now.