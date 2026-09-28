# ADR-0008: office ingest — native xlsx, office-oxide for .doc, minimal HTML

- **Status:** Accepted
- **Date:** 2026-09-28
- **Supersedes:** the deferred decision in `src/pdf_router/formats.py`
  ("OOXML package that is not a docx (xlsx/pptx?)" and the fall-through
  to "unrecognized file format" for OLE2/HTML inputs)
- **Decided via:** wayfinder map `docs/wayfinder/v0.8-office-ingest.md`
  (tickets TKT-OI-01..05); research findings:
  [doc-tools.md](../wayfinder/research/doc-tools.md),
  [xlsx-corpus.md](../wayfinder/research/xlsx-corpus.md)

## Context

The corpus (`~/kotel-zamechanna`, recursive count 2026-09-28) is
PDF 50 / **xlsx 89** / docx 26 / **.doc ~100** / md 40 / html 7 — and
the router today routes only pdf/docx/djvu. Every xlsx dies at the
gate's OOXML check; every `.doc` at "unrecognized file format". The two
missing format families are not exotic: smeta/estimates (xlsx) and
project texts/appendices (`.doc`), the substance of the local RAG
pipelines this project serves.

Ground truth that shaped the decisions:

- **xlsx**: 89 files / 172 sheets, 0 parse errors; 27,655 merges;
  41,387 content cells (17.9%) behind `hidden="1"` columns, incl. the
  ЛСР "staircase duplicate" (far hidden columns repeat each row's
  visible text 100%); 287 formula cells, all cached; 0 real date
  serials; 0 media/charts/comments; `_xlnm.Print_Titles` proves
  header ≠ row 1 on 104 sheets; `docProps` title 0/89.
- **.doc**: ~90 OLE2 files (32 KB → **276 MB**), 7 HTML documents
  masquerading as `.doc` (federal laws), 2 Word lock files (`~$…`,
  162 B, no OLE2 magic). Measured tool landscape: only
  `office-oxide` passes the decision rule; antiword loses 42.7% of the
  vocabulary at 56 MB; wv/catdoc time out; native parsing is unviable
  (420 s → killed); LibreOffice is 256 MB / 915 MB RSS.
- **OfficeReadCpp** (assessed pre-map): no LICENSE file anywhere
  (`license: null`, `/license` 404) — legally unusable — plus no
  Python bindings and text/markdown-only output (no structured rows,
  no heading levels), which would have forced re-parsing markdown back
  into `TableBlock.rows` (the exact flaw ADR-0003 pinned on markitdown).

## Decision

**Three ingest paths behind the existing `_INSPECTORS` seam, plus a
grown format gate — renderers untouched, IR ships as 0.6.**

1. **xlsx = native stdlib parse** (`zipfile` + `xml.etree.ElementTree`),
   `adapters/xlsx.py`, ADR-0003's doctrine applied to spreadsheets.
   Locked semantics (TKT-OI-03):
   - **sheet = page** (`page_count` = sheet count); each page's
     markdown opens with `## <sheet name>` — the only section signal
     there is (title 0/89; the 15 multi-sheet files get sections here);
   - **hidden columns and hidden rows are cut**; grid width = last
     *visible* column with content (the ЛСР staircase dies with it —
     17.9% dropped content is the honest, documented cost);
   - **merges**: vertical → value repeated down (the docx `vMerge`
     invariant — rows stay self-contained); horizontal → value in the
     first cell only, followers `""`. The divergence from docx
     `gridSpan` expansion is deliberate: chunks' caption-demotion
     needs a single content cell, and a full-width title repeated ×16
     is retrieval poison. The IR grid stays flat (no spans, v0.7
     doctrine);
   - **sheet → blocks = alternating runs**: a row is dense when ≥50%
     of visible columns carry content — dense runs → `TableBlock`,
     sparse runs → prose paragraphs; empty rows don't break a run. One
     rule catches the smeta shape (preamble → header → body) and the
     5 `ПП468` prose→table→prose sheets, and gives the IR the same
     shape docx already has;
   - `header_rows` = the existing IR heuristic (leading rows of short
     text, no digits); `definedNames` unused (2,870/3,282 are
     `#REF!`-broken);
   - values: numeric `<v>` → `float` → `repr` (kills
     `4.8000000000000001E-2`); formulas publish the cached value only;
     no numFmt engine, no date conversion (0 serials in corpus);
     whitespace-only → `""`;
   - metadata: `source_format="xlsx"`, `SrcTool.XLSX_ADAPTER`,
     `pdf_type="text_based"`, `confidence=1.0`, honest
     `title=None`, `pages_with_tables` = sheets that produced tables.
