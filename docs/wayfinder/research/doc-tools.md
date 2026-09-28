# TKT-OI-01 research — legacy `.doc` toolchain vs the native baseline

Research subagent findings, 2026-09-28. All facts below are verified against
primary sources: PyPI JSON (`pypi.org/pypi/<pkg>/json`), GitHub repo API
(license endpoint + raw `LICENSE` URLs), Debian package metadata
(`dpkg-deb -f`), source tarballs, and — for every quality claim — **direct
runs on the real corpus files named in the ticket** (33 KB / 46 KB / 274 KB /
56 MB OLE2 `.doc` + one HTML-masquerading `.doc`), each tool run through
`uv run --no-project --with …`, an extracted `.deb` under `/tmp/opencode`, or
the system `soffice`/`pandoc`. No project file was touched (`git status` shows
only the pre-existing untracked wayfinder docs).

## Verdict

**One candidate qualifies: `office-oxide` (PyPI 0.1.12, MIT OR Apache-2.0) —
it satisfies both legs of the rule (`deps ≤ baseline`: one package, zero
transitive dependencies, no external binary, no JVM, 1,810,012 B wheel;
`quality ≥ baseline`: text recall 1.000 on all five corpus files including the
56 MB one, table rows identical to the LibreOffice→docx ground truth, heading
levels and document metadata, honest typed error on the HTML-masquerading
file), while every other candidate fails the dependency leg (LibreOffice 256 MB
on disk, Tika ≈276 MB JVM+jar, antiword/wv/catdoc external GPL binaries,
textract→LibreOffice + 13 packages) or the quality leg (catdoc hangs, wv emits
zero headings and times out at 56 MB, antiword recovers only 57% of the big
file's text, OfficeReadCpp has no license at all).**

Three caveats that belong to TKT-OI-04, not to the verdict:

1. `office-oxide` is **0.1.x** — first release ~2026-04, latest 0.1.12 on
   2026-09-21, single author, 138 stars (`yfedoseev/office_oxide`, created
   2026-03-01, pushed 2026-09-23). The measured behaviour is excellent; the
   *maintenance* risk is real and is a strategy call, not a measurement.
2. Its heading recovery degrades on the big file: **1 heading vs 42** from the
   LibreOffice→docx adapter (table rows are exact there: 184 = 184). Text and
   rows never degraded anywhere.
3. If "deps ≤ baseline" is read as **pure-Python only** (no compiled wheel at
   all), then *nothing* qualifies and the honest outcome is native text-only
   `.doc` + an Out-of-scope ruling for structure. That reading is
   inconsistent with the project's existing envelope — `pyproject.toml` already
   ships `onnxruntime`, `pymupdf`, `rapidocr` (all compiled wheels, all far
   heavier than one 1.8 MB abi3 wheel).

Candidate status table (decision rule: **quality ≥ honest baseline AND deps ≤
baseline**; both must hold):

| candidate | deps (class) | license | text recall (F1a/F1b/F2/F3) | tables | headings | big file (56 MB) | HTML-as-doc | qualifies? |
|---|---|---|---|---|---|---|---|---|
| **office-oxide 0.1.12** | **1 pkg, 0 transitive, wheel 1.8 MB, in-process** | MIT OR Apache-2.0 | **1.000 / 1.000 / 1.000 / 1.000** | `to_ir`: 1×[2,2], 0, **4×32 rows exact**, 79×184 rows | levels present (2 / 1 / 7 / **1 of 42**) | **4.10 s**, rc=0 | rc=1 honest typed error | **YES** (caveats above) |
| native parse (olefile + FIB reader) — **baseline** | 1 pkg (`olefile`), 0 transitive, pure Python | BSD-2 | 1.000 / 1.000 / 1.000 / 1.000 (partial) | **none** (keeps `\x07` cell marks: 116 on F2) | **none** | **420 s timeout** (prototype) | rc=1 (not OLE) | **BASELINE** (text-only) |
| LibreOffice 24.2.7.2 (`soffice --headless`) | **external suite, 256 MB on disk, 214–915 MB RSS** | MPL/LGPL/GPL-3 (tri-licensed) | 1.000 / 1.000 / 1.000 / ref | via `--convert-to docx`: 1 / 0 / 4×32 / 26×184 | via docx adapter: 0 / 1 / 9 / **42** | 9.39 s txt, 25.87 s docx | rc=0 (renders the HTML, 70 s) | **NO — deps** |
| antiword 0.37 (2005) | **external binary, 589 KB, libc6 only, GPL** | GPL (Debian) | 1.000 / 1.000 / 1.000 / **0.573** | plain: pipe grid; `-x db`: **32 `<row>` / 84 `<entry>` exact** | none (`-x db`: 2 `<title>`, 1 `<chapter>`) | 0.435 s txt, **`-x db` 420 s timeout** | rc=1 `is not a Word Document.` | **NO — deps + 57% text on big file** |
| wv / wvHtml / wvText 1.2.9 (2010) | **external binary, 732 KB, GPL-2+** | GPL-2+ | 1.000 / 1.000 / 1.000 / 0.997 | wvHtml: **32 `<tr>` / 84 `<td>` exact** | **0 `<h*>` anywhere** | wvHtml 27.79 s / 129 MB; **wvText 420 s timeout** | rc=255, 0 B | **NO — deps + no headings** |
| catdoc 0.95 (2016) | **external binary, 675 KB, libc6 only, GPL-2** | GPL-2 | 1.000 / 1.000 / 1.000 / — | tabs (41 tab-lines on F2) | none | **420 s timeout** | **300 s timeout**, emits raw HTML | **NO — deps + hangs** |
| Apache Tika 3.3.2 | **JVM 203 MB + jar 73 MB ≈ 276 MB**, client deps bs4+requests | Apache-2.0 | 1.000 / 1.000 / 1.000 / **rc=1, 0 B** | not captured (text layer only) | not captured | **62.4 s, rc=1, connection dropped** | **180 s timeout**, 2,446,493 B partial | **NO — deps + reliability** |
| OfficeReadCpp (RapidAI) | C++20 build, **no Python bindings**, no external runtime | **NO LICENSE (API `license: null`, `/license` 404)** | 1.000 / 1.000 / 1.000 / — | none (`-markdown` byte-identical to `-text-only` on `.doc`) | none | not run (no license = may not be used) | rc=1 `not an OLE compound file` | **NO — unlicensed + text-only** |
| textract 2.1.0 | **13 packages** incl. lxml, pillow, pdfminer-six, xlrd — `.doc` = shell-out to `soffice` | MIT | n/a (wraps LibreOffice) | via LibreOffice | via LibreOffice | n/a | n/a | **NO — deps** |
| markitdown 0.1.8 | base: bs4, charset-normalizer, defusedxml, **magika~=0.6.1**, markdownify, requests | MIT | — | — | — | — | — | **NO — no `.doc` converter exists** |
| compoundfiles 0.3 (2014) | container-only (CFB reader), MIT | MIT | — | — | — | — | — | **NO — dead (last upload 2014-09-01), no Word text layer** |
| pandoc 3.1.3 | external binary (benchmark only) | GPL-2+ | — | — | — | — | — | **n/a — cannot read `.doc`** |

## 1. The baseline: what a native parse honestly gives us

Baseline = `olefile 0.47` (BSD-2-Clause per `decalage2/olefile/LICENSE.txt`,
PyPI `requires_dist` has only `pytest`/`pytest-cov` test extras → **0 runtime
dependencies**, sdist 112,240 B) + a hand-written `WordDocument` reader
(FIB → piece table → text). Prototype written for this research:
**127 lines** (`native_doc.py`).

Measured (`uv run --no-project --with olefile python native_doc.py …`):

| file | wall | RSS | bytes | recall vs LibreOffice text | structure emitted |
|---|---|---|---|---|---|
| F1a 33 KB | 8.581 s (cold uv env; warm ≈0.8 s) | 47 MB | 1,434 | 1.000 | text + 6 `\x07` cell marks, 0 rows, 0 headings |
| F1b 46 KB | 0.834 s | 43 MB | 736 | 1.000 | text only (0 cell marks — the table there is an image), 0 rows, 0 headings |
| F2 274 KB | 0.775 s | 43 MB | 47,948 | 1.000 | text + 116 cell marks, 0 rows, 0 headings |
| F3 56 MB | **420.022 s → timeout (rc=-9)**, 88,025 B partial | 45 MB | partial | 1.000 of the reference tokens it reached | partial text |
| F4 HTML | 3.291 s, **rc=1** (traceback: not OLE) | 43 MB | 0 | — | honest failure |

So the honest native baseline is: **full text at zero new dependencies, and no
structure** — plus a runtime that is fine on small files but *does not scale*
in unoptimized Python (antiword does the same 56 MB file in **0.435 s**, so the
cost is code optimisation, not compute).

## 2. Ground truth (LibreOffice → docx → the project's own stdlib adapter)

Structure yardstick produced with `soffice --convert-to docx` +
`pdf_router.adapters.docx.extract_ir` (read-only; no project file modified):

| file | tables | rows | headings | adapter wall |
|---|---|---|---|---|
| F1a | 1 × [2, 2] | 2 | 0 | 18.5 ms |
| F1b | 0 (table is an image) | 0 | 1 (level 3) | 87.5 ms |
| F2 | 4 × [1,2], [16,2], [5,4], [10,3] | **32** | 9 | 168 ms |
| F3 56 MB | 26 (184 rows total) | **184** | **42** (96 figures) | 801 ms (after 25.87 s conversion) |

Cyrillic is intact in every tool's output **except** where noted below; no tool
emitted `U+FFFD` on any file.

## 3. Small + medium corpus runs (direct, quoted)

Runner: `bench.py` (per-run `wait4` max-RSS, 300 s deadline on small/medium
files, 420 s on the big file, per-tool env).
Python tools run via `uv run --no-project --with …`, so wall time includes
interpreter/env start; `office-oxide` in-process parse times are quoted
separately below.

| tool | F1a wall / out | F1b wall / out | F2 wall / out | F2 structure |
|---|---|---|---|---|
| `antiword -m UTF-8.txt` | 0.071 s / 2,257 B | 0.068 s / 6,756 B | 0.065 s / 52,784 B | 54 pipe-grid lines |
| `antiword -x db -m UTF-8.txt` | 0.056 s / 2,952 B | 0.004 s / 2,822 B | 0.066 s / 60,251 B | **32 `<row>` / 84 `<entry>` — cell-exact**, but 0 heading levels |
| `catdoc -d utf-8` | 0.345 s / 1,549 B | 0.055 s / 1,421 B | 0.075 s / 48,334 B | 41 tab-separated lines |
| `wvWare -1 -x wvText.xml` | 1.599 s / 1,481 B | 0.211 s / 1,524 B | 0.535 s / 48,192 B | 8 tab lines, literal `\x07` cell marks survive |
| `wvHtml` | 1.651 s / 12,953 B | 0.368 s / 28,615 B | 0.775 s / 154,679 B | **32 `<tr>` / 84 `<td>` exact, `<h*>` = 0** |
| `native_doc.py` | 8.581 s / 1,434 B | 0.834 s / 736 B | 0.775 s / 47,948 B | 116 cell marks, no rows/headings |
| `soffice --convert-to txt:Text (encoded):UTF8` | 23.549 s / 1,438 B | 9.491 s / 1,193 B | 14.897 s / 47,753 B | tables flattened (0 cell marks) |
| `soffice --convert-to html:HTML (StarWriter)` | 8.916 s / 8,500 B | 12.009 s / 37,369 B | 11.547 s / 185,532 B | 32 `<tr>` / 83 `<td>`, **2 headings** (`<h1 class="раздел">`, `<h2 class="пункт-1-раздела-western">`) |
| `soffice --convert-to docx` | 14.018 s / 6,763 B | 11.303 s / 17,032 B | 5.045 s / 117,277 B | 4 tables / 32 rows / 9 headings (via adapter) |
| `office-oxide extract_text` | 3.803 s (cold uv) / 1,437 B | 0.366 s / 1,393 B | 0.410 s / 47,667 B | recall 1.000 |
| `office-oxide to_markdown` | 0.409 s / 1,459 B | 0.458 s / 1,421 B | 0.508 s / 48,368 B | **36 pipe lines + 7 `##` headings** |
| `office-oxide to_ir` (in-process) | **3.0 ms** | **1.5 ms** | **34.6 ms** | 1×[2,2] / 0 / **4×32 rows (dims exact)** + metadata |
| OfficeReadCpp `-text-only` | 0.052 s / 1,409 B | 0.058 s / 718 B | 0.057 s / 47,483 B | text only (`-markdown` byte-identical) |
| Tika (`tika==3.3.2` client) | see §5 (flaky) | 4.023 s / 3,140 B (warm server) | 15.647 s / 48,210 B | text layer only |
| `pandoc -f doc -t plain` | rc=21 | rc=21 | rc=21 | `Pandoc can convert from DOCX, but not from DOC.` |

**Cyrillic handling** (the corpus is Russian):

- `antiword` is locale-dependent: correct under a UTF-8 locale / `-m UTF-8.txt`,
  but `LC_ALL=C antiword …` → `????? ?-4` (question marks) — verified both ways.
- `catdoc` needs `-d utf-8` plus a `charset_path`/`map_path` config (Debian's
  shipped `/etc/catdocrc` targets `8859-1`); with `-d utf-8` output is correct.
- everything else correct by default. Raw `\x07` cell marks survive into the
  text of `wvtext` (F1a 6 / F1b 71 / F2 116 / F3 996) and of the native
  prototype (F1a 6 / F2 116 / F3 1,251 partial); `office-oxide` and LibreOffice
  `txt` emit **0** cell marks (cell boundaries become tabs/newlines — oxide
  writes 124 tab characters on F2), and antiword converts them on small files
  but leaked 173 on the 56 MB file.
- **`antiword -x db` is the one mojibake found in this research**: its stderr
  prints `XML doesn't need a mapping file`, and the DocBook `<title>`/`<bookinfo>`
  fields come out cp1251-as-latin1 (`Êàïèòàëüíûé ðåìîíò ñ çàìåíîé…`, 304 latin-1
  characters) while the body text is fine (20,607 Cyrillic chars, recall 1.000).

## 4. The 56 MB file (F3, `00847.2024.07-ИГМИ_Изм.1.doc`, 56,133,120 B)

Run via `nohup bench.py F3_big56 … 420 big` + polling. Text recall is measured
against the LibreOffice txt export (87,027 B, 2,153 unique tokens):

| tool | wall | RSS | output | rc | text recall |
|---|---|---|---|---|---|
| `antiword -m UTF-8.txt` | **0.435 s** | 13 MB | 61,490 B | 0 | **0.573** |
| `antiword -x db` | **420.008 s → killed** | 13 MB | 65,536 B partial | -9 | — (quadratic DocBook writer) |
| `catdoc -d utf-8` | **420.041 s → killed** | 13 MB | 65,536 B partial | -9 | — |
| `wvWare -1` (wvText) | **420.037 s → killed** | 14 MB | 65,536 B partial | -9 | — |
| `wvHtml` | 27.786 s | **129 MB** | 729,130 B (HTML) | 0 | 0.997 |
| `native_doc.py` | **420.022 s → killed** | 45 MB | 88,025 B partial | -9 | 1.000 of what it reached |
| `soffice --convert-to txt` | 9.391 s | 249 MB | 87,027 B | 0 | 1.000 (reference) |
| `soffice --convert-to html` | 15.353 s | **866 MB** | 440,537 B HTML + images = 54,480,481 B | 0 | 0.997 |
| `soffice --convert-to docx` | 25.872 s | **915 MB** | 52,170,482 B | 0 | (→ 26 tables / 184 rows / 42 headings) |
| `office-oxide` `to_ir()` | **4.099 s** (in-process) | — | 93,879 B text, **79 tables / 184 rows**, 1,447 paragraphs, 74 images, 1 heading | 0 | **1.000** |
| Tika client | **62.444 s** | 48 MB (client) | **0 B** — client traceback ends in `urllib3` `getresponse` (server dropped the connection) | 1 | — |
| `pandoc` | 0.868 s | 16 MB | — | 21 | — |

Two facts worth carrying into TKT-OI-04:

- **antiword is fast but lossy at scale**: 0.435 s and 13 MB RSS, yet only
  **57.3%** of the reference vocabulary — 61,490 B vs 87,027 B. Speed is not
  quality.
- **office-oxide's row inventory matches LibreOffice exactly at 56 MB
  (184 = 184)**, and its text recall is 1.000, but its heading detection on this
  file yields 1 heading vs the adapter's 42 (its 79 tables vs the adapter's 26
  reflect different merging of adjacent tables — same 184 rows).

