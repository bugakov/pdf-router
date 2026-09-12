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

_(open)_
