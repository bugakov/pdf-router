# Custom OCR adapter on rapidocr (PP-OCRv6 det + PP-OCRv5 eslav rec)

## Destination

Built-in OCR adapter in `pdf-router` powered by **`rapidocr`** (RapidAI/RapidOCR, Python package on `onnxruntime`), using **PP-OCRv6 small detection** + **PP-OCRv5 eslav mobile recognition** for Russian-language PDFs (server-variant ONNX for eslav does not exist — corrected after the API probe). The adapter registers as `ocr_fallback_engine = "rapidocr"` in `RoutingConfig` and **replaces** `pdf_inspector_ocr` as the default — see TKT-OC-01 resolution for the operational details (5-engine dispatch table, no removals, no breaking changes, only the default changes).

The end state: `pdf-router --out ./out paper.pdf` on a Russian architectural PDF produces high-quality Russian markdown via the rapidocr adapter, with `page.needs_ocr` from pdf-inspector still driving the per-page decision to OCR.

**Vehicle changed from `oar-ocr` to `rapidocr`** (2026-09-07, TKT-OC-02 Finding 3): oar-ocr is a pure Rust crate with no Python bindings; building our own Rust extension would duplicate the pdf-inspector fork work we already rejected. rapidocr gives the same model family (PP-OCR ONNX via ONNX Runtime) as a plain pip dependency.

## Notes

- **Skills every session should consult:** `/research` (for model availability, training data, SOTA claims), `/prototype` (for shape exploration: "is this feasible on a 50-line adapter?"), `/grilling` and `/domain-modeling` (default for any open question)
- **Standing preferences for this effort:**
 - **No abstractions over real things.** A new module is added when the operation it wraps is a real library call, not when we "might want to swap" — the swap is already a config flag (`ocr_fallback_engine`)
 - **No silent fallbacks.** If the rapidocr engine or model fails to load, the page gets `error: ocr_load_failed`, not a fallback to a worse engine
 - **No network calls at OCR time.** rapidocr downloads models on first run from ModelScope into its own cache; after that, offline. `ocr_fallback_params` may pass `model_dir` if a fully pinned layout is needed later — v1 of the adapter uses rapidocr's own cache and does not manage model files itself
 - **One `ocr_page` integration point, no new protocol.** The adapter slots into the existing `ocr_page(path, page_num, config) -> (markdown, engine_name)` signature
 - **Tests on real Russian PDF, not just mocks.** The fixture is `/tmp/pzu_test.pdf` (architectural drawing, Russian text, currently OCRed via pdf_inspector)
- **Repo conventions to preserve:**
 - Flat functions in `src/pdf_router/`, not classes (per RT-05)
 - `routing.ocr_fallback_params` is the dict of engine-specific knobs (per RT-06)
 - Sidecar image layout from RT-12 is orthogonal — no changes there
 - Tests live in `tests/`, fast unit tests in the main run, slow integration tests marked `@pytest.mark.slow`
- **Current state of the code path this will modify:**
 - `src/pdf_router/ocr.py` — `_ocr_rapidocr()` joins `_ocr_pdf_inspector()`, `_ocr_tesseract()`, `_ocr_glm()`, `_ocr_deepseek()` in the dispatch. `_ocr_pdf_inspector()` stays (demoted to alternative)
 - `src/pdf_router/config.py` — `RoutingConfig.ocr_fallback_engine` default changes to `"rapidocr"`; `ocr_fallback_params` unchanged in shape
 - `router.toml.example` — documents all five engines, rapidocr as the uncommented default

## Decisions so far

- [TKT-OC-01 — destination scoping: rapidocr replaces pdf_inspector_ocr as default](tickets/TKT-OC-01-destination.md) — **RESOLVED 2026-09-07**: 5-engine dispatch, new default `rapidocr`, `_ocr_pdf_inspector` stays demoted, no breaking changes
- [TKT-OC-02 — pre-flight SOTA validation before any code](tickets/TKT-OC-02-sota-validation.md) — **RESOLVED 2026-09-07**: v6-det + v5-rec combination exists and is supported (per-component version mixing is a first-class pattern in rapidocr); v6 rec has NO Cyrillic, v5 eslav mobile is the Russian model (server variant does not exist — corrected after API probe); oar-ocr rejected as vehicle (Rust-only); CER measurement deferred to adapter implementation
- **rapidocr API probe completed** (2026-09-07, see TKT-OC-02 probe results) — the recipe works end-to-end on pzu p13: clean Russian ("Ведомость чертежей основного комплекта", "Ситуационная схема участка"), 98 text lines, avg conf 0.924, ~29s/page cold on CPU-only. Key API facts pinned:
 - `RapidOCR(params={...})` accepts dotted keys (`Det.ocr_version`, `Rec.lang_type`, ...) with **enum values** (`OCRVersion.PPOCRV6`, `LangRec.ESLAV`), not strings
 - `Det.lang_type` for v6 must be `LangDet.CH` (the v6 detector is a single multilingual model; resolver keys it as `ch_PP-OCRv6...` regardless of target language)
 - `engine(img)` takes a numpy RGB array (from `rasterize_page`), **not a PDF path** — the adapter rasterizes via pymupdf first, same as tesseract/deepseek paths
 - Result: `result.boxes` (N,4,2) already y-sorted, `result.txts`, `result.scores`, `result.elapse_list` [det, cls, rec]
 - `eslav` vs `cyrillic` rec on p13: near-identical (98 vs 100 lines, conf 0.924 vs 0.923) — pick `eslav` (East-Slavic specialised: ru/be/uk)
 - **Cyrillic server-variant ONNX does not exist** — only `mobile` for both eslav and cyrillic in v5 (server exists only for `ch`); the map's "eslav server" plan corrects to "eslav mobile"
 - Both rapidocr's pip onnxruntime and pdf-inspector's bundled ort coexist in one process (probe passed)
 - Warm engine init ~0.6s; per-page: native-light p1 13.8s, dense p13 28.5s, mixed p19 18.5s (det+rec split ~50/50)
 - Models auto-download from ModelScope on first use into `.venv/.../rapidocr/models/` (~17MB det + 7.7MB eslav rec), then offline
