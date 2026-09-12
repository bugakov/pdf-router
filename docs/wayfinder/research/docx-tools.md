# TKT-DX-01 research — ready-made docx tool vs native stdlib parse

Research subagent findings, 2026-09-12. All facts below are verified against
primary sources: PyPI JSON metadata (`pypi.org/pypi/<pkg>/json`), GitHub repo
API, and — for every quality claim — **direct conversion runs on the real
corpus files named in the ticket** plus one synthetic control document.

## Verdict

**No ready-made tool qualifies. The native stdlib parse (zipfile + ElementTree)
stands.**

The only candidate with adequate output quality (markitdown[docx]) fails the
locked constraints: its *base* dependency chain includes `magika 0.6.1`, which
requires `numpy` + `onnxruntime` (a heavy ML runtime) — decisively violating
"ultra-minimal, deterministic, deploy-anywhere" and the no-C-extension rule via
the docx extra's `lxml`. It also inlines images as base64 data-URIs in the
markdown (file 2: 24 KB docx -> 657 KB md), contradicting the sidecar-image
decision. Meanwhile the one thing it does add over a native parse — pipe-table
markdown for the md renderer — is ~10 lines of code the native adapter must
have anyway to fill `TableBlock.rows` + `TableBlock.markdown` from its own 2D
list.

Candidate status table (the decision rule: quality >= native parse AND deps <=
native parse; both must hold):

| candidate | tables survive? | images | heading levels | deps (docx use) | lxml? | onnxruntime? | maintained | qualifies? |
|---|---|---|---|---|---|---|---|---|
| **mammoth** (1.12.2) | **NO — md writer destroys them on all 3 corpus files + control** | base64 inline; header image skipped | yes (built-in styles) | 1 pure-Python (`cobble`) | no | no | yes (released today, 2026-09-12) | **NO — quality** |
| **markitdown[docx]** (0.1.7) | yes — pipe tables on all 3 + control | base64 inline (contradicts sidecar) | yes (built-in) | **~10 incl. numpy, onnxruntime, requests, bs4, lxml** | yes | **yes (magika)** | yes (0.1.7 on 2026-07-29) | **NO — constraints** |
| **python-docx** (1.2.0) | yes — exact rows | inline_shapes only; anchored images invisible to it | via style map | lxml + typing_extensions | **yes** | no | yes | **NO — lxml** |
| **docx2python** (3.7.1) | yes — exact nested lists | `save_images` to folder | no (pars not typed by level) | **lxml>=6.1.1** + paragraphs + typing-extensions | **yes** | no | yes | **NO — lxml** |
| **docx2txt** (0.9) | **NO — all structure destroyed (0 structured lines on all 3)** | extracts to folder (incl. header) | no | **0 deps** | no | no | **stale** (2025-03-24) | **NO — quality + stale** |
| pandoc (3.1.3) | best rows (colspan/rowspan preserved), but complex tables degrade to raw HTML in gfm | skipped | yes (built-in) | **external binary — benchmark only** | n/a | n/a | n/a | n/a — out of scope by lock |
| **native stdlib zipfile+ET** | full control; rows as 2D list (exact); vMerge unwrap | rels+media extraction to sidecar (full control, incl. headers) | styleId -> w:name -> builtIn map | **0 deps** | no | no | n/a (stdlib) | **BASELINE** |

## 1. Ground truth on the corpus (stdlib zipfile inspection)

The 3 ticket corpus files — the quality yardstick:

- **F1** `fin-fine/data/docs/ФНС_контрольные_соотношения_ЕА-4-15-2048_2023.docx`
  (21,995 B; 12 zip entries; no media): 1 `w:tbl` of 28 `w:tr` x 140 `w:tc`
  (5 cols, no `vMerge`/`gridSpan`); 162 `w:p`; **zero heading styles** — only
  paragraph style used is `a3` -> `List Paragraph` (1 para).
