# TKT-DJV-02 — Integration strategy: upstream conversion vs in-router conversion vs native render

## Question

Where does djvu become something the pipeline consumes? Three strategies
(the user explicitly kept this open on 2026-09-12 — it is decided here, on
evidence from TKT-DJV-01, not by pre-analysis):

- **A. Upstream conversion** — the caller converts djvu→PDF first (e.g. via
  the vbrdb pipeline); the router never learns djvu beyond today's typed
  error.
- **B. In-router conversion** — a djvu inspector registered in
  `_INSPECTORS` shells out to `ddjvu` into a temporary PDF and re-dispatches
  to the existing pdf inspector; everything downstream (OCR `(pdf_path,
  page)`, pymupdf image extraction) works untouched.
- **C. Native render + PageSource seam** — a djvu inspector renders pages
  (ddjvu per-page or python-djvulibre) and harvests the hidden text layer
  (djvused); the downstream passes generalize from "PDF-shaped" to "page
  source" (architecture-report candidate 4).

Decision inputs: corpus text-layer prevalence (free native text vs forced
OCR — TKT-DJV-01); the router's embed promise ("hand the file to the
facade, it figures out what it is"); the minimal-resources ethos; the fact
that ADR-0003's anti-binary ruling was docx-specific and does NOT pre-rule
ddjvu here; the cost of the PageSource seam if C.

Output: the strategy ruling, recorded in this ticket and feeding TKT-DJV-09
(ADR-0004). The sub-decision tickets (03–08) take their final shape from
this ruling.

Blocked by: TKT-DJV-01.

## Resolution

_Closed 2026-09-12 (grilling; the user's lean confirmed against TKT-DJV-01
evidence)._ 

**Strategy C wins: native render + PageSource seam, pages rendered via the
`ddjvu` subprocess — no new Python dependencies.**

The user leaned C ("склоняюсь к стратегии C") with the research facts on the
table; the grilling stress-tested them and C held:

- the corpus profile makes B's whole-file temp PDF awkward (median 209
  pages / 7.9 MB, max 21.7 MB — full conversion up front even for
  `--info`, disk spikes), while C renders per page into the existing
  ThreadPoolExecutor model;
- ddjvu's PDF output is image-only (TKT-DJV-01) — B would lose the hidden
  text layers that C can harvest (3/33 files — bounded, but real);
- C's text-layer advantage is a bonus, not the driver: 9.1% prevalence
  means OCR stays the main path for ~91% of pages under every strategy.

Locked sub-decision (grilling Q2): **rendering mechanism = `ddjvu`
subprocess** (`-page=PAGESPEC`, `-scale`/`-size`, `-format=ppm` → stdout;
flags verified against `ddjvu --help`), NOT python-djvulibre. The bindings
are sdist-only (no wheels ever), stock build breaks on Python 3.12
(`cython<3` pin + `--no-build-isolation` required), GPL-2, dormant since
0.8.8 (2022) — build fragility in the deploy path contradicts
deploy-anywhere. External-binary ruling for djvu: `djvulibre-bin` is
admitted (the docx anti-binary ruling was docx-specific, LibreOffice-scale);
`djvutxt` (same package, present at /usr/bin) is the text-layer extractor.

Seam shape direction (feeds the graduated TKT-DJV-11): the PDF-shaped
constraint is the *raster-acquisition point* inside the OCR engines —
pymupdf in `_ocr_rapidocr` (ocr.py:193–204), `rasterize_page` in
tesseract/deepseek (ocr.py:313, 431), single-page pymupdf in glm
(ocr.py:367–375). One format-aware raster step (pdf → pymupdf,
djvu → ddjvu) lets the engines keep their signatures;
`pdf_inspector_ocr` stays PDF-only with a typed error. PDF code paths stay
untouched — regression risk contained by golden anchors.

Rejected alternatives:

1. **A. Upstream conversion** — breaks the router's embed promise ("hand
   the file to the facade, it figures out what it is"); every embedding
   app reimplements conversion; the router's djvu detection stays a dead
   end.
2. **B. In-router conversion (ddjvu → temp PDF)** — whole-file temp PDF
   for median 209-page books (no laziness, `--info` pays full conversion);
   loses text layers (image-only output); double lossy encoding (ddjvu
   q-encode → pymupdf re-raster).
3. **python-djvulibre as the C renderer** — see Q2 above.

Consequences for the sub-tickets: TKT-DJV-05 becomes "djvu raster
lifecycle (in-memory bytes vs temp files)", decided with TKT-DJV-12
measurements; TKT-DJV-06 decides whole-page-raster sidecar semantics;
TKT-DJV-11 (the seam) is a named ticket blocking implementation.