- **Vehicle change: oar-ocr → rapidocr** (2026-09-07) — see TKT-OC-02 Finding 3

## Not yet specified

- **rapidocr API surface for our adapter.** ✅ **PROBED 2026-09-07** — see Decisions so far. Constructor takes dotted-key `params` dict with enums; `engine()` takes numpy RGB; result has `boxes` (y-sorted), `txts`, `scores`. Remaining detail: exact param keys for skipping cls (`Global.use_cls=False` confirmed working) and whether `Det.model_type` accepts `tiny`/`medium` variants for speed tuning.
- **Model recipe details.** `eslav` vs `cyrillic`: probed on p13 — near-identical (98 vs 100 lines, conf 0.924 vs 0.923); `eslav` picked (ru/be/uk specialised). Detector variants (`tiny`/`medium` vs `small`): not yet probed — candidate for speed tuning if 13-28s/page proves too slow in practice.
- **Reading order for markdown assembly.** rapidocr returns boxes; we join lines top-to-bottom, left-to-right. For multi-column architectural drawings this will interleave columns. Acceptable for v1 (tesseract output has the same problem), or do we sort by column when `page is wide`? Decided empirically on pzu.
- **Performance budget.** ✅ **MEASURED 2026-09-07**: rapidocr warm per-page on pzu — native-light p1 13.8s, dense p13 28.5s, mixed p19 18.5s; init 0.6s. vs `pdf_inspector_ocr` 23.6s/page (garbled Russian) and tesseract ~50s/page. rapidocr is competitive on speed and decisively better on quality. Parallelism already exists in `core.py`; no further target set.
- **`routing.language` → rapidocr `LangRec` mapping edge cases.** What happens when `language` is unset (None)? Default to eslav (Russian corpus) or ch (rapidocr default)? What about mixed-language pages (Russian + English stamps on drawings)? The `ch` model covers ch/en/japan; eslav covers ru/be/uk + what English coverage? Needs a probe on real pages.
- **Model cache location.** rapidocr uses its own cache (defaults to somewhere under `~/.cache/`). Fine for v1. If we later want a pinned layout (e.g., for Docker images or CI), `model_dir` param exists — deferred.
- **Dependency weight.** ✅ **PROBED 2026-09-07**: pip `onnxruntime` (rapidocr's) and pdf-inspector's bundled `libonnxruntime.so` coexist in one process — no conflict observed. Models ~25MB total, cached under `.venv/.../rapidocr/models/` after first download.
- **Error UX.** When rapidocr import fails (not installed) or model download fails (offline first run), what does the user see? `OcrError` with engine name + install hint, not silent tesseract fallback. Wording decided during implementation.
- **Test strategy for the slow path.** Current `test_ocr.py` mocks `process_pdf_with_ocr` — needs a real-run slow test: rapidocr adapter on pzu page 13, assert Russian words present (e.g. "Фролов", "Ситуационная"), assert no garbled-mix output like "СитуационнаяОбщие". Marked `@pytest.mark.slow`, requires models downloaded once.
- **What `src_tool` value does the page carry.** `SrcTool("rapidocr")` — needs adding to the enum if not present, plus `to_dict`/`from_dict` round-trip stays intact.

## Out of scope

- Forking `pdf-inspector` to add a second `ModelManifest` — ruled out (maintenance burden, and moot now that rapidocr is the vehicle)
- ~~Switching to other OCR families (RapidOCR, Surya, GOT-OCR)~~ — **superseded**: rapidocr IS the vehicle now; Surya/GOT-OCR remain out of scope
- GPU-encoding optimization — CPU-only first, GPU later if measurements show it matters
- Fine-tuning PP-OCRv5 eslav on our specific corpus — out of scope; we trust the pretrained model
- New IR fields (per-page confidence, per-span bbox) — IR is at v0.2, schema additions are a separate map
- Replacing the `process_pdf_with_ocr` quality signal in `extract_ir` — `page.needs_ocr` is from pdf-inspector and stays. Our adapter gets called **when** needs_ocr=True, not for the decision itself
- The `oar-ocr` direct Rust integration — dead path, documented in TKT-OC-02 Finding 3
