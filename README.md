# pdf-router

Ultra-minimal, deterministic PDF/DOCX/DJVU → LLM-ready Markdown router.

Primary inputs: scientific journals, monographs, project documentation.
Output: per-page markdown (with optional page markers), RAG-ready JSONL
chunks, and a serialized IR (dataclass → JSON).

## Install

```sh
uv sync                       # core only (pdf-inspector + pymupdf)
uv sync --extra tesseract     # local tesseract OCR
uv sync --extra cloud         # cloud OCR (glm-ocr, deepseek-ocr)
uv sync --extra dev           # pytest
```

Requires Python ≥ 3.12 and the [pdf-inspector](https://github.com/firecrawl/pdf-inspector)
native wheel (≥ 1.17). For DJVU input, `djvulibre-bin` must be on `PATH`
(`djvused`, `djvudump`, `ddjvu` — ADR-0004); PDF/DOCX need no external
binaries.

## Quick start

```sh
# Process a PDF with all three renderers
pdf-router path/to/file.pdf

# Same for a docx — native stdlib parser, no external binaries (ADR-0003)
pdf-router path/to/том.docx

# And a djvu — djvulibre subprocess adapter (ADR-0004)
pdf-router path/to/книга.djvu

# Only the markdown output
pdf-router --render md path/to/file.pdf

# Page markers in markdown
pdf-router --render md --render-opts '{"page_markers": true}' path/to/file.pdf

# Print metadata only (no rendering): PDFs classify, docx/djvu inspect
pdf-router --info path/to/file.pdf
```

## Python API (embedding)

`import pdf_router` is the supported integration surface — no submodule
imports needed. Heavy dependencies (pymupdf, onnxruntime, rapidocr) load
lazily, so importing the package at app startup is cheap (~85 ms).

```python
import pdf_router

# One call, no config, no files written — the pipeline runs in memory.
markdown = pdf_router.convert("scan.pdf")

# Full control: IR + per-page provenance + failure counts. Also pure by
# default; pass output_dir= to extract sidecar page images
# (<out>/<doc-id>/images/) — the only IO switch the pipeline has.
ir, report = pdf_router.process_pdf("scan.pdf", config="router.toml")
ir, report = pdf_router.process_pdf("scan.pdf", output_dir="sidecars/")
for page in ir.pages:
    print(page.page + 1, page.src_tool, page.error or "ok")

# strict=True raises ProcessingError(report) if any page failed.
markdown = pdf_router.convert("scan.pdf", strict=True)

# Render an IR you already hold (all three formats, files written).
paths = pdf_router.write_outputs(ir, "out/", "doc")
```

Config is optional everywhere: `None` (built-in defaults), a TOML path, or a
`RouterConfig` you tweaked in Python (`pdf_router.load_config()` returns the
defaults for inspection/override). The value is coerced once, up front, in
every entry point — a missing explicit config path raises `ConfigError`
before the first page is processed, so typos never silently change routing
or burn a pipeline run.

Input format is detected from **magic bytes, not the file extension**.
Today PDF, DOCX and DJVU are routable. A docx renamed to `.pdf` converts
as docx; a PDF renamed to `.djvu` converts as PDF. Adding a format means
one registry entry in `src/pdf_router/formats.py`; nothing downstream
changes.

DOCX is parsed **natively** (stdlib `zipfile` + ElementTree, ADR-0003):
`w:pStyle` resolves through `word/styles.xml` to exact heading levels,
`w:tbl` yields exact 2D rows (gridSpan expanded, vMerge continuations
repeat the restart value), `word/media/*` becomes sidecar images. The
whole document is one pageless IR page (`source_format="docx"`,
`pdf_type="text_based"`, confidence 1.0 — no OCR pass exists or is
needed). No external binaries, milliseconds per document; scanned docx
is out of scope (not present in the corpus).

DJVU is ingested **natively** (djvulibre subprocess adapter, ADR-0004) —
no intermediate PDF is ever written: pages, hidden text layer and stored
DPI are read with `djvused`/`djvudump`; page rasters for OCR are rendered
on demand by `ddjvu` (PPM to stdout, in-memory). A page whose hidden text
layer yields real text answers without OCR (`src_tool="djvu_text_layer"`);
layerless pages are OCR'd over the rendered raster. `pdf_type` is always
`"scanned"` (djvu is a raster-scan format by nature). No sidecar images:
a djvu page *is* a page-background scan, and those are never exported
(same rule as scanned PDFs). `pdf_inspector_ocr` stays PDF-only (its
Rust core cannot open djvu) — route djvu through any other engine.

Output policy belongs to the call, not the config: `convert()` and
`process_pdf()` write nothing unless you pass `output_dir=`. The
`[renderers].output_dir` TOML key is the CLI's default directory
(`--out` > TOML > `./out`); the library core never reads it.

One exception to "no side effects": if you select the `pdf_inspector_ocr`
engine, its Rust core reads two env vars (`PDFIUM_LIB_PATH`,
`ORT_DYLIB_PATH`) and there is no kwarg alternative. The library itself
never writes `os.environ` — call `pdf_router.wire_pdf_inspector_env(config)`
once after loading the config (the CLI does this for you). It bridges the
`[ocr.pdf_inspector_ocr]` TOML section into the env, warns on stale
mismatches, and is a no-op for every other engine.

## Configuration

Copy `router.toml.example` to `router.toml`. `[ocr.<engine>]` sections store
engine params; exactly **one** engine runs, picked by
`[routing].ocr_fallback_engine` (default `rapidocr`, which needs no section).
Sections of unselected engines are validated but otherwise ignored — keep
keys for several engines and switch by changing one line. Precedence:
CLI flag > `router.toml` > built-in default.

```toml
# router.toml (gitignored)
[router]
log_level = "info"

[routing]
ocr_fallback_engine = "tesseract"

[ocr.tesseract]
languages = ["rus", "eng"]
```

Selecting an engine without its `[ocr.*]` section — or an unknown engine
name — is a `ConfigError` at load, before any page is processed.

## Architecture

```
┌────────────────┐    ┌──────┐    ┌──────────┐    ┌─────────────┐
│ pdf_inspector  │ →  │ IR   │ →  │ md       │ →  │ file.md     │
│ (Rust napi)    │    │(data │ →  │ chunks   │ →  │ .chunks.jsonl│
└────────────────┘    │ class│ →  │ json     │ →  │ .ir.json    │
┌────────────────┐    └──────┘    └──────────┘    └─────────────┘
│ docx adapter   │ →     ↑
│ (stdlib only)  │       │
└────────────────┘       ↓
┌────────────────┐
│ djvu adapter   │ →
│ (djvulibre)    │
└────────────────┘
                ┌────────────┐
                │ OCR engine │ (PDF/DJVU pages, opt-in, per page)
                └────────────┘
```

- **One** `process_pdf` call — the format gate (`formats.route()`) picks
  the inspector (PDF → pdf-inspector adapter; DOCX → the native stdlib
  adapter; DJVU → the djvulibre subprocess adapter).
- **Zero or more** OCR calls per PDF/DJVU page, via the single configured
  `[routing].ocr_fallback_engine` (params from its `[ocr.*]` section).
  DOCX is born-digital — no OCR pass exists for it.
- **One** set of renderers, each reading the same IR.

See `docs/glossary.md` for terms and `docs/adr/` for design decisions.

## IR contract (0.4)

```python
{
  "ir_version": "0.4",
  "metadata": {
    "source_format": "pdf" | "docx" | "djvu",   # the container format
    "pdf_type": "text_based" | "scanned" | "image_based" | "mixed",
    "page_count": int,
    "pages_with_tables": list[int],   # 1-indexed
    "pages_needing_ocr": list[int],   # 1-indexed
    ...
  },
  "pages": [
    {
      "page": int,                    # 0-indexed; docx = single page 0
      "markdown": str,                # authoritative for md/chunks renderers
      "src_tool": "pdf_inspector" | "docx_adapter" | "djvu_adapter" | "djvu_text_layer" | ...,
      "blocks": [                     # advisory
        {"kind": "HeadingBlock", "level": int, "text": str, "bbox": {...} | null, "source": "structure_tree"|"font_size_fallback"|"docx_style"},
        {"kind": "TableBlock", "markdown": str, "rows": list[list[str]], "header_rows": int, "bbox": ...},
        {"kind": "FigureBlock", "bbox": null, "caption": str | null, "image_index": int | null},
        ...
      ]
    }
  ]
}
```

`PageResult.markdown` is preserved verbatim from the inspector (PDF:
pdf-inspector; DOCX: the native adapter; DJVU: the hidden text layer or
empty); `blocks` is a structural decomposition populated when the adapter
has upstream data for it (djvu: always empty). See
[ADR-0001](docs/adr/0001-ir-structural-blocks.md),
[ADR-0003](docs/adr/0003-docx-native-parse.md) and
[ADR-0004](docs/adr/0004-djvu-ingest.md).

IR 0.4 adds `"djvu"` to `source_format` and two `SrcTool` values
(`djvu_adapter`, `djvu_text_layer`); loading an 0.4 payload with an older
reader raises `ValueError` — that is the version bump doing its job.

## Indexing

- **1-indexed** (matches pdf-inspector): `metadata.pages_with_*`,
  `metadata.pages_needing_ocr`, structure-tree `StructureElement.page`,
  `TextItem.page`, `OcrReasonEntry.page`.
- **0-indexed** (matches Python iteration): `PageResult.page`.

## Exit codes

- `0` — full success.
- `1` — usage / IO error (file not found, invalid config, unrecognized input format).
- `2` — partial success (at least one page failed but pipeline completed);
  every failed page is listed on stderr, outputs are still written.
- `3` — total failure (an adapter raised on the document itself — broken
  PDF, corrupt djvu, unreadable docx).

## Project layout

```
src/pdf_router/
  __init__.py           # public surface (re-exports api.py)
  api.py                # embedding facade: convert/process_pdf/render_*/write_outputs
  ir.py                 # IR dataclasses (the single shared contract)
  formats.py            # format detection + inspector registry (multi-format seam)
  core.py               # flat pipeline orchestration (process_pdf)
  config.py             # TOML loader
  cli.py                # argparse entry point
  adapters/
    pdf_inspector.py    # PDF: always-loaded (text path + structural blocks)
    docx.py             # DOCX: native stdlib parser (ADR-0003)
    djvu.py             # DJVU: djvulibre subprocess adapter (ADR-0004)
  rasterize.py          # format-aware page raster seam (pymupdf | ddjvu)
  ocr.py                # flat OCR engines: rapidocr/pdf_inspector_ocr/tesseract/glm/deepseek
  renderers/
    md.py, chunks.py, json_renderer.py
tests/
  test_ir.py                    # IR round-trip
  test_ir_structural_blocks.py  # IR 0.2 block anchors (p5 + ТКР1)
  test_docx.py                  # docx: synthetic control + golden volumes
  test_djvu.py                  # djvu: fixtures, seam, layer policy, corpus slow tier
  test_p5.py, test_p27.py, test_big_test.py   # golden anchors
  test_api.py                   # public-surface contract + import-cost guard
  test_renderers.py, test_conventions.py, ...
docs/
  glossary.md           # terms
  adr/0001-ir-structural-blocks.md
  adr/0003-docx-native-parse.md
  adr/0004-djvu-ingest.md
```

## License

TBD.
