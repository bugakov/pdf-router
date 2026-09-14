# ADR-0005: OCR pages grow structural blocks — hybrid structure pass

- **Status:** Accepted
- **Date:** 2026-09-14
- **Supersedes:** the deliberate v0.3–v0.5 limitation "OCR results land in
  `page.markdown` only; `blocks` stays `[]`" (recorded in the map's fact
  base and `src/pdf_router/core.py:236-238`)
- **Decided via:** wayfinder map `docs/wayfinder/v0.6-ocr-structure.md`
  (tickets TKT-ST-01..06); research findings:
  [ocr-structure-tools.md](../wayfinder/research/ocr-structure-tools.md)

## Context

The router's destination contract (v0.6 map, user wording: "collect
everything, lose nothing, spend minimum resources") requires OCRed pages —
scanned pdf and djvu alike — to emit the same structural IR as native
pdf/docx, so RAG renderers can chunk project documentation and scientific
journals without flattened, cell-scrambled tables. ADR-0002 stays binding:
the IR is the Python dataclass; renderers/chunkers for the new structure
are a later effort, not this one.

Ground truth from the map's research and anchors:

- The default OCR engine (rapidocr, `src/pdf_router/config.py:91`) returns
  per-line `boxes + txts + scores`; v1 flattens it to
  `"\n".join(txts)` (`src/pdf_router/ocr.py:211-214`) — the boxes carry
  the geometry a structure stage needs, and we throw them away today.
- glm_ocr already returns structure in `layout_details` (HTML tables,
  `vision_title` labels, bboxes) — but it is cloud, billed, and
  non-deterministic; standing constraint: cloud engines are opt-in only,
  never the default path.
- Boxless engines (tesseract, deepseek_ocr, pdf_inspector_ocr) return
  flat text/markdown — no coordinates, structure cannot be recovered
  from geometry they never emit.
- pdf-inspector's structure machinery (structure tree, font-size
  fallback) is native-path-only; its OCR output is equally flat.
- Anchor pain, quantified (TKT-ST-02): Рыбы p. 14 → rapidocr @150 dpi
  yields 167 flat lines with column headers interleaved («Бассейн /
  Бассейн / … / Показатель»), zero structure. Both scan anchors and the
  journal pairs (native-vs-OCR) live on corpus paths in
  `~/vbrdb/orchestrator/downloads/`.
- Live probe (TKT-ST-03, CPU, 150 dpi, our raster + our rapidocr result
  fed as `ocr_result`): on Рыбы p. 14 — header ruled, body not — the
  wired unet model recovers only the 8 header cells while lineless
  recovers 144 (header + body rows in place); on Генетические p. 34
  lineless is never worse (149 vs 94 cells). Measured cost: lineless
  +7.3–7.7 s/page, wired +3.8–3.9 s/page on top of 21.7–23.7 s OCR.
- The RapidAI TableStructureRec family (wired_table_rec 1.2.0,
  lineless_table_rec 0.1.0, table_cls 1.2.0) is Apache-2.0, pure-python
  wheels, CPU onnxruntime — already a project dependency — and accepts
  an external `ocr_result=[(box, text, score), ...]`, i.e. the exact
  seam: the structure stage reuses the OCR run the router already paid
  for. The wheels declare no rapidocr dependency, so the README's
  `rapidocr <3.0.0` compat window is advisory for us (verified in wheel
  METADATA); models auto-download from ModelScope (unet.onnx 8.3 MB;
  lineless lore detect+process 88.5 MB total).

## Decision

**Hybrid structure pass (approach C from TKT-ST-03): one engine-agnostic
stage after OCR is the default path; engines that already carry structure
short-circuit it; boxless engines degrade to markdown pipe-table parsing;
everything lands in the existing IR block types.** New module
`src/pdf_router/structure.py` owns the seam; only `ocr.py` and `core.py`
change around it.

Locked sub-decisions (details in the wayfinder tickets):

1. **Pass shape (TKT-ST-03).** `structure_page(raster, ocr_result) ->
   blocks` runs after OCR on every scanned page. rapidocr (default)
   feeds it the `boxes+txts+scores` v1 currently discards. When the
   engine is glm and `layout_details` came back, blocks are built
   directly from them (`blocks_from_glm`) and the local table model does
   not run on top. Boxless engines get the cheapest layer only:
   pipe-table parsing of their markdown (parser reused from the native
   path) — honestly degraded, no fake geometry.