- **F2** `kotel-zamechanna/examples/Протокол ответов/№316 от 17.03.2026 г. -
  Ответы на замечания. Крюково.docx` (330,414 B; 24 entries): 1 `w:tbl` of
  3 `w:tr` x 6 `w:tc` (2 cols, 3 `vMerge` cells); **2 PNGs** — `image1.png`
  referenced from the body (`a:blip rId8`) and `image2.png` referenced from the
  **page header** (`word/_rels/header2.xml.rels`; a mailto-logo); used styles:
  `ab` -> `List Paragraph`; `a7` -> `Subtitle` present in styles.xml but
  **unused in the body**; zero heading styles.
- **F3** `kotel-zamechanna/archive/ВЫДАЧА_БМК Лехово/Титул на папки передачи
  БМК Лехово.docx` (17,420 B; 15 entries; no media): 1 `w:tbl` of 6 `w:tr` x
  18 `w:tc` (3 cols, 12 `vMerge` cells); used style `a5` -> `List Paragraph`;
  zero heading styles.

Corpus-wide observations (locked expectations, confirmed):

- The generator-made corpus is **heading-light** (0/3 files use heading
  styles) and **table-rich** (3/3 files have exactly 1 table each). Table
  fidelity is therefore the *critical* quality asset, not headings.
- Generator docs obfuscate styleIds (`a3`, `ab`, `a5`); a docx parser must
  resolve them through `word/styles.xml` (`w:name`), never match raw styleIds.
- Media lives in `word/media/` and may be referenced from **body or header
  rels** (F2); both parts' rels must be walked.

## 2. Corpus conversion results (direct runs)

Counted output of each tool on the 3 real files (structure-preserving metrics;
"structure" = pipe lines for markdown, `<tr>`/`<td>` for HTML, exact dims for
2D-list APIs):

| tool | F1 (28x5) | F2 (3x2, vMerge 3) | F3 (6x3, vMerge 12) | images | F2 md size |
|---|---|---|---|---|---|
| mammoth `convert_to_markdown` | **0 pipe lines, 0 structure — table destroyed**, cells leak as bold-flattened paragraphs | destroyed | destroyed | 1 `data:image` base64 inline; **header logo skipped** | **357,142 B (10x the 24 KB source)** |
| mammoth `convert_to_html` | 28 `<tr>` / 140 `<td>` — exact | 3 `<tr>` / 4 `<td>` (vMerge -> rowspan) | 6 `<tr>` / 9 `<td>` (12 vMerge -> rowspan) | skipped | — |
| markitdown[docx] `text_content` | **pipe table preserved** (30 pipe lines, 5 cols) | preserved (5 pipe lines) | preserved (8 pipe lines) | base64 `data:image` inline in md | 657 B |
| pandoc `-f docx -t gfm --wrap=none` | 0 headings, 0 pipe, **1 raw `<table>`** (complex table degrades to HTML in gfm; `colspan`/`rowspan` preserved) | raw `<table>` | raw `<table>` | 0 (header logo skipped) | — |
| python-docx (body-order walk) | **rows=28, cols=5 — exact** | rows=3, cols=2 | rows=6, cols=3 | `inline_shapes`=0 on F2 — **the anchored body picture is invisible to python-docx**; header paras=1 | — |
| docx2python (body nesting) | preserved exact: `body > list[28] > list[5] > list[1] > str` | preserved | preserved exact (6x3) | `save_images`/`images` folder | — |
| docx2txt `process` | **0 structured lines** — cell boundaries destroyed (5,481 B flat text) | destroyed | destroyed | extracts **both PNGs** (incl. header logo) to sidecar | — |

Merged cells: F2 has 3 and F3 has 12 `vMerge` cells; mammoth's HTML writer
reconciles them into rowspans (140/140 exact on F1, which has none; 6 `w:tc` ->
4 `<td>` on F2 and 18 -> 9 on F3). A native parser can unwrap vMerge into the
IR's 2D `rows` with full control (repeat or blank the continuation cells) —
2D-list APIs and stdlib reconcile; markdown emitters (markitdown) flatten.

## 3. Synthetic control (heading + table + custom style)

