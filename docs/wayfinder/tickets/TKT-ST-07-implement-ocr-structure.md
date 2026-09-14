# TKT-ST-07 — Implement OCR structure end-to-end + docs

## Question

Implement the decided approach behind ADR-0005:

- structure stage wired per the approach ruling (post-OCR pass and/or engine
  glue), consuming the locked raster seam (no second rasterize if the ruling
  says so);
- IR 0.5 per TKT-ST-04: OCR-sourced blocks flow into `PageResult.blocks`,
  renderers untouched (markdown stays authoritative);
- cloud opt-in semantics honored (default path fully local);
- quality gates per TKT-ST-05 (fast tier green + slow tier on real anchors);
- README (engines + structure section), glossary terms that graduate
  (domain-modeling), map ticket updates;
- e2e smoke: one scanned journal page and one djvu page through
  `convert()`/CLI with blocks in the JSON output.

Implementation detail tickets graduate from TKT-ST-03's ruling if the shape
splits work further (do not pre-slice now).

Blocked by: [TKT-ST-05](TKT-ST-05-quality-bar.md),
[TKT-ST-06](TKT-ST-06-adr-0005-ocr-structure.md).

## Resolution

Done 2026-09-14. All quality gates green: fast suite 198 passed
(`-m "not slow"`), slow tier 5 passed (`tests/test_structure.py -m slow`).

- **Seam**: `src/pdf_router/structure.py` — `structure_page(raster,
  ocr_triples, config)` (lineless/wired table engine + box-height heading
  heuristic), `blocks_from_glm(layout_details)`,
  `blocks_from_pipe_markdown(md)` (retagged native parser), lazy engine
  singleton, `StructureError` surfaced to `ocr.py`'s degradation path.
- **Key implementation correction vs the bench**: cell grid is rebuilt
  span-aware from the engine's `pred_html` (rowspan/colspan is the
  authoritative grid). The bench-era `zip(cells, logic_points)` approach
  was conceptually wrong — live engines emit 124 `<td>` vs 53 anchor-only
  `logic_points` in arbitrary order. This is what fixed the header/body
  placement on both anchors.
- **Wiring**: `ocr_page` now returns `(markdown, engine, blocks)`;
  boxless engines degrade to pipe fallback; `core.py` stores blocks on
  `page.blocks`; IR bumped 0.4 → 0.5 (`*.source` provenance);
  `[structure]` TOML section (`table_engine`, heading ratios);
  wheels pinned in `pyproject.toml` (core deps, Apache-2.0).
- **Quality bar**: three tiers in `tests/test_structure.py` (13 stub /
  1 crop-middle / 5 slow, incl. e2e smoke) + contract pins updated in
  five existing test files. Crop fixture
  `tests/fixtures/structure/ryby_p14_table.png` (540x734, header inside,
  139 KB) — the first cut missed the header band (y 80..620 fixed).
- **Live-measured anchors** (asserts match reality, not the bench):
  Ryby p.14 grid 42x6, caption occupies grid row 0, header (Показатель)
  lands in rows[1]; Genetika p.34 grid 41x7, header (Признаки) in top
  rows, body words (молоди) survive; p.14 is a full-page table page —
  zero headings is the asserted no-false-positives behavior (positive
  heading cases live in the stub tier). Perf: structure-only 7.4 s/page
  (gate 15 s, OCR's 22–24 s outside the gate per TKT-ST-05).
- **Docs**: README (structure section, blocks schema, ADR links),
  `router.toml.example` (`[structure]`), glossary (djvu entry no longer
  claims `blocks=[]`; new "Structure pass" entry; IR 0.5 bump recorded).
