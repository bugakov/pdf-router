# TKT-ST-05 — Quality bar: golden anchors for OCR structure

## Question

Define the test gates for OCR-sourced structure, modeled on v0.5's two-tier
bar (fast tier = committed tiny fixtures + stubbed engines; slow tier = real
corpus):

1. Which anchors from TKT-ST-02 become slow-tier gates: the two scanned
   journal/book table pages, a project-doc scan table, native-vs-OCR
   comparison pairs from the journals dir, one djvu case if TKT-ST-02 kept
   one.
2. Table invariant: what counts as "cells not scrambled" — e.g. L2
   golden-substring assertions per expected cell (row i, col j contains
   "X"), row/col counts, header-row detection?
3. Fast tier: what tiny fixture carries the shape gates (a 1-page scanned
   pdf fixture with a small table — commit-size rules? synthetic raster?),
   what stays stubbed (structure engine deterministic stub, like v0.5's
   stubbed OCR).
4. Determinism policy for any ML structure model: pinned revision, thread
   count, seeds — and what the slow tier tolerates (the v0.5 OCR
   determinism anchor is the precedent).
5. Perf gate: does the slow tier assert a wall-clock ceiling per page for
   the structure stage?

Output: the quality-bar contract feeding implementation; anchor list with
expected structure values per page.

Blocked by: [TKT-ST-02](TKT-ST-02-anchor-survey.md) (anchor list),
[TKT-ST-03](TKT-ST-03-structure-approach.md) (which engine under test),
[TKT-ST-04](TKT-ST-04-ir-contract-ocr-blocks.md) (what the IR assertions
even are).

## Resolution

**Resolved (grilling, 2026-09-14).** Quality bar locked on the v0.5
two-tier pattern, plus one middle gate the user asked for:

1. **Slow tier (real corpus, corpus paths — no fixture copies of the
   155–248 MB files, v0.5 precedent):** Рыбы p. 14, Генетические
   pp. 15 + 34, both native-vs-OCR journal pairs (table pages 3/4/6 and
   5/6/8/9). djvu carries no table anchor (TKT-ST-02 finding) — it is
   covered by the shared scan path, not a dedicated gate. Every anchor
   magic-checked before use (the `10.*` HTML-dump caveat).
2. **Table invariant:** no exact matching. L2 asserts are
   fuzzy-substring on named cells (row i / col j must contain text X,
   normalization + fuzzy threshold), minimum row/col counts derived
   from `logic_points`, header tokens present (Показатель / Бассейн /
   группы…). Journal pairs add golden cell text from native extraction,
   still fuzzy-matched (scan noise is proven: «Кубани»→«Кубанн»,
   «42,326,0» glued sub-columns).
3. **Fast tier: stub + crop (user ruling "стаб + кроп").** Stubbed
   structure engine (v0.5 precedent) gates IR shape, provenance
   sources, heading levels deterministically. PLUS one committed crop
   fixture — table region of Рыбы p. 14 (~100 KB PNG) — running the
   live lineless engine as a middle gate: asserts a sane cell count
   and header tokens without corpus files. The crop itself is produced
   and wired in TKT-ST-07; its spec lives here.
4. **Determinism:** exact wheel versions via `uv.lock`, fixed
   onnxruntime thread count, models via vendor auto-download
   (rapidocr precedent). Slow tier tolerates numeric noise; structural
   drift (cell counts collapsing, headers lost) fails the gate.
5. **Perf gate:** structure stage ≤ 15 s/page wall clock in the slow
   tier (measured 7.3–7.7 s/page for lineless — 2× headroom; OCR's own
   22–24 s stays outside this gate).

All three result tiers are specified; nothing blocks TKT-ST-06
(ADR-0005 write-up) or TKT-ST-07 (implementation).