## 5. The HTML-masquerading file (F4, 3,059,959 B, `<!DOCTYPE html>`)

What each binary does with a non-OLE file (quoted stderr/rc):

| tool | result |
|---|---|
| `antiword` | **rc=1**, stderr: `…190-ФЗ_Градостроительный_кодекс.doc is not a Word Document.` — 0 B output (honest) |
| `catdoc` | **300.048 s hang → killed**, 65,536 B of *raw HTML* streamed (`<!DOCTYPE html><html lang="ru"…`) — worst case: garbage + hang |
| `wvWare` / `wvHtml` | rc=255, 0 B (honest) |
| `native_doc.py` | rc=1, traceback (not OLE) — honest |
| `office-oxide` | **rc=1**, `OfficeOxideError : CFB error: invalid CFB header: bad magic signature`, 0 B (honest, typed) |
| OfficeReadCpp | rc=1, `officeread: not an OLE compound file` (honest) |
| Tika | **180.022 s → killed**, 2,446,493 B partial (it happily parses the HTML as HTML, twice, but does not finish) |
| `soffice --convert-to txt` | rc=0 in **70.481 s**, 2,445,872 B — it *renders* the masquerade |
| `pandoc` | rc=21 (cannot read `.doc`) |

The masquerade therefore cannot be used to discriminate text quality — but it
separates tools into *honest rejectors* (antiword, wv, native, oxide, orc) and
*hangers/silent-succeeders* (catdoc hangs; Tika times out; LibreOffice
succeeds on 70 s of work the format-dispatch seam should be deciding).

