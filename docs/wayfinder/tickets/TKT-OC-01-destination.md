# TKT-OC-01 — Destination scoping for the rapidocr adapter

## Question

What does "the rapidocr adapter replaces pdf_inspector_ocr as default" mean operationally in `RoutingConfig`? Specifically:

1. Is the new `ocr_fallback_engine = "rapidocr"` registered as a **new** entry alongside `pdf_inspector_ocr`, `tesseract`, `glm_ocr`, `deepseek_ocr`, or does it replace one of those in the dispatch table?
2. When `ocr_fallback_engine` is unset in `router.toml`, what is the new default? `rapidocr` (our new adapter), or `tesseract` (works without ONNX runtime), or `pdf_inspector_ocr` (status quo)?
3. Does the old `pdf_inspector_ocr` entry stay in the dispatch table, or is it removed from `ocr.py` and `_ocr_pdf_inspector()` deleted?
4. Does `router.toml.example` need to swap the commented example, or add the new one alongside?
5. Migration: do existing user configs that have `ocr_fallback_engine = "pdf_inspector_ocr"` continue to work (compat), or do we bump a version and require update?

## Resolution

_Closed 2026-09-07, mostly collapsed by TKT-OC-02 findings (rapidocr is a pip dependency, not a Rust build):_

1. **New entry alongside.** `"rapidocr"` joins the dispatch table in `ocr.py` as a fifth engine. Nothing is removed from the table.
2. **New default = `rapidocr`.** The `ocr_fallback_engine` default in `RoutingConfig` changes from `pdf_inspector_ocr` to `rapidocr`. Rationale: pdf_inspector_ocr cannot recognize Russian at all (its pinned PP-OCRv6-small dict has zero Cyrillic), and rapidocr installs as a plain pip wheel with `onnxruntime` — no `.runtime/libpdfium.so` + `ort` path juggling, which was the single biggest usability failure of `pdf_inspector_ocr` in practice.
3. **`_ocr_pdf_inspector` stays** (demoted, not deleted). It remains in the dispatch table for users on CJK-heavy corpora where the PP-OCRv6-small ch/en model is actually the right tool, and for anyone who already has the pdf-inspector runtime set up. Cost of keeping it: one function + its test.
4. **`router.toml.example` documents all five engines**, with `rapidocr` as the uncommented default and the others commented as alternatives. The `rapidocr` param block documents the model recipe (Det PP-OCRv6 small + Rec PP-OCRv5 eslav mobile for `language: ru`).
5. **Compat: no breaking change.** The old value `pdf_inspector_ocr` keeps working (entry stays in dispatch). No config version bump. Users who explicitly set `pdf_inspector_ocr` in their `router.toml` are unaffected; only the *default* changes.

Additional operational decisions folded into this resolution:

- **Model recipe mapping.** `ocr_fallback_params.rec_language` (default derived from `routing.language`): `"ru"` → `LangRec.ESLAV` + `ModelType.MOBILE` (mobile is the only variant for eslav/cyrillic; `SERVER` raises `Unsupported configuration` — corrected after the API probe); `"en"` → `LangRec.EN` + `MOBILE`; `"ch"`/CJK → `LangRec.CH`; other Latin → `LangRec.LATIN`. `Det` stays `PP-OCRv6 small` for all languages (detection is script-agnostic).
- **No conditional default.** The default is `rapidocr` unconditionally, not "rapidocr if models present". A missing model produces a clear error (`OcrError` with the engine name), not a silent engine swap — consistent with the standing preference "no silent fallbacks".
- **Markdown output.** rapidocr returns text lines with boxes; the adapter assembles page markdown by joining recognized lines in reading order. No layout reconstruction beyond line order in v1 of the adapter.
