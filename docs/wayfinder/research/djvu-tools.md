# TKT-DJV-01 research — djvu toolchain & corpus survey

Research subagent findings, 2026-09-12. All facts below are verified against
primary sources: local probes on the real corpus (djvulibre 3.5.28 binaries +
their man pages), upstream djvulibre-git sources, PyPI JSON metadata, the
PyMuPDF PyPI page — and, for every behavioral claim, **direct runs**: a
throwaway probe over all corpus files, a ddjvu→PDF conversion inspected with
PyMuPDF, and python-djvulibre built and smoke-tested on Python 3.12.

## Verdict

**(a) Text-layer share: 3 of 33 corpus files (9.1%) carry a hidden text
layer — all three excellent (word-level zone trees, 97–98% alphanumeric
lines, 1,300–3,200 chars/page).** A native text-layer path is therefore a
bounded bonus (~18% of corpus bytes, since the three files are among the
largest), **not** an OCR replacement: 91% of files have no layer at all, so
the OCR pipeline remains the primary text source either way. The layer is
worth reading when present — it is free, high-quality, word-anchored text.

**(b) Rendering mechanisms per strategy** (map: upstream conversion / 
in-router conversion / native render + PageSource seam):

| strategy | mechanism | status |
|---|---|---|
| A — caller-owned ddjvu | caller runs `ddjvu -format=pdf` upstream; router sees a PDF | **proven** — the reference pipeline `~/vbrdb/src/preprocessing/djvu_to_pdf.py` does exactly this (`ddjvu -format=pdf -quality=88` → PyMuPDF+pyvips recompression) |
| B — in-router: ddjvu → temp PDF → existing PDF inspector | same one-liner inside the inspector | **viable**; the produced PDF is **image-only** (see below) so text comes from the inspector's OCR path — or from a parallel `djvutxt` extraction on the original djvu |
| C — native render + PageSource seam | `ddjvu` per-page rasters (`-format=ppm/tiff`, `-page=N`, `-eachpage`) **or** python-djvulibre in-memory pixmaps | **both viable**; ddjvu = zero new deps; python-djvulibre proven on Python 3.12 but needs a `cython<3` build pin (unmaintained since 2022, sdist-only, GPL-2) |

**The fact every strategy must face: ddjvu's PDF output never carries the
djvu hidden text layer.** Verified three ways: upstream source (the PDF path
renders pixels → temporary TIFF → embedded `tiff2pdf()`; no text-chunk code
path exists), the man page ("Each page in the resulting file is represented
by an image"), and an empirical run (ddjvu→PDF of a text-layer file, opened
with PyMuPDF: 0 chars/page, one image XObject per page, producer
`libtiff / tiff2pdf`). If the hidden layer is to be exploited (TKT-DJV-04),
it must be extracted separately via `djvutxt`/`djvused` from the original
djvu — regardless of A, B, or C.

**(c) Golden-anchor shortlist** — 5 files under
`/home/scanum/vbrdb/orchestrator/uploads/` (detail in §5): smallest
(`4139_from_s3.djvu`), typical (`._._2_32e52013.djvu`), richest text layer
(`._._-__d2c86e35.djvu`), large+text (`._-__18163057.djvu`), largest
(`4172_from_s3.djvu`).

**Corpus-size correction: the map's "~92 djvu" is stale. Ground truth is
33 files** (probed 2026-09-12; see §1). Every downstream estimate keyed to
"92" should be rescaled.

## 1. Corpus ground truth (read-only probes)

Method: throwaway probe script run from `/tmp` via `uv run python` (nothing
saved into the repo; uploads/ only read). Per file: byte size; `djvused -e n`
(page count) and `-e size`; text-layer volume via `djvused -e print-txt` /
`print-pure-txt` byte counts plus quality heuristics (share of lines >20%
alphanumeric, share of non-blank lines); chunk census via `djvudump` (SJBZ =
JB2 pages, BG44/BM44 = IW44 chunks); DPI from INFO chunks. **33/33 parsed, 0
failures** — the odd `._`-prefixed names decode as ordinary DjVu documents
(they are name-mangled real documents, not AppleDouble sidecars; an
AppleDouble file would not survive `djvused`).

Layout: 33 `.djvu` files, all at the top level of `uploads/` — 21 with
visible names + **12 `._`-prefixed dotfiles** (invisible to shell globs —
`ls *.djvu` undercounts at 21). The `covers/` and `thumbnails/` subdirectories
contain zero djvu.

