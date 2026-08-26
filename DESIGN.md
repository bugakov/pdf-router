# PDF Router — Design Spec v0.1

**Status:** Implemented through 22 grilling questions, 2026-08-26.
**Audience:** Implementation agent (this or a fresh session). One file to restore full context.

---

## 1. Purpose

Ultra-minimal, deterministic, deploy-anywhere PDF → LLM-ready Markdown router.
Primary input class: scientific journals, monographs, project documentation.
Common traits: scanned + native mixed, multi-column, tables, occasional images.
Target: free, CPU-only, **predictable behaviour across hosts**.

## 2. Core principles (non-negotiable)

- **Determinism:** same PDF + same config + same library version = same output. No silent auto-tuning, no hidden fallbacks.
- **Minimalism:** every module opt-in through config. If a feature is not in config, it is not run.
- **Decoupling:** adapters and renderers are independent. They share only the IR type.
- **Graceful degradation:** one failing page never aborts the pipeline.

## 3. Stack (frozen)

- Python ≥ 3.12 (3.14 not required, `uv pip` resolves compatible wheels)
- `pdf-inspector` (Rust napi) ≥ 1.17 — core extraction + native Markdown
- `pymupdf` ≥ 1.27 — bbox via `extract_text_with_positions` (v2 region-routing)
- `tomllib` (stdlib) — config reading
- No cache, no progress bar, no `python-json-logger` — stdlib `logging` only

**Optional extras (installed only when needed):**
- `[tesseract]` → `pytesseract` ≥ 0.3
- `[cloud]` → `requests` ≥ 2.31, `zai-sdk` ≥ 0.2 (the PyPI package `zai` is an empty placeholder — the real SDK is `zai-sdk`)
- `[dev]` → `pytest` ≥ 8

## 4. Intermediate Representation (IR)

```python
{
  "metadata": {
    "pdf_type": "text_based" | "scanned" | "image_based" | "mixed",
    "page_count": int,
    "confidence": float,
    "processing_time_ms": float,
    "title": str | None,
    "is_complex_layout": bool,
    "has_encoding_issues": bool,
    "pages_with_tables": list[int],    # 1-indexed (per pdf-inspector)
    "pages_with_columns": list[int],   # 1-indexed
    "pages_needing_ocr": list[int],    # 1-indexed
    "ocr_reasons_by_page": list[{page: int, reasons: list[str]}],  # 1-indexed
  },
  "pages": [                            # per-page, 0-indexed
    {
      "page": int,                     # 0-indexed
      "markdown": str,
      "needs_ocr": bool,
      "ocr_reason": str | None,
      "src_tool": "pdf_inspector" | "pdf_inspector_ocr" | "tesseract" | "glm_ocr" | "deepseek_ocr",
      "page_bbox": [w, h] | None,      # pt, opt-in via flag
      "error": str | None,             # set on per-page failure
    },
    ...
  ]
}
```

**Indexing convention (locked):** global lists (`pages_with_*`, `pages_needing_ocr`) are **1-indexed** to match pdf-inspector. `pages[].page` is **0-indexed** to match Python iteration.

**Why this shape:** pdf-inspector's `PdfResult` already exposes everything we need in `metadata`. We add `pages[]` because `PdfResult.markdown` is concatenated and we need per-page. We add `src_tool` to make provenance explicit. We add `error` to support graceful degradation.

## 5. Orchestration (locked)

One call to `pdf_inspector.process_pdf(path)` — single document load, detection + extraction + Markdown in one Rust pass. Then **fan-out per page** by `pages_needing_ocr`:

- For each `p` in `pages_needing_ocr` (1-indexed): run the configured OCR adapter; on failure or empty result mark `error` and keep the empty/garbled Markdown from pdf-inspector (do **not** silently substitute).
- Multiple `[ocr.*]` sections form an ordered **fallback chain** (TOML order = priority): per page, adapters are tried in order and the first non-empty result wins; `src_tool` records the winner. `ProcessingReport` distinguishes `pages_skipped` (no adapter configured → `error="no_ocr_configured"`) from `pages_failed` (adapters configured, all failed).
- Text-based pages keep pdf-inspector Markdown untouched.

**Why:** `process_pdf` is the cheapest correct entry point. `classify_pdf` is a redundant call. Region-routing is v2.

## 6. Config (TOML, locked)