2. **Table engine: default `lineless`** (lineless_table_rec "lore",
   88.5 MB onnx) — ruled by the live probe above: real anchor pages are
   mixed (ruled header, unruled body), where wired-only loses the body.
   `wired` (unet 8.3 MB) stays a TOML value for fully ruled tables;
   `auto` (table_cls routing, +70 KB) is deferred until a corpus
   justifies the classifier. TOML: `[structure].table_engine =
   "lineless" | "wired" | "auto"`. Cell row/col semantics come from the
   engines' `logic_points` (per-cell `[row_start, row_end, col_start,
   col_end]`) — directly the `TableBlock.rows` shape.
3. **Headings: local box-height heuristic in v1** (TKT-ST-03/04) from
   the same rapidocr boxes: line height vs page-median height, plus
   cheap context signals (short line, no sentence terminator); table
   zones are masked so cells cannot pose as headings. Scan headings
   restrict `HeadingBlock.level` to {1, 2} (level 1 = height ≥
   `[structure].heading_big_ratio` × median, level 2 = height ≥
   `[structure].heading_small_ratio` below that) — the heuristic cannot
   honestly support 1–6, and chapter/section is the granularity RAG
   renderers consume. glm `vision_title` remains the opt-in escalation.
4. **Raster reuse is mandatory** — the pass consumes the raster the OCR
   stage already rendered (rasterize-once, TKT-RT-10 / ADR-0004 §7).
   Probe confirmed both table engines accept our 150-dpi rasters as-is
   (inside wired/lineless's ≤1500 px sweet spot — no rescaling).
5. **IR contract (TKT-ST-04).** Five deltas, implementation in the map's
   implementation ticket:
   - new `source` provenance values alongside the existing
     `structure_tree` / `font_size_fallback` (`src/pdf_router/ir.py:119`):
     `ocr_table_lineless`, `ocr_table_wired`, `ocr_pipe_fallback`,
     `ocr_glm_layout`, `ocr_box_height`;
   - `bbox` becomes optional-with-semantics: OCR-sourced blocks keep
     boxes in **raster pixels** as-is — no scaling to PDF points (the
     user's standing rule: coordinates only if resource-cheap; scaling
     spends precision and CPU for nothing). Consumers treat bbox as a
     hint, not a contract;
   - `IR_VERSION` 0.4 → 0.5 (new provenance values + pixel-space bbox on
     OCR sources — same visibility argument as the 0.3→0.4 bump in
     ADR-0004 §4);
   - `HeadingBlock.level` ∈ {1, 2} for scan sources (see §3);
   - `TableBlock` does not grow: no per-cell confidence (OCR scores live
     on the OCR result, nothing consumes them in the IR).
6. **Cloud stays opt-in (carried).** glm never the default; its
   non-determinism is why the quality bar's determinism policy applies
   to the local path only.
7. **Dependency ruling.** `wired-table-rec==1.2.0` and
   `lineless-table-rec==0.1.0` (exact pins — the wired default
   `model_type` has churned across releases; `table_cls` is NOT added
   yet) enter the main dependency path: Apache-2.0, `py3-none-any`
   wheels, Python 3.12-compatible; new transitive deps are scipy +
   scikit-image (CPU-only, common); `onnxruntime` is already required
   (`pyproject.toml:8`). Weights auto-download from ModelScope on first
   use, same pattern as rapidocr's bundled models; disk cost 88.5 MB
   default (8.3 MB if the operator pins `wired`). The
   wired/lineless ↔ rapidocr version window is advisory (no rapidocr
   dependency in their wheels) — pinned anyway, and the fast+crops test
   tier smoke-tests the pair.
8. **Quality bar (TKT-ST-05).** Three result tiers: fast = committed
   stub engine + deterministic IR/provenance/level gates; middle = one
   committed ~100 KB crop of the Рыбы p. 14 table region running the
   live lineless engine (sane cell count + header tokens, no corpus
   files); slow (`-m slow`) = corpus-path anchors (Рыбы p. 14,
   Генетические pp. 15/34, two native-vs-OCR journal pairs; djvu rides
   the shared scan path with no dedicated table anchor). Invariants are
   fuzzy-substring on named cells + row/col counts from `logic_points`
   + header tokens — never exact match (scan noise is proven:
   «Кубани»→«Кубанн», glued «42,326,0» sub-columns are a known shared
   weakness whose fix path is `logic_points` splitting at
   implementation time). Determinism: exact wheel pins via `uv.lock`,
   fixed onnxruntime thread count, structural drift fails the gate.
   Perf gate: structure stage ≤ 15 s/page (measured 7.3–7.7 s — 2×
   headroom; OCR's own cost stays outside this gate).

## Alternatives considered

1. **Post-OCR pass for every engine (plain A).** Rejected — boxless
   engines (tesseract / deepseek / pdf_inspector_ocr) have no
   coordinates; feeding them to a geometry pass yields nothing without
   reworking the engines. The hybrid gives them the honest cheap layer
   (pipe-parse) instead.
2. **Per-engine structure (B).** Rejected — the same
   `ocr_result → lineless → TableBlock` glue would be duplicated in
   every engine branch; one seam (`structure.py`) is the rasterize.py
   precedent (ADR-0004 §7: a locked seam earns a module).
3. **wired unet only in v1 (8.3 MB vs 88.5 MB).** Rejected on evidence,
   not budget: the probe showed wired recovers only the ruled header on
   the canonical anchor page (8 cells vs 144) — the minimal-resources
   pick fails the actual corpus. wired remains available via TOML for
   fully ruled tables.
4. **A local layout model for headings.** Rejected — no viable
   candidate: RapidLayout ships no pip-installable title model;
   DocLayout-YOLO is AGPL-3.0 + torch; PP-Structure carries the paddle
   tax (ADR references TKT-ST-01 research for the verified numbers).
   The box-height heuristic is free and gated by the anchors.
5. **Heavy AI parsers (docling, unstructured).** Carried out of scope
   from ADR-0003 / the map — not revisited.
6. **Per-cell confidence in `TableBlock`.** Rejected — scores live on
   the OCR result object; no renderer consumes them; IR stays minimal.
7. **Scaling OCR bboxes to PDF points.** Rejected — spends CPU and
   precision to serve no current consumer; raster-pixel hint documented
   per-source instead.

## Consequences

Positive:

- OCRed pdf/djvu pages carry the same block contract as native
  pdf/docx — the map's destination. `blocks` stops being
  always-`[]` on the scan path; `page.markdown` remains the
  authoritative flat text for legacy renderers (ADR-0001's advisory
  rule), so no renderer changes.
- The structure stage reuses the paid-for OCR run — the only new cost
  is ~7.5 s/page (lineless) and 88.5 MB of one-time model download.
- Headings and tables land without any new infrastructure beyond two
  small Apache-2.0 wheels; `table_cls`/`auto` is an additive later
  flip, not a redesign.

Negative:

- Two new pinned packages (+ scipy, scikit-image transitively) enter
  the main dependency path; models auto-download at first run —
  first-run needs network (same as rapidocr today).
- Glued twin sub-columns («V,% / h²» → «42,326,0») are a known
  lineless weakness on the anchors; `logic_points` carry the split
  path, but until implemented, some dense journal tables will keep
  merged cells. Fuzzy gates, not exact, hold the bar meanwhile.
- Heading levels from a global height ratio can jitter between scans
  of different provenance; thresholds are TOML-tunable and the golden
  anchors are the arbiter of where {1,2} breaks.
- `IR_VERSION` 0.4 → 0.5 is a breaking change for consumers of
  `to_dict` output on OCR pages (no external consumers; recorded for
  honesty, as in ADR-0001/0003/0004).
- glm-sourced blocks (opt-in) stay non-deterministic run-to-run —
  documented engine behavior, excluded from the determinism gates.

## Implementation

- `src/pdf_router/structure.py` (new) — `structure_page(raster,
  ocr_result, config) -> blocks`; `blocks_from_glm(layout_details)`;
  pipe-table fallback parser (reused from the native path); lazy
  engine singleton per `[structure].table_engine` (pattern:
  `_rapidocr_get_engine`).
- `src/pdf_router/ocr.py` — rapidocr branch stops discarding
  `boxes`/`scores` (today: `ocr.py:211-214`); `ocr_page`'s return grows
  to carry blocks (or the structure pass is called from `core.py` —
  decided at implementation, both touch only these two files).
- `src/pdf_router/core.py` — OCR branch wires produced blocks into
  `page.blocks` (today: `core.py:236-238`).
- `src/pdf_router/ir.py` — `IR_VERSION = "0.5"`; module docstring
  documents the new provenance sources and the raster-pixel bbox
  semantics for OCR sources.
- `src/pdf_router/config.py` — `[structure]` section (new top-level key
  in `_KNOWN_TOP_LEVEL`): `table_engine`, `heading_big_ratio`,
  `heading_small_ratio`; defaults per §2/§3.
- `pyproject.toml` — `wired-table-rec==1.2.0`, `lineless-table-rec==0.1.0`.
- Tests per TKT-ST-05: fast stub tier; the committed crop fixture
  (produced and wired here) as the middle gate; `-m slow` corpus
  anchors with fuzzy invariants + perf gate.
- Docs: README structure section (engines table + `[structure]` TOML);
  glossary terms graduate via domain-modeling; map ticket updates.

## References

- Wayfinder map `docs/wayfinder/v0.6-ocr-structure.md`; tickets
  TKT-ST-01..07.
- Research: `docs/wayfinder/research/ocr-structure-tools.md` (tool
  survey, wheel/METADATA verification, live probe numbers, glm
  `layout_details` contract).
- ADR-0001 (IR structural blocks — blocks stay advisory),
  ADR-0002 (DocLang not as IR — cited, not reopened), ADR-0003 (docx
  native parse), ADR-0004 (djvu ingest — the raster seam and the
  IR-bump precedent this ADR extends).
- Vendor: <https://github.com/RapidAI/TableStructureRec> (Apache-2.0);
  models via ModelScope `RapidAI/RapidTable`.