Built with python-docx: `Heading 1` "Отчет о проверке", `Heading 2` "Раздел 1:
Методика", a custom style paragraph ("МойЗаголовок" — the unknown-style case
generator docs hit), and a 3x2 table:

- **mammoth md**: `#` / `##` correct; custom style -> plain paragraph +
  warning (no crash); **table destroyed — 0 pipe lines**, cells leak as plain
  lines. Confirms the corpus result is a *writer* limitation, not corpus
  pathology.
- **markitdown[docx]**: `#` / `##` correct (2 heading lines); custom style ->
  plain paragraph (no crash); **pipe table preserved** (5 pipe lines).
- **pandoc gfm**: `#` / `##`; custom style plain; pipe table preserved.
- **docx2python**: preserves paragraph/table interleaving in `body` as nested
  lists, but exposes **no heading levels** (pars are not typed by level) and
  needs `"".join(unwrap(...))` glue per cell.

Heading conclusion: on the corpus (no heading styles at all) all tools emit 0
headings — a wash. On the control, built-in English heading styles are
detected identically by mammoth/markitdown/pandoc, and custom/obfuscated
styles are ignored identically. A native parser resolves the same information
by walking `word/document.xml` paragraph styles through `word/styles.xml` to
the builtIn heading map — same quality, no dependency.

## 4. Dependency weight (the locked constraint)

Per-package `requires_dist` from PyPI (fetched 2026-09-12):

- **native stdlib**: **0 dependencies.**
- **docx2txt 0.9**: **0 dependencies** (but destroys all structure —
  disqualifying; and stale, 31 open issues, last touched 2025-03-24).