Tika reliability note (post-JRE-fix runs): cold client starts failed 3 times
with `Tika startup log message not received after 3 tries` (rc=1, 16–18 s) —
the server needs >15 s to print its banner on this machine, and a warm server
then served F1b in 4.023 s and F2 in 15.647 s. Server footprint measured here:
JRE tree **203 MB** (`du -sh /tmp/opencode/jre`) + `tika-server-standard-3.3.2.jar`
**76,497,574 B**.

## 6. Per-candidate evidence

### 6.1 office-oxide (the qualifier)

- **PyPI**: `office-oxide 0.1.12`, uploaded **2026-09-21**, `license = "MIT OR
  Apache-2.0"`, `requires_dist = None` (**zero runtime dependencies**),
  wheel `office_oxide-0.1.12-cp38-abi3-manylinux_2_34_x86_64.whl` =
  **1,810,012 B**, 9 files, none yanked. Summary: "The fastest Office document
  processing library for Python — DOCX, XLSX, PPTX, DOC, XLS, PPT".
- **Repo**: `yfedoseev/office_oxide` — license `Apache-2.0`, 138 stars, created
  **2026-03-01**, last push **2026-09-23**; repo carries `LICENSE-MIT` +
  `LICENSE-APACHE`; docs at `office.oxide.fyi`, changelog in-repo; author
  Yury Fedoseev.
