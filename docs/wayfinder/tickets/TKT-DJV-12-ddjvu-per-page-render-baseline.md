# TKT-DJV-12 — Research: ddjvu per-page render cost baseline

## Question

Is per-page `ddjvu -page=N` subprocess rendering acceptable for the
pipeline's parallel page model (ThreadPoolExecutor, `workers` default 2)?

Measure on 3 golden anchors (small / typical / large — shortlist from
TKT-DJV-01 research):

- wall time per page: subprocess spawn + decode + ppm→array conversion,
  at OCR-relevant resolution (~150 dpi equivalent via `-scale`/`-size` —
  the corpus DPI ∈ {72, 300, 600}, so scaling math matters);
- memory profile of the raster at 150 dpi for a typical scan page;
- whether bundled multipage djvu pays a re-decode penalty per spawn
  (pages are independent DIRM chunks — expected cheap, verify empirically);
- end-to-end estimate: a median 209-page book at workers=2 — total render
  wall time, for comparison against the known OCR cost (~24–40 s/page
  CPU-only, `ocr.py` notes).

Output: numbers + verdict — **per-page on demand is fine** / **needs
chunked rendering** (`-eachpage` into a temp dir) / **needs whole-file
mode** — feeding TKT-DJV-05 (in-memory vs temp-file lifecycle) and
TKT-DJV-10 (implementation).

Method: throwaway probe scripts from /tmp (never saved to the repo);
read-only on the corpus; findings appended to
[`../research/djvu-tools.md`](../research/djvu-tools.md) as a new
section, with the verdict also recorded here.

Blocked by: — (anchors exist; TKT-DJV-01 closed).

## Resolution

_Closed 2026-09-12 by the research subagent; full measured baseline:
[`../research/djvu-tools.md`](../research/djvu-tools.md#tkt-djv-12--ddjvu-per-page-render-baseline-measured-2026-09-12)._

**Verdict: (a) per-page on demand is fine** — per-page `ddjvu -page=N`
subprocess rendering is acceptable for the pipeline's parallel page model;
neither chunked rendering (`-eachpage` into a temp dir) nor whole-file mode
is justified by the numbers.

Evidence:

1. **Per-page wall time at 150 dpi** (`-scale=150`, spawn+decode+PPM→numpy,
   5 reps, pages 1/N÷2/N of each anchor): totals 72–354 ms; per-anchor
   median-of-medians small 137 / typical 149 / large 155 / huge(552 pp)
   236 ms; pooled median **162 ms** — vs the 24–40 s/page OCR budget
   (`ocr.py:174-175`), render is 0.4–0.7% of per-page cost. PPM→numpy
   conversion is zero-copy (0.04–0.05 ms); the fixed floor is the ~53 ms
   bare `ddjvu` spawn.
2. **Scale mapping**: unitless `-scale=N` = N dpi output against each page's
   stored INFO dpi (600→÷4, 300→÷2, 72→×2.08), verified against `-size=WxH`
   within ±2 px. Gotchas: `-scale=N%` is % of the 100-dpi render, NOT of
   native (`-scale=25%` on 600 dpi → 139×192); mixed-DPI files exist
   (10/231 pages of `-_3_b7754266.djvu` are 72 dpi) → implementation rule
   `-scale=min(150, stored_dpi)` (literal 150 dpi on a 72-dpi page: 126.6 MB
   ppm / +248 MB RSS / 1.03 s vs 29.2 MB / +57 MB / 0.24 s at native).
3. **Memory**: fresh-process peak RSS for one conversion = numpy baseline
   26.7 MB + 10.4–14.0 MB (payload 2.9–7.2 MB ≈ 2× transient) → in-memory
   bytes lifecycle is fine at workers=2 (≤ ~30 MB concurrent); no temp files
   needed (feeds TKT-DJV-05).
4. **No re-decode penalty**: the largest file (21.69 MB) renders the fastest
   pages (72–189 ms); the 552-page file is flat/decreasing across
   p1/p276/p552 (354/236/199 ms); a 34× file-size gap → 1.9× time (page
   complexity, not document size — independent DIRM page chunks, as
   expected).
5. **End-to-end** (median 209-page book, workers=2): render ≈ 209 × 0.162 s
   ÷ 2 ≈ **17 s** (conservative 25 s, absolute worst 37 s) vs OCR
   2 508–4 180 s (42–70 min) — 0.4–0.7% of OCR wall (≤1.5% worst).

Consequences: TKT-DJV-05 decides in-memory bytes (this baseline supports
it); TKT-DJV-10 implements with the two scale rules above (unitless
`-scale`, `min(150, stored_dpi)` per page).
