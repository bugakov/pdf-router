# TKT-DJV-05 — Conversion artifact lifecycle & determinism

## Question

If the chosen strategy produces intermediate artifacts (strategy B: a
temporary PDF per input; strategy C: per-page rasters):

- where artifacts live (tempfile vs configured cache dir) and when they
  are cleaned (immediately, on process exit, never);
- determinism/idempotency: same input → same output; is a converted PDF
  cached and reused across runs (keyed by path+mtime+size? content hash?);
- failure semantics: ddjvu nonzero exit / timeout — typed error at the
  document level vs per-page;
- disk budget and concurrency (two processes converting the same file);
- strategy A dissolves this ticket (the caller owns artifacts).

Shape locked by TKT-DJV-02: strategy C with per-page `ddjvu` subprocess
renders — so the real question is **in-memory bytes vs temp files** for
page rasters, and whether any cross-run caching is worth it. The
measurements from TKT-DJV-12 (per-page render cost) feed this decision.

Blocked by: TKT-DJV-02, TKT-DJV-12.

## Resolution

**Resolved (grilling, 2026-09-13).** Confirmed (mostly pre-decided by the
TKT-DJV-12 measurements — render 17 s vs OCR 42–70 min per book):

1. **No temp files, no cache.** The page raster lives in memory and dies
   with the page (ddjvu writes to stdout, we read bytes). This closes the
   in-memory-vs-temp-files fork left open by TKT-DJV-12.
2. **No cross-run caching** — saves milliseconds against minutes of OCR.
3. **ddjvu failure** (nonzero exit / timeout / empty output) → per-page
   error, other pages continue. Same semantics as OCR failures today
   (`OcrError` per page, `core.py:225-228`).
4. **Concurrency-safe by construction**: one ddjvu subprocess per page,
   no shared state.
