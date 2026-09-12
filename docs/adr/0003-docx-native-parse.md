# ADR-0003: docx — native stdlib parse, no external conversion

- **Status:** Accepted
- **Date:** 2026-09-12
- **Supersedes:** the deferred decision in `src/pdf_router/formats.py`
  ("A non-PDF format either converts to PDF inside its own inspector, or
  those passes grow format awareness — an ADR decision, deliberately not
  made here")
- **Decided via:** wayfinder map `docs/wayfinder/v0.4-docx-ingest.md`
  (tickets TKT-DX-01..06); research findings:
  [docx-tools.md](../wayfinder/research/docx-tools.md)

## Context

The format dispatch seam (commit `57cfa1f`) recognizes DOCX by magic bytes
and routes through the `Inspector` registry, but no docx inspector exists —
`get_inspector(Format.DOCX)` raises `UnsupportedFormatError` with a
forward-looking message. The open question: how does a docx become IR?

Two strategies were on the table:

- **A. Convert to PDF upstream** (LibreOffice headless → existing pipeline,
  incl. OCR of scanned docx). LibreOffice *is* installed on the dev machine,
  but: an external binary in the dependency path (~seconds per document,
  hundreds of MB, "deploy-anywhere" broken), and a **double quality loss** —
  the conversion flattens docx's native structure (styles, real tables) into
  PDF glyphs, which pdf-inspector then re-derives with heuristics
  (font-size fallback with known false positives, ADR-0001; tables via
  re-parse of markdown pipes).
- **B. Native docx parse** (read the OOXML zip directly). No external
  dependency; structure comes from the source, not heuristics.

The user's direction (2026-09-12): the goal for docx is the same as for PDF —
**quality material for LLM/RAG with minimal resources**. The LibreOffice path
was rejected as "оскорбительно нерационально, абсурдно и расточительно";
scanned docx are not expected in the corpus.

A tool survey (TKT-DX-01) tested ready-made libraries against the decision
rule "quality ≥ native parse AND dependencies ≤ native parse" on real corpus
files: **no candidate qualified** (mammoth's markdown writer destroys tables;
markitdown[docx] pulls `magika` → numpy + onnxruntime and base64-inlines
images; python-docx/docx2python require lxml; docx2txt flattens structure and
is stale; pandoc is an external binary). Corpus ground truth: the corpus is
**table-rich and heading-light** — tables are the critical quality asset, and
only a native parse shapes them directly into IR.

## Decision

**Strategy B: native parse on the Python standard library** (`zipfile` +
`xml.etree.ElementTree`), implemented as one inspector function
(`adapters/docx.py`) registered in `_INSPECTORS[Format.DOCX]`. Zero new
dependencies, no external binaries, no C extensions.

Locked sub-decisions (details in the wayfinder tickets):

1. **Page semantics (TKT-DX-02):** whole document = single `PageResult(page=0)`.
   docx is pageless; "page = extraction unit" (pdf → physical page, docx →
   whole document). The `chunks` renderer splits markdown by headings, not
   pages, so it is unaffected.
2. **IR bump (TKT-DX-03):** `IR_VERSION` 0.2 → 0.3; new
   `Metadata.source_format` field (`"pdf"|"docx"`); `SrcTool.DOCX_ADAPTER`.
   PDF-flavored fields (`pdf_type`, `is_complex_layout`, `has_encoding_issues`,
   bbox frame) stay honestly PDF-specific. docx fills `pdf_type="text_based"`,
   `confidence=1.0`. Renderers untouched — `markdown` stays authoritative.
3. **Images (TKT-DX-04):** `word/media/*` extracted to the RT-08 sidecar
   schema (`images/page0000_image_{idx:02d}.{ext}`, kind=`image`), referenced
   from markdown as `![](images/...)`; `FigureBlock` carries honest `None`s
   (no coordinates exist in docx); media referenced from **body and header
   rels** are both extracted. The IR contract serves the renderers (the RAG
   targets), not PDF mimicry — docx fields are never faked into PDF shapes.
4. **Tables:** `w:tbl` → exact 2D `TableBlock.rows`; **`vMerge` continuation
   cells repeat the merged value** (every row self-contained for retrieval);
   pipe-markdown for `TableBlock.markdown`/md renderer generated from the
   same rows (~10 lines).
5. **Headings:** `w:pStyle` → `word/styles.xml` (`w:name`) → builtIn heading
   map. Raw styleIds are never matched (the corpus obfuscates them: `a3`,
   `ab`, `120`...). Unknown/custom styles degrade to plain paragraphs.
6. **CLI (TKT-DX-06):** `--info` routes through the front door (pdf →
   classify, docx → adapter metadata), killing the API-bypass path.
7. **Quality bar (TKT-DX-05):** golden anchors on project-documentation
   volumes (`00845.2024.07 - ИГИ.docx`: 25 tables/112 rows/9 media;
   `ПР ИГМИ Урицкого.docx`: 8 media/12.2 MB), vMerge-repeat invariant,
   all docx tests fast.

## Alternatives considered

1. **LibreOffice headless → PDF → existing pipeline.** Rejected — external
   binary dependency, double quality loss (structure flattened then
   re-derived by heuristics), seconds per document. See Context.
2. **mammoth** (pure-Python, 1 dep). Rejected — its markdown writer
   destroys tables on 3/3 corpus files + a synthetic control (writer
   limitation); its HTML writer is exact but then HTML→markdown conversion
   is still needed (that is markitdown's dependency-laden path).
3. **markitdown[docx]** (Microsoft). Rejected — quality adequate, but the
   dependency chain (~10 packages, `magika` → numpy + onnxruntime, 2 C
   extensions) violates the minimal-resources constraint; base64-inlines
   images against the sidecar convention; output must be parsed back to
   fill `TableBlock.rows`.
4. **python-docx / docx2python.** Rejected — both require `lxml` (C
   extension), which is not a project dependency today; python-docx is also
   blind to anchored (non-inline) images.
5. **docx2txt.** Rejected — destroys all structure (cell boundaries lost on
   3/3 files); stale upstream.
6. **pandoc.** Benchmark only (external binary, locked out of the dependency
   path). Noted: best row fidelity on complex tables (colspan/rowspan), a
   reference point for future quality-bar comparisons.
7. **docling / unstructured** (AI layout parsers). Rejected — violate the
   minimal-resources constraint by orders of magnitude.

## Consequences

Positive:

- Structure comes from the source, not heuristics: heading levels exact where
  styles exist; table rows exact (2D list, vMerge unwrapped); media complete
  (incl. header/footer references).
- Zero new dependencies; deploy-anywhere preserved; parse cost is
  milliseconds (stdlib zip+XML), no subprocess, no temp files.
- The `Inspector` seam proves itself: one registry entry, nothing downstream
  changes; renderers consume docx output unchanged.

Negative:

- **Scanned docx get no OCR** — accepted: user states they are not expected;
  such documents are typed honestly in metadata. Revisit only if the corpus
  changes (the LibreOffice path remains the fallback *idea*, documented here,
  not implemented).
- **No physical pagination** — page-keyed machinery sees one page; per-page
  granularity (e.g. `page.error` isolation) is coarser than PDF's. Accepted:
  docx is pageless and the renderers do not depend on pages.
- The native parser owns OOXML details (obfuscated styleIds, vMerge/gridSpan,
  rels walking) that a library would hide — mitigated by the golden-anchor
  tests on real project-documentation volumes.
- `IR_VERSION` 0.2 → 0.3 is a breaking change for consumers of `to_dict`
  output (no external consumers; recorded for honesty, as in ADR-0001).

## Implementation

- `src/pdf_router/ir.py` — `Metadata.source_format`, `SrcTool.DOCX_ADAPTER`,
  `IR_VERSION = "0.3"`, round-trip for the new key.
- `src/pdf_router/adapters/docx.py` — the inspector (stdlib only; lazy
  import per the heavy-deps rule is trivially satisfied).
- `src/pdf_router/formats.py` — `_INSPECTORS[Format.DOCX] = _docx_inspector`.
- `src/pdf_router/cli.py` — `--info` through the front door.
- Tests: golden anchors (skip-if-missing corpus fixtures) + synthetic
  control; all fast. See TKT-DX-08 for the full checklist.
- Docs: README format paragraph; `docs/glossary.md`.

## References

- Wayfinder map `docs/wayfinder/v0.4-docx-ingest.md`; tickets TKT-DX-01..08.
- Research: `docs/wayfinder/research/docx-tools.md` (tool survey with
  primary-source citations and corpus conversion runs, 2026-09-12).
- ADR-0001 (IR 0.2 structural blocks — the heuristics docx avoids),
  ADR-0002 (DocLang not as IR).
- Architecture review 2026-09-12, candidates 2/3/5
  (`/tmp/architecture-review-20260912-0614.html`, ephemeral).