2. **`.doc` (OLE2) = `office-oxide==0.1.12`** as a core dependency
   (abi3 wheel, 1.8 MB, zero runtime deps, MIT OR Apache-2.0, version
   pinned in `uv.lock`), lazy-imported, `adapters/doc.py`.
   Locked bar (TKT-OI-04):
   - full docx-level IR: document = single `PageResult(page=0)`;
     oxide tables → `TableBlock.rows` (row fidelity = LibreOffice
     ground truth, 184 = 184 at 56 MB); headings → levels best-effort
     (the F3 gap, 1 vs 42, recorded under Consequences); images →
     RT-08 sidecar written only when `output_dir` is set (74 images on
     F3); oxide metadata → `Metadata.title`;
   - **no artificial size cap**: oxide parses 56 MB in 4.099 s
     in-process; the 276 MB file is a slow-tier anchor with a ≈60 s
     budget (TKT-OI-07), a cap only if that measurement demands it;
   - typed failures: corrupt OLE2 → `OfficeOxideError` → `ValueError`
     → exit 3; `source_format="doc"`, `SrcTool.DOC_ADAPTER`.
3. **HTML-masquerade = minimal stdlib text ingest**, `adapters/html.py`
   (`HTMLParser`: tags → whitespace, block elements → paragraphs,
   entity unescape) — the 7 law files reach RAG without a dependency.
   `source_format="html"`, `SrcTool.HTML_ADAPTER`; no images, no OCR;
   a full HTML inspector stays out of scope.
4. **Format gate** (TKT-OI-05 predicates, landing with their adapters):
   `Format` += `XLSX`/`DOC`/`HTML`; order = lock-file name check
   (`~$` prefix — the one deliberate name-based check, content
   detection of 162-byte owner files is unreliable) → PDF → DJVU →
   zip (`xl/workbook.xml` → XLSX; `ppt/presentation.xml` → precise
   typed rejection; OOXML message updated) → OLE2 magic → DOC →
   HTML magic (`<!doctype html` / `<html`, BOM/WS-tolerant) →
   unrecognized.
5. **IR 0.5 → 0.6** (precedent: docx → 0.3, djvu → 0.4 — a new
   `source_format` value is a bump). `core.py` grows exactly one
   branch (`source_format == "doc"` image sidecar); the OCR text pass
   keeps gating on `("pdf", "djvu")` — all three new formats are
   born-digital and never OCR'd (the ADR-0003 scanned-docx limitation
   extends to them, accepted for the same reason: scans are not
   expected in these families).

## Alternatives considered

**For xlsx:**

1. **Third-party libraries (openpyxl / pandas / LibreOffice)** — not
   surveyed, deliberately: unlike ADR-0003 (where a docx tool survey
   ran first), the IR-specific rulings above (run model, hidden-column
   policy, merge policy, `repr` values) are custom shaping any library
   would still require, and the stdlib parse meets the measured corpus
   geometry with zero deps. *Revisit trigger:* if the native parse
   hits a wall (numFmt/date demands, style-driven structure), survey
   openpyxl against the ADR-0003 rule before rewriting.
2. **OfficeReadCpp** — unlicensed (see Context); ruled out before any
   measurement.
3. **office-oxide for xlsx too** — oxide parses xlsx, but ADR-0003's
   doctrine stands: structure must come from the source, not a
   third-party flattening, for the format we *can* parse natively.
   Oxide is admitted exactly where native parsing was measured
   unviable (legacy OLE2).

**For `.doc`:** (all with direct runs — [doc-tools.md](../wayfinder/research/doc-tools.md))