- **Quality (direct runs)**: recall 1.000 on F1a/F1b/F2/F3; `to_ir()` matches
  the LibreOffice→docx ground truth on tables for F1a (1×[2,2]) and F2
  (4 tables, dims `[1,2] [16,2] [5,4] [10,3]`, 32 rows) and on rows for F3
  (184 = 184); heading levels present (F1b: level 3, matching the adapter
  exactly; F1a: 2 headings where the adapter finds 0); metadata block carries
  title/author/created/modified; `to_markdown()` keeps tables as pipes and
  headings as `##`; on the HTML masquerade it fails typed and immediately
  (0.360 s, rc=1).
- **Speed/footprint**: in-process parse 1.5 ms (F1b) / 3.0 ms (F1a) / 34.6 ms
  (F2) / **4.099 s (F3, 56 MB)**; client RSS 44–49 MB in the harness; no
  subprocess, no server, no config files.
- **Risks**: 0.1.x (6 months old, single maintainer); heading recall collapsed
  on F3 (1 vs 42); image/sidecar semantics and IR mapping are design work for
  TKT-OI-04, not measurements.

### 6.2 LibreOffice (the quality ceiling, fails the dependency leg)

- Installed on this machine: `/usr/bin/soffice`, **LibreOffice 24.2.7.2
  420(Build:2)**; `/usr/lib/libreoffice` = **256 MB** (`du -sh`), 9
  `libreoffice-*` packages; root `COPYING` (GPLv3) + `COPYING.LGPL` +
  `COPYING.MPL` → tri-licensed.
