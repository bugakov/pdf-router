# TKT-OI-01 — Research: legacy .doc toolchain landscape

## Question

Which tool (if any) can turn the corpus's legacy `.doc` files into IR-grade
material under the project's decision rule — **quality ≥ honest baseline
AND deps ≤ baseline** — and does anything qualify at all? Evidence feeds
TKT-OI-04 (structure bar + strategy) and, if nothing qualifies, the honest
Out-of-scope ruling for `.doc`.

Facts already on the table (map Notes, 2026-09-28): ~90 OLE2 files
(`d0cf11e0a1b11ae1`), 7 HTML files masquerading as `.doc`, 2 Word lock
files (`~$…`, 162 B), sizes 32 KB → **276 MB**; no pptx/xls in the corpus.
The user's decree: "Research решит" — this ticket decides with evidence,
nothing is pre-ruled (ADR-0003's anti-binary ruling was docx-specific;
ADR-0004 admitted `djvulibre-bin` on evidence).

Candidates to investigate (primary sources: PyPI/GitHub/official docs;
license file status checked in-repo, not just README claims):

- **antiword**, **wv / wvText** — classic text extractors; maintenance
  state, Unicode/Cyrillic handling (the corpus is Russian), table
  survival, availability on this machine (`command -v`, apt);
- **LibreOffice/soffice headless** — `--convert-to txt|html|docx`
  quality and wall-clock on a 100 MB+ file; measured as a *candidate*,
  not pre-rejected;
- **Apache Tika** — Java dependency weight, CLI mode, structure output;
- **Python route**: `olefile` / `compoundfiles` + hand parsing of the
  `WordDocument`/`Table` streams (what antiword does — what would OUR
  native parse cost?); `mammoth`/`docx2txt` do not apply to OLE2;
  `textract` (stale?);
- **OfficeReadCpp** (RapidAI) — license status (**no LICENSE file as of
  2026-09-28** = cannot be used), Python binding situation, build cost;
  measured only if a binding/CLI path exists at all;
- Anything else surfaced by search (Rust/Go ports, `RapidAI/OfficeRead_new`).

Empirical runs required — on **real corpus files**, at minimum:

- small text/table file: `/home/scanum/kotel-zamechanna/**/Акт передачи
  пунктов.doc` (33 KB) or `Таблица 8.doc` (~45 KB);
- medium: `ПР ИГДИ (00845.2024.07).doc` (273 KB);
- **big**: one of the 56 MB `00847.2024.07-ИГМИ_Изм.1.doc` class —
  wall-clock + peak memory, and does the output still contain the text
  (and any surviving tables) or time out;
- one HTML-as-doc file (e.g. `190-ФЗ_Градостроительный_кодекс.doc`) —
  what does a binary extractor do with it (garbage? honest empty?).

For each candidate record: license, dependency class (pure-Python /
C-ext / external binary / JVM), install path (must not touch the project
`pyproject.toml`/`uv.lock` — use `uv run --no-project --with …`, an
ephemeral venv, or system packages), wall-clock on the files above,
output fidelity (do tables survive as rows? headings? Cyrillic intact?),
maintenance state. Mind the shell hard deadline (5 min) — run big-file
timings via `nohup … &` + poll.

Deliverable: `docs/wayfinder/research/doc-tools.md` in the shape of
`docs/wayfinder/research/docx-tools.md` (verdict line first, then the
candidate table, then per-candidate evidence with the runs quoted) + a
pointer appended to this ticket. No project code changes.

Blocked by: nothing — frontier.

## Research findings

Findings: [`../research/doc-tools.md`](../research/doc-tools.md) (2026-09-28,
five real corpus files: 33 KB / 46 KB / 274 KB / 56 MB OLE2 + one
HTML-masquerading `.doc`, every claim quoted from a direct run).

**`office-oxide` 0.1.12 (MIT OR Apache-2.0) is the one qualifying candidate** —
one package, zero transitive deps, 1.8 MB wheel, no external binary/JVM (deps
≤ baseline), and text recall 1.000 on all five files with `to_ir()` table rows
identical to the LibreOffice→docx ground truth (F2: 4 tables/32 rows exact;
F3: 184 = 184 rows) plus an honest typed failure on the HTML masquerade
(quality ≥ baseline) — with the caveats that it is a 6-month-old 0.1.x project
from a single author and its heading recall collapses to 1 vs 42 on the 56 MB
file. Everything else fails the dependency leg (LibreOffice 256 MB on disk /
915 MB RSS / 26 s per file, Tika ≈276 MB JVM+jar with a connection error at
56 MB and two 180 s timeouts, antiword/wv/catdoc as external GPL binaries,
textract = LibreOffice + 13 packages) or the quality leg (antiword recovers
only 57% of the big file's text and `-x db` times out quadratically, wv emits
zero heading levels and times out at 56 MB, catdoc hangs on two of five files,
OfficeReadCpp has no license file anywhere — API `license: null`, `/license`
404 — and is text-only anyway). Native structure parse remains possible but
costs antiword-scale code (reference implementations: 37,429 and 40,258 lines
of C/H), so the strategy call — pin-and-validate `office-oxide`, keep
LibreOffice as a QA oracle only, or rule `.doc` structure out of scope — passes
to TKT-OI-04.

## Resolution

_Closed 2026-09-28 by the research subagent; full findings with citations
and quoted runs: [`../research/doc-tools.md`](../research/doc-tools.md)._

**Verdict: `office-oxide` 0.1.12 (MIT OR Apache-2.0, one package, zero
transitive deps, 1.8 MB wheel) is the only candidate passing both legs of
the rule** — text recall 1.000 on all five corpus files (incl. 56 MB),
table rows identical to the LibreOffice→docx ground truth, typed error on
the HTML masquerade. Everything else fails deps (LibreOffice 256 MB,
Tika ≈276 MB JVM, antiword/wv/catdoc external GPL) or quality (antiword
57% text @56 MB, wv zero headings + timeout, catdoc hangs, OfficeReadCpp
`license: null`).

Open calls handed to TKT-OI-04: 0.1.x/single-maintainer risk (pin
`0.1.12`), heading recall 1 vs 42 on the big file, the
pure-Python-only reading of "deps", and the HTML-masquerade → format
dispatch (TKT-OI-05).
