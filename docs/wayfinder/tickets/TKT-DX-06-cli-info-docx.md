# TKT-DX-06 — CLI --info for docx

## Question

Remaining candidate-5 residue (architecture report): `--info` currently goes
through `pdf_inspector.classify_pdf()` — PDF-only, bypassing the public API.
After docx lands, does `--info` route through the same front door
(format-aware: pdf → classify, docx → adapter metadata) or stay PDF-only with
an honest typed refusal (`UnsupportedFormatError` path) for docx?

Blocked by: TKT-DX-03 (metadata contract).

## Resolution

_Closed 2026-09-12 (grilling)._

**`--info` routes through the one front door** (format-aware): pdf →
`classify_pdf`, docx → adapter metadata (title, table count, media count),
unrecognized/unsupported → typed refusal. Kills the parallel API-bypass path
(architecture-report candidate 5 residue).
