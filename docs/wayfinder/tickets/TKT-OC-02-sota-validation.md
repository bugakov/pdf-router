# TKT-OC-02 — Validate SOTA claim before writing code

## Question

The user asserted: "PP-OCRv6 detection + PP-OCRv5 recognition is SOTA for Russian text recognition as of 2026, and the combination works on a single oar-ocr ONNX runtime." Before writing any code, validate the factual basis:

1. **Do both models exist on Hugging Face / PaddleOCR / GreatV/oar-ocr?**
2. **Is PP-OCRv5 recognition actually trained on Russian Cyrillic?**
3. **Is the `oar-ocr` Rust crate (v0.9.1) able to load and run both models together?**
4. **Empirical evidence on our actual corpus (`pzu_test.pdf`, CER vs tesseract rus+eng)**

This is a `research` (AFK) ticket — resolved by running the comparison, not by arguing about it.

## Claim

_Assigned to forge, 2026-09-07._

## Resolution

_Research completed 2026-09-07. Result: **SOTA-claim PARTIALLY VALID, but the proposed vehicle (`oar-ocr`) is NOT usable from Python. `rapidocr` is the right vehicle instead.**_

### Finding 1 — PP-OCRv6 det + PP-OCRv5 rec: combination EXISTS and is a supported pattern

- **PP-OCRv6 detection** (`tiny`/`small`/`medium`) and **PP-OCRv5 recognition** both exist as converted ONNX models, maintained by RapidAI and hosted on ModelScope (`RapidAI/RapidOCR` repo, `models` directory).
- RapidOCR's own docs demonstrate **mixing detector/recognizer versions** as a first-class supported pattern: the default config in `rapidocr>=3.9.0` is literally `Det: PP-OCRv6 small` + `Cls: PP-OCRv4` + `Rec: PP-OCRv6 small`, and the docs show how to override any component independently (`Det.ocr_version`, `Rec.ocr_version` etc.).
- Therefore "v6 det + v5 rec" is not an exotic hack: the inference framework explicitly supports per-component version mixing.

### Finding 2 — Russian Cyrillic: the story is more nuanced than the rumor

- **PP-OCRv6 rec** (`small`/`medium`): the supported language list **does NOT include `cyrillic`/`ru`/`eslav`** — only Latin-script and CJK languages (ch, en, japan + ~50 Latin languages). Footnote in official model list: "In PP-OCRv6, regardless of which `lang_type` you specify, the model is the same. Models are differentiated only by `model_type`."
- **PP-OCRv5 rec `cyrillic`** (`rapidocr>=3.5.0`): **EXISTS** — `mobile` variant only. Covers Russian, Belarusian, Ukrainian, Serbian (cyrillic), Bulgarian, Mongolian + ~30 more cyrillic-script languages per footnote 3.
- **PP-OCRv5 rec `eslav`** (`rapidocr>=3.3.0`): **EXISTS** — `mobile` variant only. Footnote 6: covers Russian, Belarusian, Ukrainian only. This is the East-Slavic-specialized model.
- The rumor is therefore **confirmed in substance**: v6 dropped the cyrillic/eslav rec heads; for Russian recognition you go to v5.
- **Correction (2026-09-07, API probe on rapidocr 3.9.2):** there is **no `server` variant for ANY cyrillic rec model** — neither `eslav` nor `cyrillic`, in any engine backend (onnxruntime, openvino, mnn, paddle, torch). `default_models.yaml` lists only `eslav_PP-OCRv5_rec_mobile` / `cyrillic_PP-OCRv5_rec_mobile`. Requesting `ModelType.SERVER + LangRec.ESLAV` fails with `Unsupported configuration` (hard error, no silent fallback). Earlier statement that "eslav server exists" was wrong — it misread the official docs' model matrix, where the `server` column for v5 rec is populated only for `ch`.

### Finding 3 — `oar-ocr` as a vehicle: REJECTED