Single file. `router.toml` lives **in the working directory or `--config` path**. **Always gitignored**. Holds API keys, OCR paths, everything. No env-variable indirection — that was rejected for the cloud keys.

```toml
# router.toml — gitignored, not committed
[router]
log_level = "info"        # debug | info | warning | error; default "warning"

[ocr.pdf_inspector_ocr]
pdfium_lib_path = "/abs/path/libpdfium.so"
ort_dylib_path = "/abs/path/libonnxruntime.so"
# optional:
# model_cache_dir = "~/.cache/pdf-router/models"
# offline = false

[ocr.tesseract]
languages = ["rus", "eng"]
# optional:
# bin = "/usr/bin/tesseract"
# dpi = 200

[ocr.glm_ocr]
api_key = "sk-..."                 # NOT via env, by design
base_url = "https://api.z.ai/api/paas/v4"
# optional:
# model = "glm-ocr"
# timeout = 60
# dpi = 200

[ocr.deepseek_ocr]
api_key = "sk-..."
base_url = "https://api.novita.ai/openai"
model = "deepseek/deepseek-ocr-2"
# optional:
# upload_url = "https://uguu.se/upload"  # default; 48h retention. catbox.moe
#   is blocked from this host (412 "Invalid uploader"); field names are
#   auto-detected per host if you switch anyway.
# prompt = "OCR this image."  # terse; markdown-style prompts trigger
#   repetition loops on dense drawing pages (live-tested 2026-08-26)
# timeout = 60
# max_tokens = 4096
# dpi = 200

[renderers]
# Which renderers run is chosen via --render (CLI only); no `enabled` key here.
# Default: all three on.
# output_dir = "out/"  # default: ./out/
# page_markers = true  # insert <!-- page N --> in md renderer; default false
[renderers.chunks]
# max_tokens = 512     # default
# overlap_tokens = 64  # default
[renderers.json]
# include_page_bbox = false  # default; opt-in via flag

[routing]
# Reserved for v2. Empty in v1.
```

**Precedence (locked):** CLI flag > TOML file > built-in default. There is no env-var lookup for keys.

**What "opt-in" means literally:** if `[ocr.tesseract]` is absent, the tesseract adapter is not loaded, not even imported, and pages needing OCR get `error="no_ocr_configured"`.

## 7. Renderers (3, locked semantics)

| Name | Output | Splitting | Notes |
|---|---|---|---|
| `md` | `<base>.md` | none | Per-page Markdown concatenated. Optional `<!-- page N -->` markers (default off). |
| `chunks` | `<base>.chunks.jsonl` | by `##`/`###`/paragraph, `max_tokens=512`, `overlap=64` | Each line: `{chunk_id, text, page, section?, src_tool}`. |
| `json` | `<base>.ir.json` | none | Serialized IR (dataclass → dict). Optional `page_bbox`. |

Each renderer is a standalone module with the signature:
```python
def render(ir: IR, *, config: RendererConfig) -> str | bytes
```
No renderer imports another. No renderer imports an adapter. Renderers depend only on `ir.py`.

## 8. CLI (locked)

```
pdf-router [--config PATH] [--out DIR] [--render md,chunks,json] [--info] [--log-level LEVEL] [--version] INPUT.pdf
```

- `INPUT.pdf` — positional, required (also with `--info`).
- `--config PATH` — defaults to `./router.toml` if present, else no config.
- `--out DIR` — defaults to `./out/`.
- `--render ...` — comma-separated subset; default = all three.
- `--info` — print metadata only (calls `classify_pdf`), no render.
- `--log-level` — overrides `[router].log_level`.
- `--version` — package version.

**Exit codes (locked):**
- `0` — full success.
- `1` — usage / IO error (file not found, config invalid, no adapter importable).
- `2` — partial success (at least one page failed but pipeline completed).
- `3` — total failure (e.g. pdf-inspector raised on the document itself).

`pages[].error` carries per-page failure reason in IR; stderr carries the same as WARNING/ERROR log lines.

## 9. Logging (locked)

stdlib `logging`, named logger `pdf_router`. Default level `WARNING`.
Per-page INFO line on success: `page N/M: <src_tool> (<T>ms)`.
Per-page WARNING on OCR fallback / skip: `page N/M: <src_tool_or_skip> — <reason>`.
No progress bar, no structured JSON logs in v1.