4. **Native OLE2/WordDocument parse** — unviable: the reference
   implementations are 37k/40k lines of C; our Python attempt killed
   at 420 s on the 56 MB file.
5. **LibreOffice headless** — the quality oracle (26 tables / 42
   headings, 915 MB RSS, 26 s/file) but fails the dependency leg
   outright; stays a QA benchmark, never a dependency.
6. **antiword / wv / catdoc** — external GPL binaries with measured
   quality holes (57.3% recall, zero headings, hangs/timeouts).
7. **Apache Tika** — ≈276 MB (JRE 203 MB + 76 MB jar), 3 cold-start
   failures, one connection error at 56 MB.
8. **OfficeReadCpp** — unlicensed; no Python bindings; markdown-only
   output.

## Consequences

Positive:

- The two largest unrouted corpus families become first-class: smeta
  tables land in `TableBlock.rows` straight from the source (native
  xlsx), project texts get docx-level structure via oxide.
- Zero-dep doctrine survives where it matters: xlsx and HTML are
  stdlib-only; the single new dependency (`office-oxide`) is a pinned
  abi3 wheel in the same class as `pymupdf`/`onnxruntime` already in
  `pyproject.toml`.
- The Inspector seam proves itself again — three formats, three
  registry entries, nothing downstream rewrites; renderers untouched.

Negative (accepted, honest):

- **17.9% of xlsx content is dropped** with the hidden columns
  (staircase duplicates plus any unique hidden cells) — the ruling the
  user reversed into after seeing the width cost; recorded here so it
  can be revisited if retrieval misses smeta detail.
- **Heading quality on big `.doc`**: oxide finds 1 heading where
  LibreOffice finds 42 (tables unaffected: 184 = 184). Best-effort
  bar, documented.
- **0.1.x dependency, single maintainer** — mitigated by `==0.1.12`
  pin in `uv.lock`; if the wheel vanishes, `.doc` routing fails loudly
  at install time, not silently.
- **No OCR path** for xlsx/doc/html (extends the ADR-0003 limitation);
  scanned `.doc` will yield image-only garbage typed honestly.
- HTML ingest is prose-only (no structure, no links) — enough for the
  7 laws, not an HTML strategy.
- Gate grows one name-based check (`~$`) — a documented exception to
  the magic-bytes doctrine.

## Implementation

- `src/pdf_router/adapters/xlsx.py` — native inspector (TKT-OI-08).
- `src/pdf_router/adapters/doc.py` — oxide wrapper (TKT-OI-09);
  `src/pdf_router/adapters/html.py` — stdlib tag-strip (TKT-OI-09).
- `src/pdf_router/formats.py` — enum + predicates + registry, each
  branch landing with its adapter (TKT-OI-05's landing map).
- `src/pdf_router/ir.py` — `IR_VERSION = "0.6"`,
  `SrcTool.XLSX_ADAPTER`/`DOC_ADAPTER`/`HTML_ADAPTER`.
- `src/pdf_router/core.py` — one new branch (`doc` image sidecar);
  `src/pdf_router/cli.py` — `--info` one-door branches.
- `pyproject.toml`/`uv.lock` — `office-oxide==0.1.12`.
- Tests: gate predicates, synthetic xlsx control, golden anchors
  (corpus skip-if-missing; 276 MB `.doc` in the slow tier) —
  TKT-OI-07's bar, written inside OI-08/09.
- Docs: README format paragraph, `docs/glossary.md`, this ADR.

## References

- Wayfinder map `docs/wayfinder/v0.8-office-ingest.md`; tickets
  TKT-OI-01..09.
- Research: `docs/wayfinder/research/doc-tools.md` (tool survey with
  direct corpus runs, 2026-09-28),
  `docs/wayfinder/research/xlsx-corpus.md` (corpus geometry, same date).
- ADR-0003 (native-parse doctrine, decision rule, docx precedent),
  ADR-0004 (per-format external-tool ruling), ADR-0006 (chunk contract
  the tables feed).
- Pre-map assessment of OfficeReadCpp (no license; markdown-only) —
  recorded in the map's Notes.