- Quality is unmatched: the only tool that converts the HTML masquerade
  (70 s), and `--convert-to docx` + the project's stdlib adapter is the only
  route with *both* table rows and heading levels at 56 MB (26 tables / 184
  rows / 42 headings / 96 figures).
- Cost: 5–26 s per file, **214–915 MB RSS**, 256 MB on disk, external
  binary. `--convert-to txt` also **flattens tables** (0 cell marks on F2), so
  the usable structure route is the docx round-trip (conversion + adapter).
- Under the rule: quality ≥ baseline, deps ≰ baseline → **NO** (this is the
  ADR-0004-style "admit a heavy binary on evidence" decision, if TKT-OI-04 ever
  wants to take it).

### 6.3 antiword 0.37 (fast and tiny, but structurally and textually short)

- Debian `antiword 0.37-16`, **Installed-Size 589 KB**, `Depends: libc6`
  only; upstream `ReadMe` dates **0.37 to 21 Oct 2005** (author line "1998-2005
  Adri van Os"), Debian package 2020-02-28; license GPL (Debian
  `copyright`).
- Extracted to `/tmp/opencode/root` (no apt, no sudo — `sudo` requires a
  password here); mapping file needed for Cyrillic (`-m UTF-8.txt`).
- Text: recall 1.000 on F1a/F1b/F2, **0.573 on F3**; 0.065–0.435 s everywhere.
- Structure: plain mode gives pipe grids; `-x db` gives **cell-exact tables**
  (F2: 32 `<row>` / 84 `<entry>`; DocBook tag census: 241 `<para>`, 84
  `<entry>`, 32 `<row>`, 14 `<listitem>`, 4 `<tbody>`, 2 `<title>`, 1
  `<chapter>` — i.e. **no heading levels**) and **mangles property text**
  (`Êàïèòàëüíûé…`, see §3); on F3 `-x db` **does not finish in 420 s** while its
  plain mode takes 0.435 s — the DocBook writer is quadratic on large files.