## 10. Module layout (locked)

```
pdf-router/
  pyproject.toml
  .gitignore                      # includes config.toml / router.toml
  DESIGN.md                       # this file
  src/pdf_router/
    __init__.py                   # public API exports
    ir.py                         # PageResult, MetadataResult, IR dataclasses
    core.py                       # Router class + process()
    config.py                     # TOML loader, Config dataclass
    cli.py                        # argparse entry point
    adapters/
      __init__.py
      pdf_inspector.py            # process_pdf → IR (always loaded)
      pdf_inspector_ocr.py        # process_pdf_with_ocr → per-page (opt-in)
      tesseract.py                # tesseract CLI subprocess → per-page (opt-in)
      glm_ocr.py                  # HTTP → per-page (opt-in)
      deepseek_ocr.py             # HTTP → per-page (opt-in)
    renderers/
      __init__.py
      md.py
      chunks.py
      json_renderer.py
  tests/
    conftest.py                   # shared fixtures (PDF paths, native-OCR env)
    test_smoke.py                 # import sanity
    test_ir.py                    # IR round-trip
    test_p5.py                    # p5 golden anchors
    test_p27.py                   # text path + real OCR adapters (slow)
    test_p5_e2e.py                # CLI e2e on p5
    test_p27_e2e.py               # CLI e2e on p27 (no-OCR + tesseract)
    test_big_test.py              # CLI e2e on big_test (slow)
    test_cloud_adapters.py        # glm_ocr / deepseek_ocr, mocked HTTP
    test_config.py                # TOML loader
    test_renderers.py             # md / chunks / json
    test_cli.py                   # argparse + exit codes
    test_conventions.py           # module rules, IR_VERSION
```

**Module rule (locked):** only `core.py` imports `adapters/` and `renderers/`. Adapters don't import renderers. Renderers don't import adapters. Everything may import `ir.py`.

## 11. Tests (locked)

Property-based + golden-anchor per test PDF. No snapshot of full Markdown (drift-prone).

| PDF | Property | Golden anchor |
|---|---|---|
| `p5.pdf` (172K, text) | `pdf_type == "text_based"`, `is_complex_layout == True`, `page_count == 1`, markdown non-empty | contains `Обозначение`, contains `ИЛО9.1` |
| `p27.pdf` (192K, image) | `pdf_type == "image_based"`, OCR pages = all, every page has `src_tool` set | without OCR config: every page `error="no_ocr_configured"` |
| `big_test.pdf` (21M, 44p) | `pdf_type == "mixed"`, `len(pages_needing_ocr) == 10` (pages 27, 36–44 1-indexed) | pages 1–25 carry `src_tool="pdf_inspector"` |

## 12. Out of scope for v1 (explicitly)

- Caching (any layer)
- Region-routing (bbox + per-region OCR)
- A/B mode, circuit breaker
- Multi-PDF batch
- Password-protected PDFs
- Streaming output
- LLM-driven summary renderer
- Async/concurrent page processing

## 13. Why these decisions (one-line per)

- Python-only: pdf-inspector has a first-party Python binding, same Rust core.
- pdf-inspector over PyMuPDF: 0.875 vs 0.735 on opendataloader-bench, native Markdown.
- IR (A+): preserves all pdf-inspector fields, adds per-page, no double-pass needed.
- `process_pdf` once + fan-out: one document load, no redundant classification call.
- All OCR engines opt-in: enforces determinism; no silent fallback surprise.
- API keys in TOML under gitignore: one source of truth, no env-var scatter.
- TOML: stdlib (Python 3.11+), no YAML type magic, idiomatic for Python.
- Flat config sections: no DAG/circuit-breaker in v1 — those are v2.
- Three renderers: fulfils the three user personas (RAG / document / pipeline).
- Python API primary, CLI thin: minimalism + reusability.
- Tests = property + golden: drift-resistant against pdf-inspector version bumps.
- No cache in v1: keeps determinism obvious; revisit when repeat-runs become a use case.
- stdlib logging WARNING default: quiet by default, no library dependencies.
- 4 exit codes + graceful degradation: pipelines can branch on partial success.

## 14. Source of truth

The 22-question grilling session that produced this spec is the **only** authoritative design record beyond this file. If this file ever conflicts with the implementation, the implementation wins but the file must be updated in the same commit.
