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

_(open)_
