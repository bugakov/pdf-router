# TKT-DJV-04 — Hidden text layer policy

## Question

djvu files may carry a hidden text layer (TXTz/TXTa from scan-time OCR).
Prevalence/quality evidence arrives from TKT-DJV-01. Policy questions:

- when the layer exists and passes `looks_like_text` — is it **native
  text** (skip OCR) or still re-OCRed for uniformity?
- which `SrcTool` records it (new member, e.g. `DJVU_TEXT_LAYER`, vs
  reuse of an existing one);
- metadata honesty: how the IR says "text from the djvu layer, not our
  OCR";
- what happens when the layer exists but fails the quality gate (strip?
  OCR fallback as usual?);
- whether the layer even survives the chosen strategy (A/B: probably lost
  → this ticket shrinks to a footnote; C: first-class).

Blocked by: TKT-DJV-02 (shape depends on the strategy); evidence from
TKT-DJV-01.

## Resolution

**Resolved (grilling, 2026-09-12).** Confirmed as a single package:

1. **Native path (already fixed by TKT-DJV-03):** a layer page passing
   `looks_like_text` keeps its layer text and skips OCR; a garbage layer →
   `ocr_reason="low_text_quality"` → OCR fallback. Rejected: re-OCRing
   everything for uniformity — spending 42–70 min/book of OCR to discard
   free high-quality text (3/33 files, 97–98% alnum, word-level zones)
   violates determinism and minimal-resources.
2. **New `SrcTool.DJVU_TEXT_LAYER`** (`"djvu_text_layer"`). Reuse
   rejected: `PDF_INSPECTOR` lies (a Rust pdf engine), OCR engines lie
   (no OCR happened), `DOCX_ADAPTER` lies. Precedent: docx added
   `DOCX_ADAPTER` rather than reusing a pdf member
   (`src/pdf_router/ir.py:43`).
3. **Metadata honesty = three existing carriers, zero new fields:**
   `source_format="djvu"` (container), per-page
   `src_tool=DJVU_TEXT_LAYER` (text provenance — a file-level `src_tool`
   does not exist; provenance is per-page only, `src/pdf_router/ir.py:55-76`
   vs `:174`), and `pages_needing_ocr` EXCLUDES layer pages (they do not
   need OCR). No `pages_with_layer` list — per-page `src_tool` in the
   JSON IR already answers "which pages had layers" (flat, minimal).
4. **`blocks=[]` from the djvu inspector, always** — no structural data;
   blocks are advisory and the empty list is legal per contract
   (`src/pdf_router/ir.py:13-17`, `:166-167`).
5. **IR bump 0.3 → 0.4** (closes the map's "Not yet specified" item): new
   `source_format` value `"djvu"` + new `SrcTool` member. Honesty
   argument: an old reader rebuilds pages via `SrcTool(value)`
   (`src/pdf_router/ir.py:330`) and would crash on `"djvu_text_layer"`
   with `ValueError` — the bump makes that contract visible (the version
   travels in `to_dict`, `src/pdf_router/ir.py:230`; module docstring
   updated).
6. **The layer is first-class under strategy C:** text is read directly
   from the djvu file, independent of the ddjvu raster — nothing is lost
   (unlike A/B). Mechanics (`djvused output-txt` vs `djvutxt --page`) are
   implementation detail (TKT-DJV-10).

Grounded mechanics note: the existing text pass already performs the
provenance swap correctly — a layer page that passes the gate returns
early with the inspector's `src_tool` intact (`src/pdf_router/core.py:212`);
a failing page gets `src_tool` overwritten by the OCR engine, set before
the call so it is honest even on failure (`src/pdf_router/core.py:218-224`).
Zero changes needed in the text pass.