- The `oar-ocr` GitHub repo master docs (v0.6.x) list `cyrillic_pp-ocrv5_mobile_rec.onnx` (7.7MB) + `ppocrv5_cyrillic_dict.txt`, and `eslav_pp-ocrv5_mobile_rec.onnx` — so the **models exist in that ecosystem too**.
- However `oar-ocr` is a **pure Rust crate with no Python bindings**. pdf-router is a Python project. Using it would mean either (a) writing a Rust extension ourselves (duplicating pdf-inspector's fork work we already rejected), or (b) abandoning the Python ecosystem.
- Additionally, pdf-inspector 1.17.0 pins `oar-ocr = 0.9.1` and hardcodes manifest `PP_OCR_V6_SMALL` with SHA-256 checks — no way to swap models without forking (established in earlier research).
- **Verdict: `oar-ocr` path is dead for us. The right vehicle is `rapidocr`** — a mature, battle-tested Python package (RapidAI/RapidOCR) built directly on `onnxruntime` (the same inference engine family as oar-ocr), with automatic model download from ModelScope, per-component version mixing, and explicit `cyrillic`/`eslav` language support.

### Finding 4 — Empirical probe on pzu_test.pdf (API trial, 2026-09-07)

Measured during the API probe (rapidocr 3.9.2, CPU-only, dpi=150, pzu p13 — architectural drawing with title-block stamps):

| Engine | Time/page | Output quality |
|---|---|---|
| `pdf_inspector_ocr` (PP-OCRv6 small, dict без кириллицы) | 23.6s | Галлюцинации и склейки: «СитуационнаяОбщие объекта Строительство схема данные» |
| `tesseract` (rus+eng) | ~50s | Каракули на CID-fonts |
| **`rapidocr` (Det v6 small + Rec v5 eslav mobile)** | **28.5s (warm), init 0.6s** | **Чистый русский**: «Ведомость чертежей основного комплекта», «Ситуационная схема участка», «План организации рельефа вертикальной планировкой» — 98 txts, avg conf 0.924 |

`eslav` vs `cyrillic` rec on the same page: near-identical (98 vs 100 txts, conf 0.924 vs 0.923).

**No ort conflict**: pip `onnxruntime` (rapidocr) and pdf-inspector's bundled ort dylib coexist in one process — verified by importing both and running `process_pdf_with_ocr(mode='off')` alongside `RapidOCR(...)`.

**API facts locked in for the adapter:**

1. Constructor: `RapidOCR(params={...})` — dotted keys with **enum values** (`OCRVersion.PPOCRV6`, `LangRec.ESLAV`), not strings.
2. `Det.lang_type` for v6 must be `LangDet.CH` (single multilingual detector; resolver rejects `MULTI` for v6).
3. Input: **numpy RGB array** (`engine(img)`), not a PDF path — rasterization stays in our `rasterize_page` (pymupdf), same as the tesseract adapter.
4. Result: `boxes (N,4,2)` y-sorted, `txts`, `scores`, `elapse_list [det, cls, rec]`.
5. Invalid combinations (e.g. `SERVER + ESLAV`) raise `Unsupported configuration` — hard error, no silent fallback.
6. Models auto-download from ModelScope on first use into `site-packages/rapidocr/models/`, then fully offline.

_Full CER numbers against ground truth are still deferred to the adapter implementation ticket (same fixture, both engines). The architecture-relevant validation (combination exists, Russian quality is dramatically better, no runtime conflicts) is complete._

### Consequences for the map (destination redraw)

The destination in `docs/wayfinder/oar-ocr-adapter.md` must be updated:

1. **Vehicle changed**: `oar-ocr` → `rapidocr` (Python, ONNX Runtime).
2. **Engine name**: `ocr_fallback_engine = "rapidocr"` (new adapter), NOT `"oar_ocr"`.
3. **Model recipe for Russian**: `Det: PP-OCRv6 small` + `Rec: PP-OCRv5 eslav (mobile)` — the only cyrillic-capable variant that exists. Prefer `eslav` over `cyrillic` for our corpus because it is East-Slavic-specialized (ru/be/uk exactly) rather than a 35-language generic cyrillic head; empirical probe on pzu p13 showed near-identical output (eslav: 98 txts, conf 0.924; cyrillic: 100 txts, conf 0.923), so the choice is a specialization bet, not a capacity one. Default `language: ru` maps to eslav; fallback chain for non-Russian: `ch` (CJK), `en`, `latin`.
4. **`pdf_inspector_ocr` demotion** stands, and is now even easier to justify: its PP-OCRv6-small-only manifest cannot recognize Russian at all.
5. **TKT-OC-01 questions mostly collapse** — rapidocr is a pip-installable dependency with its own model cache; no Rust, no wheel building, no runtime lib paths.

### References

- RapidOCR model list: https://rapidai.github.io/RapidOCRDocs/main/model_list/ (PP-OCRv4/v5/v6 matrices, language footnotes)
- RapidOCR default config `rapidocr>=3.9.0`: Det PP-OCRv6 small + Rec PP-OCRv6 small (demonstrates per-component mixing)
- GreatV/oar-ocr docs (master): `cyrillic_pp-ocrv5_mobile_rec.onnx`, `eslav_pp-ocrv5_mobile_rec.onnx` — models exist in that ecosystem too
- pdf-inspector 1.17.0 source (local `/tmp/pdf_inspector_src/`): `src/vision/models.rs:62-95` (pinned manifest + SHA-256), `src/vision/pipeline.rs:649-690` (engine cache keyed on manifest digests)