- Under the rule: deps are a GPL external binary (allowed-by-precedent, but
  heavier than the baseline) and quality misses headings + big-file text → **NO**.

### 6.4 wv 1.2.9 / wvText / wvHtml (good tables, zero headings)

- Debian `wv 1.2.9-8`, **Installed-Size 732 KB**, `Depends: libc6, libglib2.0,
  libgsf-1, libwv`; upstream tarball built **2010-10-10**; license **GPL-2+**
  (source `COPYING`). Source: **40,258 lines** of C/H.
- `wvHtml --datadir=…`: **32 `<tr>` / 84 `<td>` exact on F2** (cell-exact) but
  **zero `<h1>`–`<h6>` in the whole file** (headings are styled `<p>`/`<div>`),
  recall 0.991–1.000 on small files, 0.997 at 56 MB in 27.8 s / 129 MB.
- `wvWare -1 -x wvText.xml` (the Debian `wvText` wrapper needs `elinks`/`lynx`,
  which are absent, so the underlying converter was invoked directly): correct
  text on small files but **420 s timeout on the 56 MB file**; it also leaves
  literal `\x07` cell marks in the text.
- Under the rule: deps = external GPL binary; quality = no heading levels +
  one hang → **NO**.

### 6.5 catdoc 0.95 (hangs)

- Debian `catdoc 1:0.95-5build1`, **Installed-Size 675 KB**, `Depends: libc6`;
  upstream `NEWS` dates **0.95 to May 25 2016**; license **GPL-2**.
- Small/medium: correct text with `-d utf-8` (0.055–0.345 s), tables arrive as
  tab-separated columns (41 tab-lines on F2), no heading information.
- **Hangs on two of five corpus files**: 420.041 s timeout on the 56 MB file
  and 300.048 s timeout on the HTML masquerade (streaming raw HTML while it
  hangs).
- Under the rule: **NO — fails quality (reliability) and deps**.

### 6.6 Apache Tika (dependency and reliability failure)

- Python client `tika==3.3.2` (PyPI: **Apache-2.0**, deps
  `beautifulsoup4>=4.13.3`, `requests`, uploaded 2026-08-01; repo
  `chrismattmann/tika-python`, 1,666 stars, Apache-2.0) + server
  `tika-server-standard-3.3.2.jar` (**76,497,574 B**, verified by download)
  + a JVM (extracted here: **203 MB** tree, OpenJDK 21.0.12.1).
- Quality where it worked: recall 1.000 on F1a/F1b/F2 (1,466 B / 3,140 B /
  48,210 B, Cyrillic intact). Structure was **not** evaluated — the harness
  captures the text layer, and the dependency leg is already lost at ≈276 MB.
