# Research — structure anchors survey (TKT-ST-02)

Date: 2026-09-13. Local corpus survey, read-only. Tools used:
`pdf_inspector.classify_pdf` / `extract_pages_markdown`, the router's raster
seam + rapidocr, `djvused`. All numbers measured today in this env.

## Verdict

All anchor classes are confirmed on disk; the two scanned anchors classify
`scanned` conf 0.95 and OCR as expected. Journal "native-vs-OCR pairs"
exist, but NOT among the `10.*` files — **most `10.*_*.pdf` in downloads/
are HTML error pages saved with a .pdf name** (verified: `<!DOCTYPE html>`
magic). Real native journals sit under descriptive-English names; three
verified pairs listed below. djvu corpus: files are per-chapter splits
(~230 pp each, tiny text layers) — table spotting needs eyes (HITL), keep
djvu anchors = project-doc/book scans for now.

## Scanned anchors (user-named, verified)

| file | type | conf | pages | note |
|---|---|---|---|---|
| `Генетические_аспекты_разведения_сиговых_ры_11188.pdf` | scanned | 0.95 | 642 | tables on pp. 15, 34 (user); 248 MB |
| `Рыбы_бассейнов_Нижнего_Дона_и_Кубани_11138.pdf` | scanned | 0.95 | 109 | table on p. 14 (user); 155 MB |

### OCR probe: Рыбы…, page 14 (the named table)

Raster 734×1074 px @150 dpi (0.31 s), rapidocr 22.9 s, **167 text lines**,
boxes present. Flat output scrambles the table exactly as the map's
destination says:

```
Таблица 2
Экологические показатели рыб бассейнов Нижнего Дона и Кубани
Бассейн / Бассейн / Нижнего / Кубанн / Показатель / Экологическне / группы / кол-во / Дона / удель- ...
```

Reading order interleaves column headers; «Кубани»→«Кубанн»,
«Экологические»→«Экологическне» (typical scan-OCR noise). Zero pipe-table
structure. This page is the primary slow-tier gate: header row must land as
`(Показатель, …Бассейн…группы, …Дона…, …Кубани…)` cells, not interleaved.

## Native journals for comparison pairs

| file | type | conf | pages | table pages (1-idx) | columns |
|---|---|---|---|---|---|
| `Altai_Osmans_of_the_Genus_Oreoleuciscus_and_African_Barbs_of_the_Barbus_intermed_9716.pdf` | text_based | 1.0 | 9 | 3, 4, 6 | 5 pp |
| `Alien_Species_in_the_Plankton_Community_of_the_Volga-Don_Shipping_Canal_2025_4046.pdf` | text_based | 1.0 | 15 | 5, 6, 8, 9 | 12 pp |
| `Assessment_of_Morphometric_Variations_Among_the_Populations_of_Asian_Sheat_Catfi_3928.pdf` | text_based | 1.0 | 2 | — | — |

Pairing logic for quality gates: native extraction of the same-species
journal tables gives golden cell text; the scanned anchors' OCR must
reproduce that cell text in `TableBlock.rows` (fuzzy-substring match, not
exact — scan noise proven above).

**Caveat recorded:** `10.1134_*` / `10.1007_*` / `10.11646_*` names are
NOT reliable PDFs (HTML payload). Anchor selection must magic-check every
file (the router's own detector does; so should tickets).

## djvu sample (3 of 33, uploads/)

`-_1_34547570.djvu`: 235 pp, text layer 4230 B ≈ per-page headers only.
`-_2_7c51cd65.djvu`: 232 pp, 4176 B. `-_3_b7754266.djvu`: 231 pp, 4158 B.
Files are chapter-splits of one work (~230 pp each — the v0.5 "largest 552
pp" file is a different item). Text layers are tiny → OCR path dominates,
same as v0.5 concluded. Whether any djvu page holds a real table = visual
question → HITL, do not guess.

## Open questions for the user (HITL)

1. Slow-tier set: OK to gate on Рыбы p. 14 + Генетические pp. 15/34 + the
   two journal pairs (pages 3/4/6 and 5/6/8/9)? Any project-doc scan with a
   big table you want added?
2. djvu: point me at a djvu (or page number) where you KNOW there's a
   table — or accept djvu coverage via the shared pdf scan path only.
3. Commit-size: slow tier points at corpus paths (v0.5 precedent) — no
   fixture copies of 155–248 MB files. Confirm.

## Feed-forward to TKT-ST-03

- The structure stage must handle: bordered tables (both scanned anchors),
  multi-column journal pages (pairs), and Russian scan noise (fuzzy gates).
- rapidocr 150 dpi rasters are small (734×1074) — inside wired-table-rec's
  ≤1500 px sweet spot (see ocr-structure-tools.md), no rescale needed.
- OCR cost per page ~23 s here vs vendor's table models ~0.1–0.5 s —
  structure stage is noise next to OCR, as v0.5's render-vs-OCR ratio was.
