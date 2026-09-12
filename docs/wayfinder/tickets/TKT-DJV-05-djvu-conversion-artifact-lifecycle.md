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

Blocked by: TKT-DJV-02.

## Resolution

_(open)_
