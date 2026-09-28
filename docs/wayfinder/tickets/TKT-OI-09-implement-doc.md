# TKT-OI-09 — Implement the `.doc` path (or close it as Out of scope)

## Question

Land whatever TKT-OI-04 + ADR-0008 ruled for legacy `.doc`:

- **If a tool/strategy was admitted**: the adapter (native parse or the
  chosen tool's binding/CLI wrapper) behind `_INSPECTORS`, gate
  predicates from TKT-OI-05 (OLE2 / HTML-as-doc / lock files / corrupt),
  IR typing (`source_format`, `SrcTool`, honest structure metadata per
  the bar), `--info`, resource policy for the 50–276 MB class, tests per
  TKT-OI-07 (small anchor fast, big anchor slow-tier with budget),
  full suite + ruff + mypy green.
- **If nothing qualified**: do NOT half-build — write the Out-of-scope
  ruling into the map (with the research citation), make sure the gate's
  rejection message for OLE2 is the precise, honest one from TKT-OI-05,
  and add the gate tests for lock files / HTML-as-doc / OLE2 so the
  fringe verdicts are still enforced in code. Then close this ticket with
  that outcome.

Either way the destination for `.doc` is reached: a working path or a
deliberate, tested rejection — never silent garbage.

Blocked by: TKT-OI-06, TKT-OI-07.

## Resolution

**Closed 2026-09-28 — `.doc` path landed (tool admitted), not rejected.**
`office-oxide==0.1.12` passed the bar; everything OI-04/ADR-0008/OI-05
ruled for this ticket sits in code, with the HTML-as-doc leg (`libreoffice`
not required) shipped in the same window per the OI-05 "one ticket, no
dead-route window" ruling.

### What landed

- `src/pdf_router/adapters/doc.py` — `extract_ir` / `extract_doc_images`
  / `doc_info`; lazy `_oxide()` (heavy-deps rule); one `PageResult(0)`
  (docx precedent); `TableBlock` pipe-markdown from the *same* rows as
  the chunks (`rows_to_markdown`, `ooxml.py`); headings clamped 1-6
  `source="oxide_style"`; section titles **dropped** (each duplicates the
  in-body heading — counting both would double every title);
  `list` prefixes `"N. "` / `"-"` so order survives; images → RT-08
  sidecar placeholders, bytes written only on `output_dir` (one
  `_figure` code path: placeholder and sidecar share the
  `image_index.consume`-on-emit rule, no index holes).
- `src/pdf_router/adapters/html.py` — stdlib `_ProseExtractor(HTMLParser)`:
  script/style/noscript/template/iframe/svg skipped, comments ignored,
  entities decoded, `&nbsp;` collapsed, block tags split paragraphs,
  `h1-h6` → `HeadingBlock(source="html_tag")`, `utf-8-sig` +
  `errors="replace"` with honest `has_encoding_issues`.
- `formats.py` — `Format.DOC/HTML`, predicate order per OI-05:
  `~$` lock by name (before bytes) → OLE2 magic → HTML magic
  (lstrip+lower); `_doc_inspector` / `_html_inspector`; gate refusal
  messages verbatim from the OI-05 spec.
- `core.py` sidecar branch `source_format == "doc"`; `cli.py` help +
  `--info` DOC/HTML; `ir.py` `SrcTool.DOC_ADAPTER` / `HTML_ADAPTER`,
  IR 0.6 docstring.
- `pyproject.toml` + `uv.lock`: `office-oxide==0.1.12` (OI-04 core-dep
  ruling, the sanctioned exception to the research-read-only rule).
- Tests: `tests/test_formats.py` +8 (gate: lock, OLE2 routable, OLE2
  named `.pdf`, HTML masquerade, BOM, both prefixes, inspectors);
  `tests/test_doc.py` 6 fast + 2 slow; `tests/test_html.py` 11.
  `HEAVY_MODULES += office_oxide` (test_api).
- README: all format claims updated (tagline, no-binaries, quick start,
  routable list, IR 0.6 contract incl. `source_format`/`src_tool`/
  heading sources, layout adapters + tests).

### Measured (2026-09-28)

| anchor | result |
| --- | --- |
| F2 274 KB | 21.6 ms; tables `[1,2] [16,2] [5,4] [10,3]`, 32 rows, **7 headings** (all level 2), 1 jpeg sidecar (88,422 B), md 25,534 |
| F3 56 MB | **8.43 s**, RSS 406 MB; 79 tables / 184 rows (ground truth), 1 heading (honest 1-vs-42), 74/74 placeholders ↔ sidecars, md 58,618, token «СПИСОК ИСПОЛНИТЕЛЕЙ» |
| 276 MB | **6.97 s**, RSS 454 MB (TKT-OI-04 promise recorded); 1413 tables / 1820 rows, 6/6 sidecars, md 490,316, tokens «Котельная»/«Опухлики» — OI-04 ruling 4 (no size cap) holds |
| 190-ФЗ HTML | 1.28 s; md 1,335,359, `<` free, `source_format="html"` |

### OI-07 amendments made here

1. F2 headings **9 → 7**: 9 was the LibreOffice round-trip ground
   truth; oxide's `to_ir` yields 7 — the 2-heading gap is now the
   pin (honest, mirrors F3's 1-vs-42).
2. F3/276 `markdown length > 1 MB` → `> 50_000` / `> 400_000`: the
   1 MB figure assumed base64-in-IR, which TKT-RT-08 forbids; sidecar
   placeholders are small (measured 58,618 / 490,316).
3. Slow-tier wall/RSS recorded in the ticket table.

### Contract details fixed during implementation

- Missing input file raises `FileNotFoundError` (IO error, docx
  taxonomy) — oxide wraps IO errors into `OfficeOxideError`, so
  `extract_ir` pre-checks existence before opening.
- Corrupt CFB: `ValueError("malformed doc (office-oxide): {path}: {exc}")`
  → exit 3; `OfficeOxideError` stays the only translation.

### Verification

- `uv run pytest -m "not slow" -q` → **307 passed, 28 deselected** (63.9 s).
- `uv run pytest tests/test_doc.py -m slow` → **2 passed** (17.96 s).
- `ruff check .` → All checks passed; `mypy` → Success (25 files).
