# TKT-DJV-07 — CLI --info and exit codes for djvu

## Question

Follow the docx front-door precedent (TKT-DX-06): `--info` routes through
the one gate for djvu too.

- what `--info` prints for a djvu (page count, size, text-layer presence,
  scan parameters — whatever the inspector metadata exposes);
- exit codes: djvu-unsupported-today vs routable — both must be
  usage-error 1 territory before an inspector exists, then normal flows
  after;
- message wording: the current forward-looking error ("DJVU detected, but
  the router does not support it yet") — does it survive, and what does it
  become under each strategy?

Blocked by: TKT-DJV-02.

## Resolution

**Resolved (grilling, 2026-09-13).** Confirmed:

1. **`djvu_info()` in the djvu adapter**, modeled on `docx_info`
   (`src/pdf_router/adapters/docx.py:612-626`): same-parser-no-drift —
   JSON with `source_format="djvu"`, `page_count`, `pdf_type="scanned"`,
   and the list of pages carrying a hidden text layer. `--info` prints
   it, exit 0.
2. **Exit codes unchanged** — the locked table already covers djvu:
   page failure → 2, document failure → 3, unsupported → 1
   (`src/pdf_router/cli.py:1-8`).
3. **The "does not support it yet" refusal branch stays in the CLI** as
   the template for future formats; for djvu it simply becomes
   unreachable once the adapter lands.
4. **`--info` stays cheap**: only djvused (page count + text-layer
   detection), no rendering, no OCR.
