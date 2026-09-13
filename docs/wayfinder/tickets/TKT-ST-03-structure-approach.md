# TKT-ST-03 — Approach: how OCR pages acquire structure

## Question

The root decision. Given TKT-ST-01 evidence and TKT-ST-02 anchors, decide
HOW structure is produced for OCRed pdf/djvu pages. Candidate shapes (not
exhaustive; the research may reveal better):

- **A. Post-OCR structure pass (engine-agnostic)** — a stage after
  `ocr_page` that takes the page raster (+ rapidocr line boxes we currently
  discard) and emits blocks; works for every engine, rapidocr stays default.
- **B. Per-engine structure** — extend engines (e.g. glue TableStructureRec
  into rapidocr; consume glm layout_details for the cloud opt-in); engines
  return (markdown, blocks).
- **C. Hybrid** — local structure pass as default; glm layout_details used
  when the engine already returned them (avoid double work); heuristics
  (pipe-table parsing of glm/inspector markdown) as the cheapest layer.

Must also settle, or explicitly defer to named tickets:
- where the stage lives in code (ocr.py / core.py / new module);
- whether the OCR raster is reused (no second rasterize) — perf budget from
  the map's fog;
- cloud opt-in semantics (never default; deterministic-repro caveats);
- headings: attempt locally vs headings-only-from-cloud vs skip-in-v1.

Output: the ruling, recorded in this ticket; it pre-shapes TKT-ST-04 (IR
contract) and TKT-ST-06 (ADR-0005).

Blocked by: [TKT-ST-01](TKT-ST-01-ocr-structure-tooling-research.md),
[TKT-ST-02](TKT-ST-02-anchor-survey.md).

## Resolution

**Resolved (grilling + live probe, 2026-09-13).** Approach **C (hybrid)**,
all sub-decisions locked:

1. **Shape.** One engine-agnostic structure pass after OCR is the
   default path. Short-circuits where the engine already carries
   structure: glm `layout_details` consumed directly (no local model on
   top of it); boxless engines (tesseract / deepseek /
   pdf_inspector_ocr markdown) degrade to pipe-table parsing of their
   markdown (parser reused from the native path). rapidocr (default)
   feeds the pass the boxes+txts+scores that v1 discards.
2. **Table engine: default `lineless`** (lineless_table_rec, lore
   detect+process, 88.5 MB onnx total) — ruled on the live probe: on
   Рыбы p. 14 (ruled header, unruled body) wired unet recovered only
   the 8 header cells while lineless recovered 144 incl. body rows; on
   Генетические p. 34 both worked (94 vs 149 cells), lineless never
   worse. `wired` stays a TOML value for fully ruled tables; `auto`
   (table_cls) deferred until a corpus justifies the classifier.
   TOML: `structure.table_engine = "lineless" | "wired" | "auto"`.
3. **Code home: new `src/pdf_router/structure.py`** — flat
   `structure_page(raster, ocr_result) -> blocks` +
   `blocks_from_glm(layout_details)` + lazy engine singleton (pattern:
   `_rapidocr_get_engine`). `ocr_page`'s return grows to carry blocks;
   only `ocr.py` + `core.py` touched. Precedent: the `rasterize.py`
   seam (TKT-DJV-11).
4. **Headings: local box-height heuristic in v1** from rapidocr boxes
   (height vs page median + short-line / no-terminator context signals;
   table zones masked so cells don't pose as headings; thresholds in
   TOML). Level semantics for scans → TKT-ST-04. glm `vision_title`
   remains the opt-in escalation.
5. **Raster reuse is mandatory** — the pass consumes the OCR raster, no
   second rasterize (TKT-RT-10). Probe confirmed both engines accept
   our 150-dpi rasters as-is.
6. **Cloud opt-in semantics**: never the default (carried constraint);
   glm non-determinism feeds TKT-ST-05's determinism policy and
   ADR-0005.
7. **Measured costs (CPU, this env, 2026-09-13)**: OCR 21.7–23.7
   s/page; structure adds lineless +7.3–7.7 s / wired +3.8–3.9 s.
   Evidence: [research/ocr-structure-tools.md](../research/ocr-structure-tools.md)
   (§ Live probe).

Unblocks TKT-ST-04 (now the frontier); TKT-ST-05/06 lose one blocker.