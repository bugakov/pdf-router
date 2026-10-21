# ADR-0009: Docling output format — a renderer, not the IR

- **Status:** Accepted
- **Date:** 2026-10-21
- **Related:** ADR-0002 (DocLang is not IR), ADR-0001 (markdown is
  authoritative, blocks are advisory), ADR-0005 (OCR structure blocks),
  ADR-0006 (the chunks renderer)

## Context

The question asked: *"can we add one more output format — a document in
Docling's format, produced from our internal IR?"*

The short answer is yes, and the layering was already decided: ADR-0002
fixed the rule that **a published format is a renderer, never the IR**.
Docling's `DoclingDocument` is a published interchange container (a typed
tree of items with page provenance) — the closest sibling of our IR, but
a different job: our IR is the in-process contract between Python adapters
and renderers, Docling's document is what other tools consume.

So the work is not a design question ("should Docling be the IR?" — no,
ADR-0002 §Decision) but an engineering one: how do we translate the IR
into a `DoclingDocument` **without lying**?

The constraints that shape the answer:

1. **Our IR is markdown-first.** `PageResult.markdown` is the
   authoritative text (ADR-0001); the structural blocks are *advisory*,
   and on OCR pages the markdown pipe table is the **scrambled twin** of
   the `TableBlock` (ADR-0005 §5) — it is a lossy projection of the real
   grid, not the grid.
2. **Docling is a typed schema with geometry.** Items carry
   `ProvenanceItem`s (page number + bbox), and docling **clamps every
   bbox to the page size**. Our `page_bbox` is opt-in and no adapter
   fills it today, so most pages have an *unknown* size (0×0).
3. **docling-core is not a light dependency.** It pulls pandas, pillow,
   typer and doclang. The core of this project is deliberately
   "ultra-minimal" (see `pyproject.toml`), and ADR-0002 already rejected
   the DocLang toolchain (lxml/typer) for the same reason.
4. **Renderers are pure functions.** A renderer may not read the input
   file (DESIGN §7). Docling's `DocumentOrigin` wants a sha256 of the
   source binary — which only the pipeline can compute.

## Decision

**Add a `docling` renderer that publishes the IR as a `DoclingDocument`
JSON document, behind an optional `docling` extra, opt-in (not part of
the default renderer set).**

Locked sub-decisions:

1. **A renderer, not a second IR.** The IR stays the only in-process
   contract; `pdf_router.render_docling(ir)` is a pure projection of it
   (ADR-0002's rule, applied to Docling).
2. **The markdown is the spine, the blocks enrich it.** The page markdown
   is walked line by line and produces the item order: headings, text
   paragraphs, list runs, pipe tables, image links. A matching
   `HeadingBlock` upgrades the heading's true level; a matching
   `TableBlock` replaces the pipe grid with the block's real grid; a
   `FigureBlock` becomes a picture pointing at its sidecar; a
   `FormulaBlock` becomes a formula item. Blocks with no markdown
   counterpart are appended after the markdown items, in reading order
   (on OCR pages: raster-pixel `(y0, x0)` order, the same rule as the
   chunks renderer, ADR-0006 §4).
3. **OCR pages: the blocks win, the twin is dropped.** A pipe-table
   region on an OCR page that no `TableBlock` covers is the scrambled
   twin of the block — re-parsing it would publish garbage. Such regions
   are dropped and the block's grid is emitted instead. On native pages
   an unmatched pipe region is kept: the pipe grid *is* the truth there.
4. **Provenance only when the geometry is honest.** A `ProvenanceItem`
   is emitted only when the page size is known **and** the block carries
   a bbox. Two reasons: docling clamps bboxes to the page size, so an
   unknown size (0×0) would silently collapse every box to zero; and OCR
   bboxes live in raster pixels, not the page frame (ADR-0005 §5) —
   promoting them would be a lie about the geometry. Native IR bboxes
   are PDF points with y up, so they are converted to docling's
   `BOTTOMLEFT` frame.
5. **No file reads; `origin` stays unset.** The renderer never touches
   the input file, so it cannot compute the sha256 docling's
   `DocumentOrigin` requires. An invented hash is worse than an honest
   "not computed": the document's `name` carries the title (falling back
   to the source file stem) instead.
6. **docling-core is an optional extra; the import is lazy.**
   `pip install 'pdf-router[docling]'` / `uv sync --extra docling`.
   `import pdf_router` must stay cheap (the import-cost guard in
   `tests/test_api.py` pins it), so docling-core is imported inside the
   renderer on first use. A missing extra raises the typed
   `DoclingUnavailableError` with the install line — never an opaque
   `ImportError` from deep inside the renderer, and never a surprise
   *after* the document was processed (the CLI checks the extra up
   front).
7. **Opt-in, not default.** `docling` is **not** in the default
   `renderers.enabled` set (`md`, `chunks`, `json`). A default run must
   not require an optional extra; the renderer is selected explicitly
   (`--render docling`, or `cfg.renderers.enabled` in Python).
8. **Pure function, deterministic.** Same IR in, byte-identical JSON
   out — the same bar as the other renderers.