- Reliability: 3 cold client starts failed (`Tika startup log message not
  received after 3 tries`), **F3 failed outright** (62.4 s, rc=1, connection
  dropped, 0 B), **F4 timed out twice** at 180 s (2,446,493 B partial).
  Server startup also needed a fixed JRE (`java.security` lives in `/etc`,
  absent for a `dpkg-deb -x` portable JRE) — an environment cost of the JVM.
- Under the rule: **NO — deps ≫ baseline, plus the worst reliability profile
  of any Python-usable option**.

### 6.7 OfficeReadCpp (RapidAI) — legally unusable, and text-only anyway

- `RapidAI/OfficeReadCpp`: **`license: null`, `/license` → 404, raw `LICENSE`
  → 404 on both `main` and `master`** (checked 2026-09-28); 1 star, 0 forks,
  4 commits, created **2026-07-22**, last commit **2026-08-31**. Its README
  says it is a port of `RapidAI/OfficeRead_new`, which now **404s**.
- Built successfully here (CMake 3.20+/C++20 + FetchContent `miniz`): binary
  at `/tmp/opencode/officeread_src/build/officeread`. CLI flags: `-text-only`,
  `-markdown`, `-metadata`, `-images DIR`, `-strict-office-*`. **No Python
  bindings** (`CMakeLists.txt`, `cmd/`, `include/officeread` only).
- Runs: 0.052–0.058 s / 14 MB RSS on small files, recall 1.000, but output is
  **text only** — `-markdown` is byte-identical to `-text-only` on `.doc`
  (47,483 B both on F2; the README's Markdown generation applies to XLSX/
  OOXML), 0 tables, 0 headings. Honest error on the masquerade.
- Under the rule: **NO — no license means it cannot be used at all** (the
  ticket anticipated this; it is now verified twice over), and its quality
  does not exceed the baseline anyway.

### 6.8 Everything else surfaced

- **textract 2.1.0** (PyPI: MIT, uploaded **2026-09-01** — not stale after
  all): `requires_dist` = `pillow, speechrecognition, argcomplete, beautifulsoup4,
  lxml, chardet, docx2txt, extract-msg, pdfminer-six, python-pptx, openpyxl,
  xlrd, odfpy`; its `.doc` parser is a **shell-out to LibreOffice**, so it adds
  13 packages on top of the heaviest dependency on this list → **NO**.
- **markitdown 0.1.8** (MIT, 2026-09-21): the converter tree has **no `.doc`
  converter at all**; base deps include `magika~=0.6.1` (→ `numpy` +
  `onnxruntime`, as documented in `docx-tools.md`) → **NO** (no support).
- **compoundfiles 0.3** (MIT): last upload **2014-09-01**; CFB *container*
  reader only (no Word text layer) → **NO**.
- **pandoc 3.1.3**: `Unknown input format doc` / `Pandoc can convert from
  DOCX, but not from DOC.` (rc=21 on all five files) → benchmark only.
- **pandoc cannot be the `.doc` route**; LibreOffice also cannot be reached
  through it (pandoc has no `-f doc` reader at all).
- Not installed/measured: `rwml` (crates.io, MIT, Rust legacy-`.doc` toolkit —
  no Python bindings), `Alpaq92/JSDoc` (JS, no license file). Both fail the
  "usable from Python without a new binding" test before any measurement.

## 7. What a native `.doc` structure parse would cost

- Text layer: **done in 127 lines** (FIB + piece table), recall 1.000 on every
  OLE2 file; no dependencies beyond `olefile`.
- Structure layer (table rows + heading levels) is where the cost lands:
  it requires the `Table` stream (TAP/PAP/SPRM formatting), cell-boundary
  reconstruction, and stylesheet-driven heading resolution. Reference
  implementations of exactly that work are **antiword 0.37: 37,429 lines of
  C/H across 67 `.c` files** and **wv 1.2.9: 40,258 lines of C/H** (both
  counted from the source tarballs here). This is not a 150-line stdlib
  adapter like the docx route.
- Runtime is *not* the obstacle (antiword: 0.435 s / 13 MB on 56 MB), but an
  unoptimized Python implementation is: our prototype hit the 420 s deadline
  on the 56 MB file. Optimisation is achievable — it is engineering time, and
  it buys exactly what `office-oxide.to_ir()` already returns.