Distributions (n=33):

| metric | min | p25 | median | p75 | p90 | max |
|---|---|---|---|---|---|---|
| pages | 25 | 131 | **209** | 235 | 335 | 552 |
| size (MB) | 0.34 | 3.25 | **7.86** | 12.28 | 15.33 | 21.69 |

- Total corpus: **282.6 MB**.
- Encoding: **33/33 files carry JB2 stencils** (SJBZ chunks); **31/33 also
  carry an IW44 background** (compound images: JB2 text/line-art stencil +
  IW44 photo background); 0 pure-IW44 photo files. IW44 backgrounds typically
  appear as **4 BG44 chunks per page = 4 progressive slices of one image**
  (verified on `4140_from_s3.djvu` via `djvudump`), not 4 separate pages.
- Page geometry: only **2/33 files have uniform page dimensions** — mixed
  page sizes within one document are the norm. Any djvu→PDF conversion must
  size pages per page, not per document.
- Scan resolution: DPI values seen across the corpus: **{72, 300, 600}**
  (mixed within some files).

Text layers — **3/33 (9.1%)**, all high quality:

| file | pages | size | print-txt (sexpr) | chars/page (pure) | alnum lines | non-blank |
|---|---|---|---|---|---|---|
| `._._-__d2c86e35.djvu` | 186 | 15.19 MB | 8,249,262 B | ~3,188 | 0.98 | 0.99 |
| `._-__18163057.djvu` | 209 | 20.46 MB | 5,368,614 B | ~1,302 | 0.98 | 0.99 |
| `._._-_-_1967_a9d40ca0.djvu` | 262 | 4.09 MB | 5,036,617 B | ~1,312 | 0.97 | 0.97 |

The three files total 39.7 MB ≈ **17.6% of corpus bytes**. The sexpr volumes
(5–8 MB per document) indicate word-level zone trees, not page-level blobs.
Sample (page 2 of `._._-__d2c86e35.djvu`, `djvused -u`):

```
(page 0 0 1549 2499
 (line 246 2265 1292 2339
  (word 246 2267 292 2331 "Г.")
  (word 330 2269 392 2333 "В.")
  (word 458 2265 1292 2339 "НИКОЛЬСКИЙ")) ...)
```

Probe gotcha discovered: **`djvused -e output-txt` emits a djvused *script*,
not text** — for a text-less document that script is literally
`select; remove-txt` (19 bytes; a literal string inside the djvused binary).
Using `output-txt` volume as a text-presence test false-positives. The
reliable tests are `print-txt` (emits the sentinel `(page 0 0 0 0 "")` for a
text-less page — upstream `command_print_txt`) or the byte volume of
`print-pure-txt`.

## 2. PyMuPDF capability check

