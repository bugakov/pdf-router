# TKT-DJV-08 — Quality bar: golden anchors on the vbrdb corpus

## Question

Pick golden anchors from the 92-file corpus (shortlist arrives from
TKT-DJV-01) and define the test policy:

- 2–4 anchors: small + typical + large, with/without hidden text layer;
- invariants: determinism (two runs → identical markdown), `page.error`
  honesty, sidecar purity under `output_dir=None`;
- **fast-test policy**: OCR on CPU is expensive (v0.4 era: 15 slow tests ≈
  35 min) — djvu e2e on scanned anchors would OCR; pre-rendered tiny
  fixtures vs `-m slow` markers vs stubbed OCR engine; the fast suite must
  stay fast;
- regression guards for the lying-extension family (`.djvu` that is
  actually a PDF, etc.), mirroring `tests/test_api.py` patterns.

Blocked by: TKT-DJV-01 (the shortlist), TKT-DJV-02 (what "e2e" even runs).

## Resolution

**Resolved (grilling, 2026-09-13).** Confirmed:

1. **Anchors — 4 of the 5 shortlisted** (research §5):
   - small `4139_from_s3.djvu` (25 pp, no layer) — fast smoke;
   - typical `._._2_32e52013.djvu` (corpus median);
   - text `._._-__d2c86e35.djvu` (richest layer) — the text-path golden
     anchor;
   - largest `4172_from_s3.djvu` (247 pp) — stress.
   The fifth (209 pp + layer) is spare — redundant in role.
2. **Fast tier**: tiny committed djvu fixtures (2–3 pp, built once via
   c44/djvm; one with a text layer via djvused) + stubbed OCR
   (monkeypatch). Covers gates, counters, the raster seam, purity
   (`output_dir=None`), and the lying-extension family (PDF named
   `.djvu`, djvu named `.pdf` — magic bytes decide).
3. **Slow tier (`-m slow`)**: real OCR on the small anchor —
   determinism (two runs → identical markdown); text anchor — layer
   pages → `DJVU_TEXT_LAYER`, no OCR runs on them.
4. **Invariants in both tiers**: `page.error` honesty (failed counter ==
   pages carrying an error), purity (no `output_dir` → `images=[]`,
   zero disk writes).