## 8. Recommendation feeding TKT-OI-04

1. **Take `office-oxide` as the `.doc` ingest candidate** for the strategy and
   structure-bar ticket: it is the only option that passes both legs of the
   rule on measured evidence (1 package, 0 transitive deps, 1.8 MB wheel, no
   external process; recall 1.000 everywhere; LibreOffice-exact table rows).
   Validate it against TKT-OI-04's golden anchors before locking it in, and
   pin the version (`0.1.12`) — the project is 0.1.x.
2. **Keep LibreOffice as the reference/QA oracle, not as a dependency**: it is
   the only tool that *converts* the HTML masquerade and the only one with 42
   headings at 56 MB, but 256 MB / 915 MB RSS / 26 s per file is the opposite
   of "ultra-minimal, deterministic, deploy-anywhere".
3. **Reject the classic binaries for the product path** (antiword, wv, catdoc):
   they are external GPL processes with measured quality holes (no heading
   levels; antiword 57% text at 56 MB; catdoc hangs twice), even though they
   are tiny and fast on small files. They stay useful as *benchmarks*.
4. **Reject Tika** (≈276 MB + the reliability record) and **note OfficeReadCpp
   as unlicensed** (usable only if a license appears upstream).
5. **The native-parse escape hatch stays open**: if `office-oxide` is rejected
   on maintenance grounds, the honest fallback is baseline text-only plus an
   explicit Out-of-scope/limited ruling for `.doc` structure — not a silent
   downgrade to LibreOffice.
6. **The HTML masquerade belongs to format dispatch** (TKT-OI-05 / Inspector
   registry): five tools reject it honestly, one hangs, one times out, one
   silently "succeeds" after 70 s. The IR path should never see the file.

## Appendix: evidence trail

- PyPI JSON (fetched 2026-09-28): `office-oxide` 0.1.12 (MIT OR Apache-2.0,
  `requires_dist=None`, 1,810,012 B abi3 wheel, 2026-09-21); `olefile` 0.47
  (BSD, test-only extras, 112,240 B sdist); `tika` 3.3.2 (Apache-2.0, deps
  bs4+requests); `textract` 2.1.0 (MIT, 13 deps); `markitdown` 0.1.8 (MIT, no
  `.doc` converter); `compoundfiles` 0.3 (MIT, 2014-09-01).
- GitHub API (2026-09-28): `yfedoseev/office_oxide` (Apache-2.0, 138 stars,
  created 2026-03-01, pushed 2026-09-23); `RapidAI/OfficeReadCpp`
  (`license: null`, `/license` 404, raw `LICENSE` 404 on main/master, 1 star,
  4 commits, created 2026-07-22); `chrismattmann/tika-python` (Apache-2.0,
  1,666 stars); `decalage2/olefile` (`LICENSE.txt`, 2-clause BSD);
  `RapidAI/OfficeRead_new` → 404.
- Debian metadata via `dpkg-deb -f /tmp/opencode/debs/*.deb`: `antiword 0.37-16`
  (589 KB, libc6), `wv 1.2.9-8` (732 KB, libc6+glib+libgsf+libwv),
  `catdoc 1:0.95-5build1` (675 KB, libc6); upstream `ReadMe`/`NEWS` dates
  2005-10-21 / 2010-10-10 / 2016-05-25; source line counts from the tarballs
  (antiword 37,429 C/H lines, wv 40,258).
- Tool versions on this machine: LibreOffice 24.2.7.2 420(Build:2),
  pandoc 3.1.3; missing system-wide: `antiword`, `catdoc`, `wvText`, `java`
  (all either extracted to `/tmp/opencode/root` or measured as absent —
  `sudo` requires a password, so nothing was installed system-wide).
- Conversion runs: `bench.py` harness with per-run `wait4` max-RSS and
  300 s (small/medium) / 420 s (big file) deadlines; small/medium matrix in
  `log_bench_small` (40 runs), big-file matrix in `log_bench_big` (10 runs),
  Tika in `log_tika_*`; results in
  `results-*.json`, outputs in `out/<tool>/<file>/`. Python tools invoked as
  `uv run --no-project --with office-oxide|olefile|tika python -c …`;
  `soffice`/`pandoc` are system binaries; the extracted tools run from
  `/tmp/opencode/root/usr/bin`. No project code, `pyproject.toml`, or
  `uv.lock` was modified; nothing was committed.
