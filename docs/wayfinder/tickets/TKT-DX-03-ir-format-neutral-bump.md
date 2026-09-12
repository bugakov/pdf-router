# TKT-DX-03 — IR bump: format-neutral contract

## Question

The IR is semantically PDF-flavored (`Metadata.pdf_type`,
`is_complex_layout`, `has_encoding_issues`; `SrcTool` has only PDF-tool
members; `IR_VERSION = "0.2"`). What is the minimal bump so an IR produced
from docx is honest, without breaking renderers (`markdown` stays
authoritative)?

Sub-decisions:

1. new `Metadata.source_format: "pdf"|"docx"` field vs reinterpreting
   `pdf_type` for docx (architecture-report candidate 2 proposes
   `source_format`; `pdf_type`/bbox frame stay honestly PDF-specific);
2. `SrcTool` member for the docx producer — the name depends on TKT-DX-01's
   outcome (e.g. `DOCX_ADAPTER` vs a tool-specific name);
3. `IR_VERSION` bump policy (0.2 → 0.3?) and `to_dict`/`from_dict` round-trip
   for the new fields;
4. what docx puts into `pdf_type`, `confidence`, `is_complex_layout` — typed
   neutral values vs format-specific honesty.

Blocked by: TKT-DX-01 (tool name), TKT-DX-02 (page semantics shape).

## Resolution

_(open)_
