# TKT-ST-02 — Anchor survey: structure targets in downloads corpus

## Question

Ground-truth pass over the named anchors before any tool decision, so later
tickets argue from measured pages, not impressions:

1. Verify disk names/pages of the user-named anchors in
   `~/vbrdb/orchestrator/downloads/`:
   - `Генетические_аспекты_разведения_сиговых_ры_11188.pdf` — tables on
     pp. 15, 34 (raster pdf);
   - Soviet book scan (Рыбы…Дона и Кубани) — table on p. 14;
   - pick 2–3 native-pdf journals in the same dir as native-vs-OCR
     comparison pairs (they give free ground truth: native extraction of the
     same table the scan garbles).
2. For each anchor table page: page size, approximate table geometry
   (bordered / borderless / merged cells?), scan DPI, skew.
3. For djvu: do any of the 33 vbrdb djvu files contain tables worth an
   anchor (spot-check via page rasters), or do djvu anchors stay
   project-doc/book scans?
4. Classify each candidate: keep / reject (too big to commit? too messy?) —
   slow-tier tests may point at corpus paths instead of fixtures (v0.5
   precedent).

Output: evidence note `docs/wayfinder/research/structure-anchors.md` with the
final anchor list (file, page, expected structure) feeding the quality-bar
ticket.

Takeable now (independent of TKT-ST-01).

## Resolution

**Resolved (corpus survey + OCR probe, 2026-09-13).** Evidence:
[research/structure-anchors.md](../research/structure-anchors.md).

1. Both user-named scan anchors verified on disk (`classify_pdf` →
   `scanned`, conf 0.95; 642 pp / 109 pp). Named table pages kept:
   Генетические pp. 15/34, Рыбы p. 14.
2. OCR probe on Рыбы p. 14 quantifies the pain: rapidocr @150 dpi,
   22.9 s, 167 flat lines, column headers interleaved («Бассейн /
   Бассейн / … / Показатель»), zero structure — the canonical gate page.
3. Native-vs-OCR pairs: three verified `text_based` journals under
   English-named files (table pages 3/4/6 and 5/6/8/9; one journal has
   no tables). CAVEAT recorded: most `10.*_*.pdf` files in downloads/
   are HTML dumps — magic-check every anchor (the router's own detector
   already does).
4. djvu: 3/33 spot-checked — ~230-pp chapter splits, tiny text layers
   (~4 KB ≈ page headers only); table spotting needs eyes (HITL) → no
   djvu table anchor kept; djvu coverage rides the shared OCR-scan path.
5. Geometry/DPI/skew: measured to raster-shape grade only (734×1074 px
   @150 dpi — inside wired_table_rec's ≤1500 px sweet spot); skew not
   measured. Consequence, not omission: gates work fuzzy on cell text
   (scan noise proven: «Кубани»→«Кубанн»), never on exact geometry.
6. All anchors stay corpus-path references, no fixture copies of
   155–248 MB files (v0.5 precedent). The three HITL items (exact
   slow-tier set, optional djvu page, commit-size confirmation) carry
   into TKT-ST-05's question unchanged.