## Alternatives considered

1. **Make `DoclingDocument` the IR.** Rejected — ADR-0002's decision,
   restated for Docling: it is a published interchange container, not a
   Python in-process contract, and replacing the dataclass IR would cost
   us the provenance fields Docling has no place for.
2. **Write the Docling JSON by hand, without docling-core.** Rejected —
   the schema is versioned and moves (docling-core 2.103.1 ships document
   schema `1.10.0`); a hand-rolled emitter would drift silently. Using
   the library gives us the typed builder *and* a real validation gate
   (`DoclingDocument.model_validate`).
3. **Run Docling's own pipeline over the source PDF.** Rejected — we
   already own the extraction (IR + OCR + structure pass). Parsing the
   PDF twice would double the cost, and the second parse would produce a
   *different* answer than the IR the rest of the pipeline agreed on.
   The whole point of the renderer is to publish *our* result in their
   format.
4. **Make docling-core a required dependency.** Rejected — it would drag
   pandas/pillow/typer/doclang into the "ultra-minimal" core for a
   format only some consumers want. The extra costs one `uv sync
   --extra docling` line.
5. **Also ship a DocLang renderer now.** Deferred — ADR-0002 already
   scoped DocLang as a renderer candidate, but DocLang's toolkit is a
   validator/packer, not a builder API, and there is no consumer yet.
   The Docling renderer is the shape both would take.
6. **Emit `ProvenanceItem`s for every block, guessing the page size
   (e.g. A4).** Rejected — a guessed page size produces bboxes that
   look authoritative and are wrong. The honest degradation is no
   provenance until the page frame is known.
7. **Put the OCR-derived tables into the document twice** (block grid
   *and* the markdown twin) "because the markdown is authoritative".
   Rejected — the markdown is authoritative for *text* (ADR-0001); on
   OCR pages the pipe table is explicitly the lossy twin of the block
   (ADR-0005), and publishing both would hand consumers two versions of
   the same table, one of them scrambled.

## Consequences

Positive:

- The router can now feed the Docling ecosystem (and anything that
  reads `DoclingDocument`) without a second extraction pass.
- The mapping rules are testable without Docling installed: the fast
  tier is synthetic IRs, and the gate is docling's own
  `model_validate` — a document that docling cannot load is a bug.
- The core stays minimal and the import stays cheap; the extra is one
  line of install.

Negative:

- The extra is optional, so a deployment that wants the format must
  install it — a deliberate trade against the "ultra-minimal" core.
- Provenance is sparse: until an adapter fills `page_bbox`, most
  documents carry items without bbox provenance. The format is still
  complete (docling accepts it), just less geometric.
- The mapping is markdown-driven, so a page whose markdown and blocks
  disagree can publish the block's version of a region — deliberate
  (the block is the better truth), but a behaviour to know about.

## Implementation

- `src/pdf_router/renderers/docling.py` — the renderer: the markdown
  walker, the block enrichment, the OCR-page substitution, the
  provenance rules, the lazy docling-core import and
  `DoclingUnavailableError`; the module docstring is the contract
  reference.
- `src/pdf_router/api.py` — `render_docling(ir, config=...)` +
  the `"docling"` entry in `_RENDERERS` (suffix `.docling.json`).
- `src/pdf_router/cli.py` — `--render docling`; a missing extra is a
  usage error (exit 1) with the install line, not a document failure.
  The check runs *before* the pipeline: the extra's absence is a typo-level
  mistake, and the user must not pay for a full OCR run to learn about it.
- `src/pdf_router/__init__.py` — `render_docling` and
  `DoclingUnavailableError` on the public surface.
- `pyproject.toml` — the `docling` extra (`docling-core>=2.87.0`). The
  floor is the oldest version the renderer was verified against: 2.87.0
  is where the module layout the renderer imports appeared, and the
  renderer was exercised on 2.87.0, 2.88.0, 2.89.0, 2.90.0 and 2.103.1
  (same document, same validation gate).
- Tests: `tests/test_docling_renderer.py` (mapping rules, schema gate,
  determinism, missing-extra error, CLI door) — skipped wholesale when
  the extra is absent.
- Docs: README renderers section (install line, usage, the mapping
  rules), glossary entries (Docling document, Renderer, Public API).
- CI: the `docling` extra is installed in a dedicated step and
  `tests/test_docling_renderer.py` runs there — without it the whole
  module would skip and the schema gate would never execute.

## References

- ADR-0002 (DocLang is not IR — the layering rule this renderer
  follows), ADR-0001 (markdown authoritative, blocks advisory),
  ADR-0005 (OCR structure blocks, raster-pixel bboxes), ADR-0006 (the
  chunks renderer — the same "renderer as published format" shape).
- docling-core 2.103.1 (`DoclingDocument`, document schema 1.10.0) —
  `docling_core.types.doc.*`; the typed builder and the validation gate.
  The renderer was verified against 2.87.0 ... 2.103.1.
- Docling project (LF AI & Data) — the consumer of the published format.