- **mammoth 1.12.2** (BSD-2-Clause, released 2026-09-12): 1 pure-Python dep,
  `cobble>=0.1.3`. Minimal — but its *markdown writer* destroys tables on 3/3
  corpus files + control, failing the quality leg alone. (Its HTML writer is
  fine, but then you still need HTML->markdown/rows conversion — `markdownify`
  + glue — which is exactly markitdown's docx path; see its deps.)
- **python-docx 1.2.0** (MIT): `lxml>=3.1.0` (C extension) +
  `typing_extensions>=4.9.0`. **lxml violates the locked no-C-extension rule.**
- **docx2python 3.7.1**: `lxml>=6.1.1` (C extension) + `paragraphs>=1.0.1` +
  `typing-extensions`. **lxml again.**
- **markitdown 0.1.7** (`markitdown[docx]`): base deps are
  `beautifulsoup4`, `charset-normalizer`, `defusedxml`, `magika~=0.6.1`,
  `markdownify`, `requests`; the `[docx]` extra adds `lxml` + `mammoth~=1.11.0`
  (-> `cobble`). The decisive blow: **magika 0.6.1 requires `numpy`,
  `onnxruntime`>=1.17, `click`, `python-dotenv`** — an ML content-type
  *detector* (irrelevant here; the Inspector registry already detects by magic
  bytes) dragging in a heavyweight inference runtime. Total ~10 packages
  including 2 C extensions (`lxml`, `onnxruntime`) for one format. It also
  pins skewed versions (`mammoth~=1.11.0` vs current 1.12.2; `magika~=0.6.1`
  vs current 1.0.3).

Popularity / maintenance (GitHub API + PyPI downloads, 2026-09-12):

| package | stars | open issues | last push | latest release | downloads/mo |
|---|---|---|---|---|---|
| markitdown | 182,863 | 636 | 2026-09-11 | 0.1.7 (2026-07-29) | 14,630,851 |
| python-docx | 5,716 | 521 | 2026-08-01 | 1.2.0 (2025-06-16) | n/a (rate-limited) |
| docx2txt | 583 | 31 | 2025-03-24 (stale) | 0.9 (2025-03-24) | 3,461,868 |
| mammoth | 1,115 | 25 | 2026-09-12 | 1.12.2 (2026-09-12) | n/a (rate-limited) |
| docx2python | 211 | 1 | 2026-09-07 | 3.7.1 (2026-08-06) | n/a (rate-limited) |

Popularity is *not* the test — constraint fit and output quality are.
markitdown's huge star count and download numbers make it the safe "community
default," but its dep chain is an order of magnitude past the locked ceiling,
and the heavy part (`magika` -> `onnxruntime` + `numpy`) does work the format
dispatch seam already owns (magic-byte detection).

## 5. Integration effort to the IR

- **markitdown[docx]**: 1 call -> full markdown. But to feed the locked IR it
  must then: strip/extract base64 `data:image` payloads to the sidecar
  (contradicting its inline behavior), parse pipe tables back into
  `rows: list[list[str]]`, and accept that it yields **no heading levels** on
  the corpus anyway (0/3 files) — plus page semantics are unresolved (docx has
  no pages; TKT-DX-02 applies to it equally). Its markdown is also 10x source
  size when images are present (base64).
- **native stdlib** (~100-150 lines, exactly shaped to the IR contract):
  walk `w:body` children in document order (`w:p` / `w:tbl`);
  heading level from `w:pStyle` -> `word/styles.xml` `w:name` -> builtIn
  heading map (obfuscated IDs handled); `w:r/w:t` text; `w:tbl/w:tr/w:tc` rows
  as exact 2D list (vMerge unwrap); image parts via `word/_rels/**.rels`
  (body + headers) -> `word/media/*` extracted to the sidecar folder as PNG
  paths (TKT-RT-08 decision — never base64); title from `docProps/core.xml`.
  The pipe-table markdown for `TableBlock.markdown`/md renderer is generated
  from the same 2D rows in ~10 lines — the only code markitdown would have
  saved, which its output then forces us to *parse back*.

## 6. Recommendation for the ADR

1. **Native stdlib adapter** (`zipfile` + `ElementTree`) through the locked
   Inspector registry seam. Quality: rows exact, media complete (incl.
   headers), heading map by builtIn style name — all equal or better than the
   candidates. Constraints: 0 deps, no C extensions, no external binaries.
2. **mammoth stays out** despite today's fresh release: its markdown writer
   destroying tables on 3/3 corpus files + control is disqualifying for a
   table-rich corpus (its HTML writer + `markdownify` would be a hand-rolled
   markitdown).
3. **markitdown stays out**: quality adequate, constraints violated —
   `onnxruntime`/`numpy`/`requests`/`lxml` dep chain, base64 image inlining,
   version skew — and its output must be *parsed back* to feed `TableBlock`.
4. **pandoc** remains a benchmark only (locked; best rows fidelity noted for
   future quality-bar comparisons in TKT-DX-05 — complex tables degrade to
   raw HTML in gfm, which pandoc preserves with colspan/rowspan).
5. If the corpus ever becomes heading-style-rich, mammoth/markitdown/pandoc
   built-in detection and the native builtIn-map approach converge — the
   native path does not degrade.

## Appendix: evidence trail

- PyPI JSON: `mammoth` (1.12.2, BSD-2, released 2026-09-12, dep cobble),
  `python-docx` (1.2.0, MIT, 2025-06-16, deps lxml+typing_extensions),
  `markitdown` (0.1.7, 2026-07-29; base deps bs4/charset-normalizer/defusedxml/
  magika~=0.6.1/markdownify/requests; extra docx = lxml+mammoth~=1.11.0),
  `magika` (0.6.1: click, numpy, onnxruntime, python-dotenv, Apache-2.0),
  `docx2python` (3.7.1, 2026-08-06, deps lxml>=6.1.1+paragraphs+typing-ext),
  `docx2txt` (0.9, 2025-03-24, 0 deps).
- GitHub API stars/pushed_at table above; pypistats where not rate-limited.
- Conversion runs: `uv run --no-project --with <pkg>` ephemeral envs on the 3
  ticket files; pandoc 3.1.3 CLI; synthetic control built and converted
  in-session. No project files (`pyproject.toml`, `uv.lock`) modified; no
  files outside `docs/wayfinder/research/` written.
