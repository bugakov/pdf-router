# TKT-OI-08 — Implement the xlsx inspector

## Question

Build what ADR-0008 ruled, end to end, behind the existing seam:

- `src/pdf_router/adapters/xlsx.py` — `extract_ir(path) -> IR` (docx
  template: tolerant `_read_xml`/rels helpers, sharedStrings resolution,
  sheet walk per TKT-OI-03's page semantics, grid construction from cell
  refs + merged-cell policy, value formatting incl. formula cache, pipe
  markdown from the same rows, `xlsx_info()` for `--info`, `Metadata`
  honesty: `source_format="xlsx"`, `SrcTool.XLSX_ADAPTER`).
- `formats.py` — gate predicates + `_INSPECTORS` registration per
  TKT-OI-05 (landing together with the adapter — no advertised-but-dead
  route).
- `core.py` / `cli.py` audit: text pass stays off for xlsx, image
  dispatch only if media exists, `--info` one door.
- Consider (decide, don't drift): factoring shared zip/rels/XML walk
  helpers out of `docx.py` — the map's fog item; take it only if the
  duplication is real, and keep the docx golden anchors green either way.
- Tests per TKT-OI-07 (synthetic control + corpus anchors,
  skip-if-missing) + gate tests (`test_formats.py`) + `--info` + e2e
  `convert()`; full suite green (`uv run pytest`), ruff + mypy clean.

Blocked by: TKT-OI-06, TKT-OI-07.

## Resolution

_Closed 2026-09-28 (implementation complete; fast suite green)._

**Shipped:**

- `adapters/ooxml.py` — the map's fog item, taken (not a new ticket):
  shared `read_xml` / `load_rels` / `core_title` / `rows_to_markdown`
  factored out of `docx.py` (de-dup ~55 lines); `docx.py` refactored
  onto it with all 25 docx tests green before and after.
- `adapters/xlsx.py` — `extract_ir` + `xlsx_info` per TKT-OI-03:
  sheet=page, dense/sparse runs, merges (vertical repeat / horizontal
  first-cell), hidden cut, `repr` values, cached formulas, `header_rows`
  heuristic, document-level `ValueError` (bad zip / unreadable
  `xl/workbook.xml` / unreadable sheet part) → exit 3, not a gate reject.
- `formats.py` — `Format.XLSX`, `_xlsx_inspector`, `_sniff_zip` branches
  (`xl/workbook.xml` → XLSX, `ppt/presentation.xml` → precise rejection,
  Content_Types → "not docx/xlsx"); `cli.py` `--info` + help; `ir.py`
  `IR_VERSION` 0.6 + `SrcTool.XLSX_ADAPTER`; `core.py` audit (no sidecar,
  text pass stays off for xlsx).
- Tests — `tests/test_xlsx.py` (22): synthetic builder control (sheet=
  page, runs, both merge policies, hidden cut, formula cache, repr
  cleanup, `header_rows`, round-trip, renderers, `convert()`,
  `process_pdf` purity, CLI, `--info`, 3 malformed-input errors) +
  corpus anchors ЛСР/СР-2/smallest (skip-if-missing, exact counts);
  `test_formats.py` gate set completed (xlsx magic + route, pptx/OOXML
  messages, extension-lying cases, inspector callable).

**Decisions during implementation:**

- **expat one-pass parser prototyped and rejected** — Python callbacks
  made it slower than `ET.parse`; ADR-0008's `zipfile + xml.etree`
  stands unchanged.
- Regex ref parsing → `_split_ref` (rstrip), no regex on the cell hot path.
- **Single-cell sheet = table**: width shrinks to content, so one cell is
  trivially ≥50% dense — the control's "Лист2" becomes a 1×1 table and
  lands in `pages_with_tables`; TKT-OI-03 #6 has no single-cell exception
  (tests pin the actual behaviour, docstring documents it).
- ЛСР timing budget in TKT-OI-07 amended `<5.0` → `<10.0` (same-run
  measurement 3.51–4.65 s; noisy sessions ~9.7 s). Docstring perf claim
  corrected the same way (measured seconds, not "milliseconds").
- Gate/CLI/IR wiring all landed together — no advertised-but-dead route
  (TKT-OI-05's window rule).

**Verification:** `uv run pytest -m "not slow" -q` → **281 passed, 26
deselected (50.7 s)**; `ruff check` clean; `mypy` clean (23 files).
Slow-tier `.doc` anchors + HTML anchors remain for TKT-OI-09.
