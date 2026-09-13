# TKT-DJV-09 — ADR-0004: djvu ingest strategy

## Question

Write `docs/adr/0004-djvu-ingest.md` mirroring ADR-0003's structure:
context (djvu = scan-native; PDF-shaped downstream), decision (the strategy
plus all locked sub-decisions from TKT-DJV-03..08), alternatives with
rejection reasons and evidence links (TKT-DJV-01 research, this map),
consequences (incl. any IR bump demanded by TKT-DJV-03/04, the external
binary ruling, the PageSource-seam disposition), implementation checklist.

Status-quo reference: the `src/pdf_router/formats.py` docstring
deliberately deferred this decision — the ADR retires that deferral.

Blocked by: TKT-DJV-03, TKT-DJV-04, TKT-DJV-05, TKT-DJV-06, TKT-DJV-07,
TKT-DJV-08.

## Resolution

**Resolved (task, 2026-09-13).** `docs/adr/0004-djvu-ingest.md` written,
mirroring ADR-0003's structure: context (scan-native format, PDF-shaped
downstream, corpus facts), decision (strategy C + all locked
sub-decisions 1–10 from TKT-DJV-02..08/11/12), alternatives with rejection
reasons (A, B, python-djvulibre, re-OCR, page-strip export), consequences
(incl. the djvulibre-bin ruling and the IR 0.3→0.4 bump), implementation
checklist, references.
