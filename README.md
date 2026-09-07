# pdf-router

Ultra-minimal, deterministic PDF → LLM-ready Markdown router.

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
native wheel (≥ 1.17).

## Quick start

```sh
# Process a PDF with all three renderers
pdf-router path/to/file.pdf

# Only the markdown output
pdf-router --render md path/to/file.pdf

# Page markers in markdown
pdf-router --render md --render-opts '{"page_markers": true}' path/to/file.pdf

# Print metadata only (no rendering)
pdf-router --info path/to/file.pdf
```

## Configuration

Copy `router.toml.example` to `router.toml` and fill in the OCR engines you
want. Anything you leave commented out is **not loaded** (not even
imported). Precedence: CLI flag > `router.toml` > built-in default.

```toml
# router.toml (gitignored)
[router]
log_level = "info"

[ocr.tesseract]
languages = ["rus", "eng"]
```

## Architecture

```
┌────────────────┐    ┌──────┐    ┌──────────┐    ┌─────────────┐
│ pdf_inspector  │ →  │ IR   │ →  │ md       │ →  │ file.md     │
│ (Rust napi)    │    │(data │ →  │ chunks   │ →  │ .chunks.jsonl│
│                │    │ class│ →  │ json     │ →  │ .ir.json    │
└────────────────┘    └──────┘    └──────────┘    └─────────────┘
                       │
                       ↓
                ┌────────────┐
                │ OCR engine │ (opt-in, per page)
                └────────────┘
```

- **One** `process_pdf` call (always-loaded adapter).
- **Zero or more** OCR calls per page, in `[ocr.*]` order (fallback chain).
- **One** set of renderers, each reading the same IR.

See `docs/glossary.md` for terms and `docs/adr/` for design decisions.

## IR contract (0.2)

```python
{
  "metadata": {
    "pdf_type": "text_based" | "scanned" | "image_based" | "mixed",
    "page_count": int,
    "pages_with_tables": list[int],   # 1-indexed
    "pages_needing_ocr": list[int],   # 1-indexed
    ...
  },
  "pages": [
    {
      "page": int,                    # 0-indexed
      "markdown": str,                # authoritative for md/chunks renderers
      "src_tool": "pdf_inspector" | ...,
      "blocks": [                     # IR 0.2 — advisory
        {"kind": "HeadingBlock", "level": int, "text": str, "bbox": {...} | null, "source": "structure_tree"|"font_size_fallback"},
        {"kind": "TableBlock", "markdown": str, "rows": list[list[str]], "header_rows": int, "bbox": ...},
        ...
      ]
    }
  ]
}
```

`PageResult.markdown` is preserved verbatim from pdf-inspector;
`blocks` is a structural decomposition populated when the adapter has
upstream data for it. See
[ADR-0001](docs/adr/0001-ir-structural-blocks.md).

## Indexing

- **1-indexed** (matches pdf-inspector): `metadata.pages_with_*`,
  `metadata.pages_needing_ocr`, structure-tree `StructureElement.page`,
  `TextItem.page`, `OcrReasonEntry.page`.
- **0-indexed** (matches Python iteration): `PageResult.page`.

## Exit codes

- `0` — full success.
- `1` — usage / IO error (file not found, invalid config).
- `2` — partial success (at least one page failed but pipeline completed).
- `3` — total failure (pdf-inspector raised on the document itself).

## Project layout

```
src/pdf_router/
  ir.py                 # IR dataclasses (the single shared contract)
  core.py               # Router class + orchestration
  config.py             # TOML loader
  cli.py                # argparse entry point
  adapters/
    pdf_inspector.py    # always-loaded (text path + structural blocks)
    pdf_inspector_ocr.py, tesseract.py, glm_ocr.py, deepseek_ocr.py
  renderers/
    md.py, chunks.py, json_renderer.py
tests/
  test_ir.py                    # IR round-trip
  test_ir_structural_blocks.py  # IR 0.2 block anchors (p5 + ТКР1)
  test_p5.py, test_p27.py, test_big_test.py   # golden anchors
  test_renderers.py, test_conventions.py, ...
docs/
  glossary.md           # terms
  adr/0001-ir-structural-blocks.md
```

## License

TBD.
