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
