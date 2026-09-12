# TKT-DJV-01 — Research: djvu toolchain & corpus survey

## Question

What are the facts any djvu strategy must build on? Four evidence bundles:

1. **Corpus ground truth** — the 92 djvu under
   `/home/scanum/vbrdb/orchestrator/uploads/` (read-only probes):
   - how many carry a **hidden text layer** (`djvused -e output-txt` /
     `dump-txt`), and a rough quality signal per file (chars per page,
     share of non-blank lines, garbage-ratio heuristics);
   - page counts, file sizes, JB2-vs-IW44 dominance (`djvused -e size`,
     `djvudump` shape), scan resolution if visible;
   - a shortlist of 3–5 representative files (small / typical / large, with
     and without text layer) as golden-anchor candidates for TKT-DJV-08.
2. **pymupdf capability check** — confirm from primary sources (PyMuPDF
   docs / supported-format matrix) that djvu is NOT openable by pymupdf;
   this kills any "inspect djvu via pymupdf" idea before it is proposed.
3. **python-djvulibre bindings** — maintenance state (last release, Python
   ≥ 3.12 compatibility, wheels vs build-from-source against
   libdjvulibre), API surface for: rendering pages to pixmaps in memory,
   reading the hidden text layer, document/page metadata.
4. **ddjvu / djvused CLI envelope** — the flags that matter per strategy:
   `-format=pdf` (+ `-quality`, `-mode`, `-scale`/`-dpi`, `-subtype`),
   per-page rasterization, and — critically — whether ddjvu's PDF output
   carries the hidden text layer (expectation: it does not; verify from
   docs/source). Exact djvused text-dump commands and output structure
   (`output-txt` vs `output-escaped-txt` vs `dump-txt`).

Constraints: read-only on the corpus; experiments in ephemeral environments
(`uv run --no-project --with <pkg> python -c …` or plain system `ddjvu`) —
never touch `pyproject.toml` / `uv.lock`. Findings with primary-source
citations go to [`../research/djvu-tools.md`](../research/djvu-tools.md).

## Resolution

_Closed 2026-09-12 by the research subagent; full findings with citations:
[`../research/djvu-tools.md`](../research/djvu-tools.md)._

**Verdict: 3/33 corpus files (9.1%) carry a hidden text layer — all three
high-quality (word-level zones, 97–98% alphanumeric lines, 1,300–3,200
chars/page) — so a native text-layer path is a bounded bonus (~18% of corpus
bytes), not an OCR replacement. ddjvu's PDF output never carries the text
layer (upstream source + man page + empirical run). PyMuPDF cannot open
djvu. python-djvulibre works on Python 3.12 only with a `cython<3` build
pin. All three strategies (A/B/C) remain viable; the text-layer fact forces
a separate `djvutxt`/`djvused` extraction path regardless of which wins.**

Evidence:

1. **Corpus ground truth** — the map's "~92" is stale: **33** .djvu files
   under `uploads/` (21 visible + 12 `._`-prefixed dotfiles; `covers/` +
   `thumbnails/` hold none). All 33 probe cleanly (0 failures). Pages:
   median 209 (p25 131, p90 335, max 552); sizes: median 7.86 MB (max
   21.69 MB; 282.6 MB total). Encoding: 33/33 JB2 stencils, 31/33 + IW44
   photo background (compound; 4 BG44 chunks = 4 progressive slices of one
   page); only 2/33 uniform page dims; DPI ∈ {72, 300, 600}. Text layers:
   `._._-__d2c86e35.djvu` (186 pp, 8.25 MB sexpr), `._-__18163057.djvu`
   (209 pp, 5.37 MB), `._._-_-_1967_a9d40ca0.djvu` (262 pp, 5.04 MB).
   Probe gotcha: `djvused -e output-txt` emits a *re-ingest script*, not
   text — a text-less document yields the 19-byte `select; remove-txt`.
2. **ddjvu PDF = image-only** — `-format=pdf` renders pixels → temp TIFF →
   embedded `tiff2pdf()`; no text-chunk code path exists (upstream
   `tools/ddjvu.cpp`). Empirical: ddjvu→PDF of a text-layer file inspected
   with PyMuPDF: 0 chars/page, one image XObject per page, producer
   `libtiff / tiff2pdf`. No `-dpi`/`-subtype` flags exist; `-scale=N` is
   the dpi-ish control (1..999); default pagespec is page 1 only unless
   `-format` is given (then `1-$`).
3. **pymupdf cannot open djvu** — PyPI "Supported File Formats" input
   table (PDF, XPS, EPUB, CBZ, MOBI, FB2, SVG, TXT, MD + images; Office via
   Pro) has no DjVu (zero page occurrences); locally PyMuPDF 1.28.2 raises
   `FileDataError: Failed to open file '...' as type djvu.`
4. **python-djvulibre 0.8.8** (2022-09-02, Jakub Wilk, GPL-2, sdist-only,
   no wheels ever): stock build fails on Python 3.12 (Cython 3 —
   `sexpr.pyx:157:28: Cannot assign type ... noexcept`); with `cython<3` +
   `--no-build-isolation` it builds and imports fine, and the smoke test
   proved in-memory rendering (`PageJob.render`, exact byte counts at 1x/2x)
   and text sexprs (`PageText.sexpr`) against a corpus file.
5. **djvused text commands** (3.5.28 + upstream master): `print-txt`
   (page-anchored sexpr zones, **origin bottom-left**, pixels — y-flip
   needed for PDF injection), `print-pure-txt` (UTF-8; `\f` pages, `\013`
   columns, `\035` regions, `\037` paragraphs), `output-txt` (script, see
   gotcha). **`output-escaped-txt` / `dump-txt` do not exist** in 3.5.28 or
   master — ticket premise corrected. `djvutxt` (installed) = plain text or
   `-detail` sexprs — the cleanest extractor.

Golden anchors for TKT-DJV-08 (all under
`/home/scanum/vbrdb/orchestrator/uploads/`): `4139_from_s3.djvu` (25 pp,
0.34 MB, no text), `._._2_32e52013.djvu` (129 pp, 7.86 MB = median, no
text), `._._-__d2c86e35.djvu` (186 pp, 15.19 MB, richest text layer),
`._-__18163057.djvu` (209 pp, 20.46 MB, text), `4172_from_s3.djvu` (247 pp,
21.69 MB, largest, no text).

**Consequence for TKT-DJV-02 / ADR-0004:** strategy A is existence-proven
(reference pipeline), B is a one-liner with the same image-only caveat, C
has two proven renderers (ddjvu per-page; python-djvulibre with build
friction). The hidden text layer is readable cheaply via `djvutxt` when
present (9% of files) but never arrives inside ddjvu's PDF — the text-layer
policy ticket (TKT-DJV-04) decides whether to read it in parallel or let
OCR do all the work.
