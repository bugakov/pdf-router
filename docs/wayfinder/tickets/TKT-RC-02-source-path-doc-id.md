# TKT-RC-02 — IR delta: Metadata.source_path (additive) + doc_id

## Question

Land the only IR change this map needs: `Metadata.source_path: str |
None = None`, filled by `core.process_pdf` with the input path it
already receives (both adapters and OCR paths funnel there — confirm
the single fill point). No IR_VERSION bump (additive field with a
default; `from_dict` tolerates its absence — old payloads keep
loading). Add the `doc_id` derivation used by chunks: 12-hex SHA-256
of the absolute path — reuse the exact algorithm from
`extract_images` (TKT-RT-12); if that helper is adapter-local, lift it
to a shared home (one function, one definition). `doc_title` policy
lives in RC-03; this ticket only guarantees the renderer can see
`source_path` + a canonical `doc_id`.

Include: `to_dict`/`from_dict` round-trip test, an old-payload
(without the field) load test, the fast-tier test for doc_id
stability, and a line in the IR contract section of README + glossary
(`Metadata` entry / doc-id term already exists — extend, do not
duplicate).

Blocked by: [TKT-RC-01](TKT-RC-01-summary-seam.md).

## Resolution

Done 2026-09-14. Additive, no bump:

- `Metadata.source_path: str | None = None` in `src/pdf_router/ir.py`
  (docstring marks it 0.5.1); `from_dict` tolerates absence — old
  payload load test in `tests/test_ir.py` alongside the round-trip.
- Single fill point confirmed: `core.process_pdf` stamps the input
  path once after the format gate (`src/pdf_router/core.py`).
- No doc_id helper was lifted — `extract_images.doc_id_from_path` was
  already the shared TKT-RT-12 implementation; the chunks renderer
  imports it from there (one definition).
- README IR contract section + glossary got the `source_path` lines
  (the doc-id term was extended, not duplicated: it now names the
  chunk `doc_id` consumer).