- Primary source: PyMuPDF PyPI page, "Supported File Formats" section
  (<https://pypi.org/project/PyMuPDF/>, fetched 2026-09-12). The **Input**
  table lists: *PDF & derivatives: PDF, XPS, EPUB, CBZ, MOBI, FB2, SVG, TXT,
  MD*; *Images: PNG, JPEG, BMP, TIFF, GIF, and more*; *Microsoft Office
  (Pro)*; *Korean Office (Pro)*. **DjVu appears nowhere on the page**
  (zero occurrences of "djvu" in the page text).
- Local check: PyMuPDF **1.28.2** on Python 3.12 (`import fitz`), matching
  the PyPI latest. Empirical: `pymupdf.open('<corpus file>.djvu')` raises
  `FileDataError: Failed to open file '...' as type djvu.` — MuPDF
  recognizes the extension but ships no DjVu decoder.
- Consequence: there is no "inspect djvu via pymupdf" shortcut. DjVu must go
  through djvulibre tooling (ddjvu/djvused/python-djvulibre) or through
  conversion to PDF first.

## 3. python-djvulibre bindings

Metadata (PyPI JSON <https://pypi.org/pypi/python-djvulibre/json>, fetched
2026-09-12; homepage <https://jwilk.net/software/python-djvulibre>):

- Latest release **0.8.8, 2022-09-02** (~4 years old at research date).
  Release cadence: 0.8.4 2019-03-11 → 0.8.5 2019-09-23 → 0.8.6 2021-03-04 →
  0.8.7 2021-08-14 → 0.8.8 2022-09-02.
- Author Jakub Wilk; license **GPL-2**; classifier "Development Status ::
  4 - Beta"; `requires_python` **unset**.
- **sdist-only: zero wheels have ever been published** — every install is a
  source build requiring Cython at build time and system `libdjvulibre`
  (present here: `libdjvulibre-dev 3.5.28-2ubuntu0.24.04.2`).

Python 3.12 compatibility — empirically resolved:

- **Stock build fails.** The sdist pins `Cython>=0.20` with no upper bound
  (its `pyproject.toml`), so uv resolves Cython 3.x, and the Cython 3
  exception-propagation rules break the build:
  `djvu/sexpr.pyx:157:28: Cannot assign type 'int (cexpr_io_t *, const char *) except? -1' to 'int (*)(cexpr_io_t *, char *) noexcept'. Exception values are incompatible.`
  (The sdist also uses stdlib `distutils` in `setup.py` — removed in Python
  3.12, surviving only via the setuptools shim.)
- **Rescue build succeeds:** in a scratch venv with `cython<3` (0.29.x) +
  `setuptools`/`wheel` and `--no-build-isolation`, the package **builds and
  imports on Python 3.12** (`import djvu.decode`, `djvu.sexpr` OK). So 3.12
  support exists but demands a build-time Cython pin — a vendored wheel or a
  pinned build recipe would be needed for clean deploys.

API surface (verified from the sdist `djvu/decode.pyx` + a live smoke test
against `._._-__d2c86e35.djvu`):

- Open: `djvu.decode.Context()` → `ctx.new_document(djvu.decode.FileURI(path))`
  → `Document`; `doc.decoding_job.wait()`. Pages: `doc.pages[i]` → `Page`
  with `.width`/`.height`.
- **Hidden text layer:** `djvu.decode.PageText(page).sexpr` → a
  `ListExpression` zone tree identical in shape to `djvused print-txt`
  (`(page x y X Y (line ... (word ... "текст")))`). Smoke test: page 2 of the
  sample file returned Cyrillic word zones.
- **In-memory rendering:** `job = page.decode(wait=True)` → `PageJob`;
  `job.render(mode, page_rect, render_rect, pixel_format, row_alignment=1,
  buffer=None)` → `bytes`. Rects are plain `(x, y, w, h)` tuples; **scale =
  page_rect size ÷ intrinsic page size** (`(0,0,w,h)` = 1x, `(0,0,2w,2h)` =
  2x); `render_rect` must lie inside `page_rect`. Modes: `RENDER_COLOR`,
  `RENDER_BLACK`, `RENDER_COLOR_ONLY`, `RENDER_MASK_ONLY`,
  `RENDER_BACKGROUND`, `RENDER_FOREGROUND`. Pixel formats:
  `PixelFormatRgb(byte_order='RGB'|'BGR')`, grey/palette/packed-bits
  variants; an optional caller `buffer` allows zero-copy into numpy/PIL.
- Smoke-test byte counts: 1x render of a 1668×2106 page = **10,538,424 B**
  (= 1668×2106×3, exact); 2x = **42,153,696 B** (exact). Rendering is
  deterministic and allocation-exact.
- Extras relevant to strategy C: `AffineTransform` (coordinate mapping
  between page and rendered space), annotations/outline/metadata APIs, and
  an async message model (progress streaming).

Assessment: technically the most complete option (render + text + metadata
from one dependency), but unmaintained since 2022, GPL-2 (license fit must be
checked against ADR-0004), sdist-only with a broken stock build — the
`ddjvu` CLI covers the same ground with zero Python-side dependencies.

## 4. ddjvu / djvused CLI envelope

Local toolchain (Ubuntu 24.04): **djvulibre-bin 3.5.28-2ubuntu0.24.04.2** —
`/usr/bin/{ddjvu,djvused,djvudump,djvutxt}`, plus `libdjvulibre21` and
`libdjvulibre-dev` (so a python-djvulibre source build can link).
`pdf2djvu 0.9.18.2` is also installed (opposite direction; irrelevant for
ingest). Sources: local man pages (`djvused(1)`, `ddjvu(1)`, `djvutxt(1)`)
and upstream master (`tools/ddjvu.cpp`, `tools/djvused.cpp` —
<https://sourceforge.net/p/djvu/djvulibre-git/ci/master/tree/tools/ddjvu.cpp?format=raw>,
`.../djvused.cpp?format=raw`); online man mirrors at
<https://djvu.sourceforge.net/doc/man/ddjvu.html> and
`.../djvused.html`.

### ddjvu flags that matter

- `-format=FMT`: `pbm`, `pgm`, `ppm`, `pnm`, `rle`, `tiff`, **`pdf`**.
  (The local 3.5.28 `--help` text omits `pdf` — stale help string; the
  option parser accepts it, the man page documents it, and upstream master's
  usage lists it.)
- `-mode=color|black|mask|foreground|background` — maps onto the
  `DDJVU_RENDER_*` modes; bitonal output for `black`/`mask`.
- `-quality=Q`: integer **25–150** (JPEG quality for lossy output) or
  `deflate`/`lzw`/`uncompressed`. For PDF output the default is
  **DEFLATE** ("All pdf engines understand deflate" — upstream comment);
  bitonal pages become 1-bit CCITT G4 when available; explicit
  `-quality=25..100` switches color pages to JPEG.
- Scale control: `-scale=N` (1..999, `%` allowed) — the man page: "The
  magnification factor mag can also be interpreted as the resolution of the
  output image expressed in dot per inch." Alternatives: `-size=WxH`,
  `-subsample=N` (mutually exclusive). **There is no `-dpi` flag and no
  `-subtype` flag** (ticket premise corrected); with `-format` given and no
  scale option, pages render at full native size.
- Page selection: `-page=PAGESPEC` (ranges, reverse ranges, comma lists —
  e.g. `1-10`, `3`, `1,3,99999-4`). **Default pagespec is page 1 only,
  unless `-format` is given, in which case it is `1-$` (all pages)** —
  upstream `main()`: `flag_pagespec = (flag_format) ? "1-$" : "1"`.
  `-eachpage` writes one file per page via `%d` in the output filename;
  `-skip` continues past corrupted pages; `-segment=WxH+X+Y` rasters a
  sub-rectangle; `-verbose` prints per-page decode/render timings.
- Per-page rasterization: `-format=ppm` (P6) / `-format=tiff` with
  `-eachpage` (or repeated `-page=N` runs) is the subprocess-friendly path
  for strategy C.

### The PDF text-layer question — answered: NO

Upstream `tools/ddjvu.cpp` (master, fetched 2026-09-12): `-format=pdf`
(`flag_format == 'f'`) renders each page's pixels with `ddjvu_page_render()`,
writes them into a **temporary TIFF** (`openfile()`: "temporary tiff, later
converted to pdf", created via `mkstemp`), and on completion converts it via
the embedded **`tiff2pdf()`** (`closefile()`: "Converting temporary TIFF to
PDF."; the file includes `tiff2pdf.h`; the local man page likewise credits
"code derived from program tiff2pdf"). **No code path in ddjvu touches the
hidden text layer** — no `TXTa`/`TXTz` handling, no text API call anywhere
in the tool. The man page agrees: "Format pdf produces a Portable Document
Format (PDF) file. Each page in the resulting file is represented by an
image at the specified resolution."

Empirical confirmation: `ddjvu -format=pdf -page=1-2 -subsample=2
-quality=88 '._._-__d2c86e35.djvu' /tmp/djvu_text_test.pdf` (a file whose
pages carry word-level text layers), inspected with PyMuPDF 1.28.2:
`get_text()` = **0 chars on both pages**, exactly **one image XObject per
page**, PDF producer metadata **`libtiff / tiff2pdf`**; `pdftotext` likewise
extracts 0 bytes.

### djvused text commands (and the ticket's premise correction)

The real command set (local man page + upstream dispatch table,
`tools/djvused.cpp` master: `print-txt`, `print-pure-txt`, `output-txt`,
`set-txt`, `remove-txt`): **`output-escaped-txt` and `dump-txt` do not
exist** — not in 3.5.28, not in master (absent from both the man page and
the source command map). The ticket's premise names are corrected to:

- `print-txt` — the structural dump: one S-expression per selected page,
  `(page xmin ymin xmax ymax (column ...) (region ...) (para ...) (line ...)
  (word ... "text") (char ...))`, zone hierarchy
  page > column > region > paragraph > line > word > character.
  **Coordinates are page-anchored pixels with the origin at the bottom-left
  corner of the page** (man page: "Coordinates are measured in pixels and
  have their origin at the bottom left corner of the page") — PDF/PyMuPDF
  text coordinates are top-left, so a y-flip (`y' = page_height − y`) is
  required if these zones are ever injected into a PDF text layer. Strings
  are C-escaped (non-ASCII as octal, e.g. `"\320\223"`) unless `-u` is
  passed (verified locally: `-u` emits raw UTF-8).
- `print-pure-txt` — plain UTF-8 text with structural control characters:
  `\f` between pages, `\n` between lines, `\013` (vertical tab) between
  columns, `\035` (group separator) between regions, `\037` (unit
  separator) between paragraphs (man page). Cheapest bulk-text extraction.
- `output-txt` — **not a text dump**: it prints a djvused *script* meant for
  re-ingest (`djvused -f`): per page `# ----` + `select "<id>" # page N` +
  `set-txt` + the sexpr + `.`; with no page selected it prepends
  `select; remove-txt` — the 19-byte gotcha noted in §1.
- Empty-layer sentinel: `print-txt` emits `(page 0 0 0 0 "")` for a page
  without text (upstream `command_print_txt`).

`djvutxt` (installed at `/usr/bin/djvutxt`, same 3.5.28 package) is the
user-friendly wrapper: no options → UTF-8 text (≈ `print-pure-txt`);
`-detail` → S-expressions with locations (≈ `print-txt`); `-escape` →
C-style escapes; `--page=pagespec` for page selection (man
<https://djvu.sourceforge.net/doc/man/djvutxt.html>). For the router,
`djvutxt --page=N` / `djvutxt -detail` is the cleanest single-command text
extractor.

## 5. Golden-anchor shortlist (for TKT-DJV-08)

All paths under `/home/scanum/vbrdb/orchestrator/uploads/`:

| # | file | pages | size | text layer | role |
|---|---|---|---|---|---|
| 1 | `4139_from_s3.djvu` | 25 | 0.34 MB | no | smallest — fast smoke anchor (used for the ddjvu→PDF text test) |
| 2 | `._._2_32e52013.djvu` | 129 | 7.86 MB | no | typical — exactly the corpus median size, 300 dpi |
| 3 | `._._-__d2c86e35.djvu` | 186 | 15.19 MB | **yes** (8.25 MB sexpr, ~3,188 chars/page, 98% alnum) | richest text layer — the text-path golden anchor |
| 4 | `._-__18163057.djvu` | 209 | 20.46 MB | **yes** (5.37 MB sexpr) | large + text; median page count |
| 5 | `4172_from_s3.djvu` | 247 | 21.69 MB | no | largest — stress anchor |

Spare/secondary: `._._-_-_1967_a9d40ca0.djvu` (262 pp, 4.09 MB, **with**
text — third text-layer file); `4140_from_s3.djvu` (29 pp, 1.93 MB, no
text) is the cleanest IW44-slice exemplar (4 BG44 progressive slices per
page) for chunk-structure debugging.

## Appendix: evidence trail

- **Local probes (2026-09-12):** djvulibre-bin 3.5.28-2ubuntu0.24.04.2
  (`djvused`, `djvudump`, `ddjvu`, `djvutxt`); throwaway probe script + JSONL
  results in `/tmp` (ephemeral; nothing written into the repo); 33/33 files
  parsed, 0 failures. ddjvu→PDF test artifact `/tmp/djvu_text_test.pdf`
  inspected with PyMuPDF 1.28.2 (ephemeral `uv run --no-project --with pymupdf`).
- **PyPI:** <https://pypi.org/project/PyMuPDF/> (Supported File Formats
  table; fetched 2026-09-12); <https://pypi.org/pypi/python-djvulibre/json>
  (release dates, sdist-only history, license, `requires_python`).
- **Upstream djvulibre-git (master):**
  <https://sourceforge.net/p/djvu/djvulibre-git/ci/master/tree/tools/ddjvu.cpp?format=raw>
  (PDF = temp TIFF + `tiff2pdf()`; option parser; default pagespec),
  <https://sourceforge.net/p/djvu/djvulibre-git/ci/master/tree/tools/djvused.cpp?format=raw>
  (text-command dispatch table; `output-txt` script emission; empty-page
  sentinel).
- **Man pages:** local `djvused(1)`, `ddjvu(1)`, `djvutxt(1)` (3.5.28);
  online mirrors <https://djvu.sourceforge.net/doc/man/ddjvu.html>,
  `.../djvused.html`, `.../djvutxt.html`.
- **python-djvulibre sdist** (0.8.8, downloaded and inspected):
  `pyproject.toml` (`Cython>=0.20`, no upper bound), `setup.py` (distutils),
  `djvu/decode.pyx` / `djvu/sexpr.pyx` (API surface; the Cython-3 breakage
  site at `sexpr.pyx:157`). Rescue build + smoke test in `/tmp` venvs
  (Python 3.12 + `cython<3`).
- **Reference pipeline:** `~/vbrdb/src/preprocessing/djvu_to_pdf.py`
  (read-only) — `ddjvu -format=pdf -quality=88` subprocess → PyMuPDF+pyvips
  recompression (bilevel PNG for RGB-text pages, JPEG for color, DPI
  downsampling) — the existence proof for strategy A.
- Constraint compliance: read-only on `uploads/`; all experiments in
  ephemeral environments (`uv run --no-project`, `/tmp` venvs);
  `pyproject.toml`/`uv.lock` untouched; only
  `docs/wayfinder/research/djvu-tools.md` and the TKT-DJV-01 ticket's
  Resolution written.

## TKT-DJV-12 — ddjvu per-page render baseline (measured 2026-09-12)

Follow-up measurement validating the locked strategy-C mechanism (TKT-DJV-02:
per-page `ddjvu` subprocess rendering) against the pipeline's parallel page
model (ThreadPoolExecutor, `workers` default 2 — `config.py:85`,
`core.py:99-101`). Method: throwaway probes (`/tmp/djvu_probe_tkt_djv_12.py`,
`/tmp/djvu_mem_probe.py`, ephemeral, never saved into the repo) run via
`uv run python` from the repo root (numpy 2.5.3 from the project env);
read-only on `uploads/`. `time.perf_counter` around `subprocess.run` +
PPM→numpy conversion; 5 repetitions per (file, page); P6 header parsed,
payload mapped via `np.frombuffer(...).reshape(h, w, 3)` (zero-copy view)
plus a writable `np.array` copy, as the OCR path would consume it. Pages
probed: 1 / N÷2 / N per anchor.

Anchors — the shortlist's small/typical/large plus two extras the question
demanded (a 500+-page file for the re-decode penalty check; a 72-dpi page for
the upscale case):

| role | file | pages | size | INFO dpi |
|---|---|---|---|---|
| small | `4139_from_s3.djvu` | 25 | 0.34 MB | 600 |
| typical | `._._2_32e52013.djvu` | 129 | 7.86 MB | 300 |
| large | `4172_from_s3.djvu` | 247 | 21.69 MB | 600 |
| huge (penalty check) | `._._-__451b9719.djvu` | 552 | 11.67 MB | 600 |
| 72-dpi check | `-_3_b7754266.djvu` p222 | 231 | 12.28 MB | mixed: 221 pg @300 + 10 pg @72 |

### Scale calibration — the mapping

**`-scale=N` (unitless) = render at N dpi against each page's stored INFO
dpi.** Cross-checked against `-size=WxH` targets (native × 150/dpi): agreement
within ±2 px on all 12 calibration rows (ddjvu floors .5 fractions:
4006/4 = 1001.5 → 1000, not 1002):

| stored dpi | native → `-scale=150` output | factor |
|---|---|---|
| 600 | 3351×4641 → 837×1159 · 3376×5332 → 844×1333 · 4357×6712 → 1089×1677 | ÷4 |
| 300 | 3600×2670 → 1800×1335 · 1919×2860 → 959×1429 | ÷2 |
| 72 | 3590×2710 → 7478×5645 | ×2.083 (upscale) |

Two gotchas the implementation must know:

1. **`-scale=N%` (percent form) is NOT percent-of-native** — it is N% of the
   100-dpi render: `-scale=25%` on a 600-dpi page gives 139×192 (a 25-dpi
   image, 36× area loss). The unitless form is the correct knob.
2. **Mixed-DPI files are real** (10/231 pages of `-_3_b7754266.djvu` are
   72 dpi while the rest are 300): scale must be chosen per page, and 72-dpi
   pages must be capped at native — literal 150-dpi upscaling of a 72-dpi
   page costs 126.6 MB ppm / +248 MB RSS / 1.03 s vs 29.2 MB / +57 MB /
   0.24 s at native (`-scale=72`), with zero information gain. Rule:
   **`-scale=min(150, stored_dpi)`**.

### Per-page wall time at 150 dpi (`-scale=150`, 5 reps, ms)

total = spawn + decode + ppm→numpy (+ writable copy). Conversion-only
(`frombuffer`+`reshape`) is 0.04–0.05 ms (zero-copy); the writable copy adds
0.3–1.7 ms. Fixed floor: bare `ddjvu` spawn (usage-exit loop, n=10) =
**53 ms** — decode+transfer is the remaining ~20–300 ms, tracking page
complexity. Even full-native renders (46.7–87.7 MB ppm) stay ≤1.1 s
(0.18–1.09 s).

| anchor | pages probed | dims @150 dpi | ppm | spawn+decode min/med | total min/med |
|---|---|---|---|---|---|
| small | 1 · 12 · 25 | 837×1159 | 2.91 MB | 153/170 · 121/124 · 130/136 | 154/170 · 122/124 · 130/137 |
| typical | 1 · 64 · 129 | 959×1429 · 1800×1335 | 4.11 / 7.21 MB | 314/318 · 146/148 · 95/105 | 315/319 · 147/149 · 96/106 |
| large | 1 · 123 · 247 | 659×980 · 709×1000 · 658×1066 | 1.94–2.13 MB | 72/72 · 185/187 · 148/153 | 72/73 · 186/189 · 149/155 |
| huge | 1 · 276 · 552 | 1089×1677 · 844×1333 | 5.48 / 3.38 MB | 325/353 · 227/235 · 189/198 | 326/354 · 227/236 · 190/199 |

Per-anchor median-of-medians (total): **small 137 ms, typical 149 ms, large
155 ms, huge 236 ms; pooled median 162 ms**.

### Memory

Fresh-process peak RSS for ONE conversion (numpy-import baseline 26.7 MB):
**peak 37.1–40.7 MB = +10.4–14.0 MB over baseline** for payloads of
2.91–7.21 MB (stdout capture + zero-copy view + writable copy ≈ 2× payload).
At workers=2 the concurrent raster footprint is ≤ ~30 MB — in-memory bytes
beat temp files trivially (feeds TKT-DJV-05). The only blow-up risk is the
72-dpi upscale case above, killed by the `min(150, dpi)` cap.

### Re-decode penalty — none

1. **File size does not predict cost**: the largest file (4172, 21.69 MB)
   renders the fastest pages (72–189 ms); the smallest (4139, 0.34 MB) sits
   in the same band (122–170 ms). Whole-file decode would scale ~64×.
2. **No position dependence**: within the 552-page file, p1/p276/p552 =
   354/236/199 ms — flat-to-decreasing with page index.
3. **Complexity, not size**: small p12 (3351×4641 → 837×1159) 124 ms vs huge
   p276 (3376×5332 → 844×1333) 235 ms = 1.9× for 1.15× output pixels — a
   page-content effect across a 34× file-size gap.

Consistent with the bundled DIRM design: ddjvu seeks each page's chunks via
the directory index; a spawn pays only the ~53 ms process floor plus that
page's decode.

### End-to-end estimate (median 209-page book, workers=2)

- render wall = pooled median 162 ms × 209 ÷ 2 ≈ **17 s** (conservative:
  huge-anchor 236 ms → 25 s; absolute worst, every page like the slowest
  measured 354 ms → 37 s);
- OCR wall = 24–40 s/page × 209 ÷ 2 = **2 508–4 180 s (42–70 min)**
  (dpi=150 default, `ocr.py:174-175`);
- ratio: render = **0.4–0.7% of OCR wall** (≤1.5% at absolute worst) — noise
  next to the OCR budget.

**VERDICT: (a) per-page on demand is fine.** At 150 dpi every page renders in
0.07–0.35 s against a 24–40 s OCR budget; memory is ~14 MB transient per
page; there is no re-decode penalty to amortize. Chunked (`-eachpage`) or
whole-file modes would add disk I/O and up-front rendering for pages that may
never be OCR'd, with no measured benefit. Carry two implementation rules into
TKT-DJV-05 / TKT-DJV-10: unitless `-scale` only (never the `%` form), and
`-scale=min(150, stored_dpi)` per page (mixed-DPI files exist; 72-dpi pages
must not be upscaled).
