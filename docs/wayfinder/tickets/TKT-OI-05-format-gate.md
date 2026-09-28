# TKT-OI-05 — Format gate: OLE2 + xlsx sniffing

## Question

Teach `formats.py` to recognise the new inputs — mechanical work, but the
exact shape depends on the semantics tickets (what gets accepted, what
each rejection says):

1. **`Format` enum** grows the members TKT-OI-03/04 ruled (`XLSX`? `DOC`?
   or does `.doc` reuse a "converted text" shape if the strategy is
   conversion — follow the ruling, don't pre-name).
2. **`_sniff_zip`** (`formats.py:103-116`): today `word/document.xml`
   → DOCX, `[Content_Types].xml` alone → "OOXML package that is not a
   docx (xlsx/pptx?)". Add the xlsx branch (`xl/workbook.xml` — decide
   the exact marker: `[Content_Types].xml` + `xl/` is broader, catches
   pptx too, which stays rejected with a *precise* message). pptx/other
   OOXML must keep their typed rejection (regression test exists:
   `tests/test_formats.py:62-67`).
3. **OLE2 branch**: magic `d0cf11e0a1b11ae1` → the `.doc` path (or its
   rejection per TKT-OI-04); **before** that: `~$` lock-file pattern and
   HTML magic (`<!DOCTYP…`, `<html`) with their own verdicts.
4. **Error messages**: every `UnsupportedFormatError` string updated to
   name what was actually found (extension-lying tests at
   `tests/test_formats.py:110-124` keep passing; add the new ones —
   renamed `.doc`→xlsx, HTML-as-`.doc`, lock file, pptx).
5. **Dispatch**: `_INSPECTORS` registration point(s) — the real
   registration lands with TKT-OI-08/09; here decide whether the gate
   accepts-and-then-fails ("no inspector") or rejects until the
   inspector ships (prefer: land gate + inspector together, no window
   where `route()` advertises an unsupported format — state the landing
   order).
6. **`--info` routing** (`cli.py:95-107`): one-door pattern extended;
   `core.py:213`/`core.py:284` source_format branches audited (xlsx and
   the `.doc` verdict must not accidentally enter the OCR text pass or
   the pdf image path).

Deliverable: rulings + the exact detection predicates recorded here;
code lands in TKT-OI-08/09 (no dangling gate changes).

Blocked by: TKT-OI-03, TKT-OI-04.

## Resolution

_Closed 2026-09-28 (spec confirmed by the user; code lands in
TKT-OI-08/09 — this ticket owns the predicates)._

**`Format` enum grows** `XLSX = "xlsx"`, `DOC = "doc"`, `HTML = "html"`.

**`detect_format` predicate order** (new branches starred):

| # | predicate | result |
|---|---|---|
| 0 | ★ `p.name.startswith("~$")` | `UnsupportedFormatError("Word lock/owner file (~$…), not a document: {path}")` |
| 1 | empty head | "empty file" (unchanged) |
| 2 | `b"%PDF-" in head` | `Format.PDF` |
| 3 | `head.startswith(b"AT&TFORM")` | `Format.DJVU` |
| 4 | ZIP magics → `_sniff_zip`: | |
| 4a | `"word/document.xml" in names` | `Format.DOCX` |
| 4b | ★ `"xl/workbook.xml" in names` | `Format.XLSX` |
| 4c | ★ `"ppt/presentation.xml" in names` | error `"PowerPoint pptx is not routed (out of scope): {path}"` |
| 4d | `"[Content_Types].xml" in names` | error, message updated to `"OOXML package that is not docx/xlsx (pptx?): {path}"` |
| 4e | otherwise | "zip archive, not a routed document format" (unchanged) |
| 5 | ★ `head.startswith(b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1")` | `Format.DOC` |
| 6 | ★ `head.lstrip(BOM/WS).lower().startswith((b"<!doctype html", b"<html"))` | `Format.HTML` |
| 7 | otherwise | "unrecognized file format" (unchanged) |

Notes:

- **Lock files are the one deliberate name-based check** (prefix `~$`,
  not an extension) — user-approved exception: their 162-byte content
  (`\x04user\0…`, no OLE2 magic) is not reliably content-detectable.
  Everything else stays magic-bytes-only (doctrine unchanged).
- HTML predicate verified against all 7 corpus files (all start with
  `<!DOCTYPE html>\r\n`, no BOM); `lstrip` covers BOM/leading-WS
  variants anyway.
- pptx keeps a typed, *precise* rejection (it was previously swept into
  the "(xlsx/pptx?)" message); xls/ppt/odt/rtf remain "unrecognized"
  (no magic known/needed — zero in corpus, map Out of scope).

**Landing map (no advertised-but-dead route window):** each branch
lands in the same ticket as its `_INSPECTORS` entry, adapter, and tests:

- **TKT-OI-08 (xlsx):** `Format.XLSX`, branch 4b, registry entry,
  adapter; `test_detect_ooxml_not_docx_hint` flips to "xlsx is
  recognized and routable"; new pptx-rejection test (4c); OOXML
  message-update test (4d).
- **TKT-OI-09 (.doc + html):** `Format.DOC`/`Format.HTML`, branches
  0/5/6, registry entries, adapters; tests: lock-file message,
  HTML-masquerade routable, OLE2 routable (incl. renamed `.pdf`),
  corrupt OLE2 → `ValueError` (exit 3), HTML `.doc` never reaches the
  OLE2 branch.
- **`--info` (one door, `cli.py:95-107`):** `xlsx_info` / `doc_info`
  branches land with their adapters; HTML gets a minimal info payload.
- **`core.py`:** text/OCR pass already excludes the new formats
  (`:213` checks `("pdf", "djvu")` — no change); one new branch
  `elif source_format == "doc":` for the oxide image sidecar
  (dispatch at `:284`); xlsx/html need no core changes (0 media).
- Module docstring "Supported today" + README format paragraph update
  with the same landings.

Feeds TKT-OI-06 (ADR-0008 gate section), TKT-OI-07 (gate tests in the
quality bar), TKT-OI-08/09 (the code).
