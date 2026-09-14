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
uv sync --extra dev           # pytest + ruff + mypy (lint/types/CI gate)
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

## Privacy: cloud OCR engines

The two opt-in cloud engines differ in where page images travel:

- **`glm_ocr` (Z.ai)** — the page leaves as a base64 data-URL **directly to
  the configured API endpoint**. No third-party file host is involved.
- **`deepseek_ocr` (Novita AI)** — the page PNG is posted **directly inside
  the chat-completions request** as a base64 data-URL (same pattern as
  glm_ocr). No public file host is involved.

Neither engine uploads page images to any public file-hosting service; the
router's own history of routing `deepseek_ocr` through uguu.se is retired.
Still, using either cloud engine means the page content leaves your
machine for the engine's operator — for sensitive corpora, keep the
default local engines (`rapidocr`, `tesseract`, `pdf_inspector_ocr`).

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
- **Zero or one** structure pass per OCR'd page (`structure.py`):
  rapidocr line geometry + the lineless/wired table engine →
  `TableBlock`s; box-height heuristic → `HeadingBlock`s (ADR-0005).
- **One** set of renderers, each reading the same IR.

See `docs/glossary.md` for terms and `docs/adr/` for design decisions.

## IR contract (0.5)

```python
{
  "ir_version": "0.5",
  "metadata": {
    "source_format": "pdf" | "docx" | "djvu",   # the container format
    "source_path": str | null,      # the input path (filled by core, 0.5.1)
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
        {"kind": "HeadingBlock", "level": int, "text": str, "bbox": {...} | null, "source": "structure_tree"|"font_size_fallback"|"docx_style"|"ocr_box_height"|"ocr_glm_layout"},
        {"kind": "TableBlock", "markdown": str, "rows": list[list[str]], "header_rows": int, "bbox": ..., "source": "markdown_pipe"|"ocr_table_lineless"|"ocr_table_wired"|"ocr_pipe_fallback"|"ocr_glm_layout"},
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
has upstream data for it — and, since IR 0.5, by the **post-OCR structure
pass**: every OCR'd page (scanned PDF or layerless djvu) grows real
`TableBlock`s (RapidAI TableStructureRec over the rapidocr line geometry)
and `HeadingBlock`s (box-height heuristic, honest levels {1, 2}). OCR
bboxes stay in raster pixels (hints, not PDF points). See
[ADR-0001](docs/adr/0001-ir-structural-blocks.md),
[ADR-0003](docs/adr/0003-docx-native-parse.md),
[ADR-0004](docs/adr/0004-djvu-ingest.md) and
[ADR-0005](docs/adr/0005-ocr-structure.md).

IR 0.4 adds `"djvu"` to `source_format` and two `SrcTool` values
(`djvu_adapter`, `djvu_text_layer`); IR 0.5 adds the post-OCR structure
pass — `TableBlock`/`HeadingBlock` gain a `source` provenance field with
five OCR values, and scanned pages get real blocks
([ADR-0005](docs/adr/0005-ocr-structure.md)). Loading an 0.5 payload with
an older reader raises `ValueError` — that is the version bump doing its
job.

## RAG chunks (envelope v2)

The chunks renderer emits hybrid chunks ([ADR-0006](docs/adr/0006-rag-chunks.md)):
one JSONL schema for all chunks, discriminated by `type`:

```json
{
  "chunk_id": "13-t1", "chunk_version": 2,
  "type": "table | table_part | text",
  "embed": "...",       # the vectorization text -- embed it as-is
  "markdown": "...",    # the generation text (context + pipe grid + summary)
  "metadata": {"doc_id": "...", "doc_title": "...", "source_format": "pdf",
                "page": 13, "section": "...", "src_tool": "rapidocr",
                "error": null,
                "table": {"table_id": "13-t1", "part": 1, "parts": 1,
                           "n_rows": 41, "n_cols": 6, "header": [...],
                           "source": "ocr_table_lineless"}},
  "images": []
}
```

- **Tables** (from `TableBlock`): key-value linear embed text
  (`"Ключ: значение, ..."` per row) + context (doc title, walk-back
  heading, caption) + a deterministic facts summary (min/max/sum per
  numeric column). The pipe grid rides in `markdown`, never in `embed`.
  Whole table = one chunk while it fits `max_tokens`; over budget it
  splits by rows with the header band duplicated in every part
  (`part`/`parts` in metadata).
- **Text** (from `page.markdown`): pipe-table regions covered by a
  `TableBlock` are subtracted (exact match on native pages, any pipe
  region on OCR pages); uncovered tables stay in the text — honest
  degradation. Optional `embed_context` preamble (`doc title › heading`)
  counts against the token budget.
- **Identity**: `doc_id` = 12-hex SHA-256 of `Metadata.source_path` —
  the same id as the sidecar images folder.

TOML: `[renderers.chunks] max_tokens, overlap_tokens, summary =
"facts"|"none", embed_context` (see `router.toml.example`). Same IR in,
byte-identical JSONL out.

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
  structure.py          # post-OCR structure pass: tables (lineless/wired) + headings (ADR-0005)
  renderers/
    md.py, chunks.py (RAG envelope v2, ADR-0006), json_renderer.py
tests/
  test_ir.py                    # IR round-trip (incl. source_path)
  test_ir_structural_blocks.py  # IR 0.2 block anchors (p5 + ТКР1)
  test_docx.py                  # docx: synthetic control + golden volumes
  test_djvu.py                  # djvu: fixtures, seam, layer policy, corpus slow tier
  test_p5.py, test_p27.py, test_big_test.py   # golden anchors
  test_api.py                   # public-surface contract + import-cost guard
  test_renderers.py, test_conventions.py, ...
  test_chunks_rag.py            # chunks quality bar: fast/middle/slow tiers (ADR-0006)
docs/
  glossary.md           # terms
  adr/0001-ir-structural-blocks.md
  adr/0003-docx-native-parse.md
  adr/0004-djvu-ingest.md
```

## License

[AGPL-3.0-only](LICENSE) — same as the OCR stack this router builds on
(lineless-table-rec / wired-table-rec are DocLayout-YOLO-family AGPL
projects). Embedding pdf-router into a network service triggers AGPL §13:
your users are entitled to the Corresponding Source of the combined
service. For internal, non-distributed deployments this changes nothing.
