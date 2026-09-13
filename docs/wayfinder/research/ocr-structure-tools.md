# TKT-ST-01 research — local table-structure & heading recovery from OCR page rasters

Research subagent findings, 2026-09-13. All facts verified against primary
sources: GitHub READMEs (RapidAI/TableStructureRec, RapidAI/RapidTable,
opendatalab/DocLayout-YOLO, RapidAI/RapidLayout), PyPI JSON metadata
(`https://pypi.org/pypi/<pkg>/json`), GitHub REST API (repo metadata, issue
search), HF model-repo API (table-transformer weights), ModelScope file API +
direct downloads (ONNX weight sizes), local wheel introspection (downloaded
and unpacked wired-table-rec 1.2.0 / lineless-table-rec 0.1.0), and read-only
introspection of the repo's installed rapidocr 3.9.2 (`uv run python`).
No repo code changed; nothing installed; nothing committed.

## Verdict

**(a) The user's first instinct survives scrutiny — RapidAI's TableStructureRec
family is the only stack that meets every constraint at once** (onnxruntime
inference, Apache-2.0, tiny pure-python wheels, Python ≥ 3.12 compatible,
Russian text handled by OCR text, not the structure stage). Concretely it is
three small packages + the already-installed rapidocr:

| package | role | wheel | CPU inference (vendor) | key weights (ONNX) |
|---|---|---|---|---|
| `wired_table_rec` 1.2.0 | ruled tables | 0.04 MB pure-py | ~100 ms | unet.onnx **8.3 MB** (default), cycle_center_net.onnx 72 MB |
| `lineless_table_rec` 0.1.0 | borderless tables | 0.03 MB pure-py | ~500 ms | lore/detect.onnx 44.3 MB + lore/process.onnx 44.2 MB = **88.5 MB** |
| `table_cls` 1.2.0 | wired-vs-lineless routing | 0.01 MB pure-py | ~70–100 ms | small YOLO cls |
| `rapid_table` 3.0.2 (sibling) | seq2seq HTML tables | 0.04 MB pure-py | ~150 ms | slanet-plus.onnx **6.8 MB**, ships inside the wheel |

**(b) The composition contract with our existing rapidocr 3.9.2 is exactly the
seam the map hoped for**: the table engines accept an external
`ocr_result=[(box, text, score), ...]` (or single-word tuples from
`return_word_box=True`) — so the structure stage reuses the OCR run the router
already paid for (24–40 s/page CPU today); the table models themselves add
only ~0.1–0.5 s/page on CPU.

**(c) Two real frictions, both bounded**:

1. **RapidOCR version-window mismatch (latent, not on our path).** The
   TableStructureRec README compat table lists wired/lineless 1.2.x ↔
   `rapidocr >1.0.0,<3.0.0` while the repo pins
   `rapidocr[onnxruntime]>=3.9.2` (`pyproject.toml:11`). Wheel evidence
   (METADATA + source, 1.2.0): the package declares **no rapidocr
   dependency at all** (numpy, onnxruntime≥1.14, opencv-python, scipy,
   scikit-image, Shapely, requests, tqdm), and rapidocr is imported only in
   the CLI demo `main()` — never on the library path. With our `ocr_result`
   passed in, the engine runs `match_ocr_cell` + `fill_blank_rec` (empty
   cells padded with `[[box, "", 1]]`, no re-OCR), so the compat window is
   advisory for us. Wire it, but pin exact versions and smoke-test the pair.
2. **Version-pin risk.** wired_table_rec's default `model_type` has churned
   around releases (today README and wheel agree: `unet`; the 2025-03-31
   release notes carry a "Fix wired unet model" entry) — pin exact versions
   and the model file.

**(d) Heading cues stay heuristic-first.** No cheap local layout model beats
font-size-from-OCR-boxes on the resource axis (RapidLayout 1.2.1 is onnx and
deps-clean but has no pip-installable title model listed; DocLayout-YOLO is
AGPL-3.0 + torch + ultralytics-style deps — disqualified; glm_ocr already
proves `vision_title` detection quality but is cloud opt-in). Box-height
statistics from `RapidOCROutput.boxes` — which v1 currently discards
(`ocr.py:210-213`) — are the cheap default; glm opt-in for the persona where
title fidelity pays.

**(e) The fallback ladder ends in pure CV**: OpenCV line-projection (0 new
deps, ~0 cost) recovers ruled-table grids when the wired model misfires; it
cannot see borderless tables or merged cells. SLANet-plus (6.8 MB, in-wheel)
covers borderless structurally via seq2seq HTML without a detector at all.

## 1. RapidAI TableStructureRec (the candidate)

Repo: <https://github.com/RapidAI/TableStructureRec> (Apache-2.0 badge +
LICENSE commit link in README; GitHub API: `license: Apache-2.0`,
962 stars, not archived). Not a single package on PyPI — an umbrella for
three inference libs. All PyPI facts from PyPI JSON, fetched 2026-09-13.

### Packages, licenses, wheels, deps (PyPI JSON)

| package | version | license | `requires_python` | wheel | deps |
|---|---|---|---|---|README badge says|
|---|---|---|---|---|---|
| `wired-table-rec` | 1.2.0 (2025-03-31) | Apache-2.0 | `>=3.6,<3.13` | 0.04 MB py3-none-any | numpy, onnxruntime≥1.14, opencv, scipy, scikit-image, Shapely, requests, tqdm |
| `lineless-table-rec` | 0.1.0 (2025-03-31) | Apache-2.0 | `>=3.6,<3.13` | 0.03 MB py3-none-any | same five heavy-ish |
| `table_cls` | 1.2.0 (2025-03-31) | Apache-2.0 | `>=3.6,<3.13` | 0.01 MB py3-none-any | same |
| `rapid-table` | 3.0.2 (2026-04-23) | Apache-2.0 | `>=3.6,<4` | 0.04 MB py3-none-any | onnxruntime>1.17, opencv, numpy, Pillow, requests, colorlog, omegaconf; torch extras opt-in |

Python 3.12: **compatible** — every wheel is `py3-none-any`, upper bounds are
`<3.13`/`<4` (3.12 installs; 3.13 would be blocked for the three
TableStructureRec packages, irrelevant here). The shared `scipy +
scikit-image` pair is the only new heavy-ish Python deps (both CPU-only,
both common transitive citizens); `onnxruntime` the repo already carries
(`pyproject.toml:8`).

### ONNX weights + measured sizes (ModelScope, downloaded 2026-09-13)

`wired_table_rec` and `lineless_table_rec` auto-download from
`https://www.modelscope.cn/models/RapidAI/RapidTable/resolve/master/`
(URLs extracted from the shipped wheel sources). Measured by actual
download:

| model | bytes | role |
|---|---|---|
| `unet.onnx` | **8,335,007** (8.3 MB) | wired default (`model_type="unet"`) |
| `cycle_center_net.onnx` | 72,182,085 (72 MB) | wired alternative |
| `lore/detect.onnx` | 44,331,958 (44.3 MB) | lineless detect |
| `lore/process.onnx` | 44,239,345 (44.2 MB) | lineless process |
| `slanet-plus.onnx` (rapid-table) | 6.8 MB (vendor table) | seq2seq HTML; **bundled in the rapid-table wheel** |

CPU inference per image (vendor, test env py3.10): table models alone are
fast — wired "~100 ms level", lineless "~500 ms level" (EN README; the CN
README says 1–7 s/image total on CPU including OCR) — "the main time
consumption is in the OCR stage".

### Python API shape (read from the shipped wheel sources, v1.2.0 / 0.1.0)

Input is the **page raster** (str/np.ndarray/bytes/Path/PIL — i.e. the
raster we already have from the raster seam) **plus an optional external
`ocr_result`**:

```python
wired = WiredTableRecognition(WiredTableInput(model_type="unet"))
out = wired(
    img,                 # raster
    ocr_result,          # [(box4pt, text, score), ...]  ← from our rapidocr run
    # need_ocr defaults to True → cell text comes from OUR ocr_result
    #   (match_ocr_cell + fill_blank_rec pads unmatched cells, never re-OCRs);
    # need_ocr=False → geometry only: pred_html="", cell_bboxes + logic_points
    col_threshold=15, row_threshold=10,   # same-x / same-y clustering
    # wired v2 extras: rotated_fix=True (±45°), enhance_box_line=True
)
# → WiredTableOutput(pred_html: str, cell_bboxes: np.ndarray (N,8),
#                    logic_points: np.ndarray (N,4), elapse: float)
```

Output contract, all three channels at once:

- `pred_html` — full `<table><tr><td>` HTML (flat cells + text; no
  rowspan/colspan attributes);
- `cell_bboxes` — 8-float cell polygons in **raster pixel space**, joinable
  back to our OCR line boxes by geometry;
- `logic_points` — per cell `[row_start, row_end, col_start, col_end]`
  (zero-based, inclusive spans) — **directly the `TableBlock.rows` shape**
  the IR wants, no HTML parsing needed.

The same dataclasses (`WiredTableInput/Output`, `LinelessTableInput/Output`)
and the same `ocr_result` shape are documented as shared with RapidTable
("input and output format are same with RapidTable" — README warning), so
both engines are hot-swappable behind one adapter.

### Maintenance status

Releases: `wired_v1.1.8` 2024-11-28 → `v1.1.9` 2024-12-12 → `wired_v1.1.10`
2025-02-23 → `table_cls_v1.2.0` + `lineless_v0.1.0` 2025-03-31 — the
2025-03-31 notes also carry "Sup for rapidOCR 2.0" and "Fix wired unet
model" (GitHub releases API; corroborates (c)1–2). Repo last push
2025-08-03; issue volume tiny — **9 issues total ever** (search API),
**1 open** at research time (repo API `open_issues_count`, which counts
issues+PRs); the 2026-04 rapid-table 3.0.2 release shows the vendor is
active. Cadence is slow but real; the code is small enough to vendor if
abandoned.

### Known issues: Cyrillic / scan quality / skew

- **Cyrillic: a non-issue for the structure stage.** GitHub issue search
  (REST `search/issues`, 2026-09-13): `cyrillic OR Cyrillic` → **0 results**;
  `russian` → 0; `skew OR rotation` → 0. Structural models see pixels, not
  scripts — language quality lives in the OCR layer (already solved: eslav
  rec head per `ocr.py` module docstring). The FAQ's quality complaints are
  about OCR text inside cells, fixable by passing a *better* OCR result —
  exactly our seam.
- **Skew: handled in-package for wired v2** — the unet postprocess computes
  a rotation angle from line masks and un-rotates cell polygons
  (`rotated_fix=True` default, ±45°; `table_structure_unet.py` in-wheel).
  Lineless "lore" has no documented skew handling. For perspective/strong
  distortion the vendor points at `rapid-table-det` (perspective unwrap) and
  `RapidUnWrap` (document unwarp); out of scope unless anchors demand it.
- **Scan resolution window**: wired_table_rec_v2 "works best ≤1500 px;
  scale >2000 px images down proportionally" (CN README). Our rasters are
  150 dpi (`ocr.py` default `dpi=150`) → A4 ≈ 1240×1754 px — **inside the
  sweet spot with no rescaling**.
- **Merged cells**: issue #123 (2025-02-20, closed) — a résumé-style table
  loses a merged last column; #153 (2025-06-03, open) asks for JSON output.
  Known weak spot: exotic merges.

### Composition with RapidOCR (same vendor)

First-class: the README quick-start passes
`zip(boxes, txts, scores)` from rapidocr 2/3 into the table engine, and a
single-char variant uses `return_word_box=True` → `word_results`
(`[text, score, box]` tuples) with `enhance_box_line=False`. Version
windows: the TableStructureRec README caps wired/lineless at
`rapidocr <3.0.0`, while the rapid-table repo's matrix requires
`rapidocr>=3.0.0` for its v3 — neither binds us (see (c)1: no rapidocr
dependency in the wired/lineless wheels; we feed `ocr_result` directly).
Our engine is rapidocr 3.9.2 (`pyproject.toml:11`).

## 2. Alternatives

### PP-Structure(V3) table side — disqualified on weight (verified)

- `paddlepaddle` 3.3.1 (PyPI JSON): linux cp312 wheel **194.8 MB**, plus the
  whole PaddleOCR/PaddleX application layer on top. `paddlex` 3.7.2
  (PyPI JSON): **170 declared dependencies**, `requires_python >=3.8` with
  **zero cp312 wheels on PyPI**, paddlepaddle not even auto-installed
  (docs: install it separately). 2023-era PaddleOCR install threads
  (CSDN mirrors in search results) document the pain.
- Quality upside over RapidTable is real but modest: vendor's own TEDS
  table — PP-Structure engine 0.679 / SLANet-plus **0.845** /
  PaddleX(SLANetXt+RT-DETC) 0.799. SLANet-plus (available ONNX through
  rapid-table, above) is the same lineage without paddle.

### microsoft/table-transformer — disqualified on torch cost (verified)

- HF API (model repos, 2026-09-13): `microsoft/table-transformer-detection`
  and `.../table-transformer-structure-recognition` (and v1.1) —
  **MIT license**, ~115 MB weights each (safetensors ≈ pytorch_model.bin),
  lastModified 2023-09. Two models needed (detect + structure), so ~230 MB
  and two-stage inference.
- Runtime: PyTorch (torch 2.14.0 linux cp312 wheel = **554.6 MB**,
  PyPI JSON). Quality: deepdoctection(table-transformer) TEDS **0.600** —
  *below* RapidTable SLANet-plus 0.845 and below the wired+lineless combo
  0.777 (vendor TEDS table). **Dominated on every axis** (bigger, heavier,
  less accurate for this task).

### Pure-CV line projection — free fallback, hard ceiling

No ML at all: `cv2` morphological closing → horizontal/vertical line masks →
projection profiles / intersections → grid cells → assign OCR boxes by
center-point containment. Zero new deps (opencv already rides with
rapidocr), ~milliseconds per page. This is literally the back half of
wired's own pipeline (`get_table_line` → label → min-area rects,
`table_structure_unet.py`). What it **cannot** do: borderless tables
(the rule lines don't exist to project), merged-cell spans beyond naive
rectangle subtraction, rotated/perspective grids without hand-tuned angle
search. Verdict: keep as the degenerate fallback and as the mental model —
but the 8.3 MB unet exists precisely to do this robustly on real scans.

### DocLayout-YOLO / MinerU side models — disqualified (license + weight)

`opendatalab/DocLayout-YOLO` (GitHub API): **AGPL-3.0**, torch
(`doclayout-yolo` PyPI: torch≥2.0.1, torchvision, matplotlib, pandas,
seaborn, albumentations...). MinerU uses it but MinerU itself is a heavy
parser (already excluded by the map's carried constraints).

### Surya/tabled — noted, rejected (noise)

For completeness: the vendor's TEDS table lists surya-tabled at **0.339**
(with its own OCR deps); and it is torch-based. Not a candidate.

## 3. Heading recovery on scans

- **Font-size-from-OCR-boxes is the right cheap default, and the data is
  already in hand.** Introspected `RapidOCROutput` (rapidocr 3.9.2,
  `.venv/lib/python3.12/site-packages/rapidocr/utils/output.py` —
  read-only): `img, boxes (N,4,2 ndarray), txts (tuple[str]),
  scores (tuple[float]), word_results, elapse_list, elapse, viser` +
  `to_json() / to_markdown() / vis()`. The router's v1 discards everything
  but `txts` (`ocr.py:210-213`). Box **height** per line is a direct font
  size estimate for scans (unlike native PDFs there is no `span.size`, so
  this *is* the standard trick); cluster heights per page (e.g. relative to
  body-text mode) → candidate heading lines. Thresholds in TOML per the
  map's flat-config ethos.
- **What others do** (verified): rapidocr's own `to_markdown()` groups lines
  by y-overlap/gap into paragraphs — no heading semantics;
  `RapidLayout` (RapidAI, GitHub + PyPI 1.2.1 2026-03-24) is an onnx layout
  zoo pointing at PP / YOLOv8 / **DocLayout-YOLO** models with a `title`
  class — but its pip package carries **no model files and no default title
  model is pip-installable** (docs site model list; wheel is 6.9 MB of
  code), and its flagship model family is AGPL-3.0; PP-Structure's
  layout model carries the paddle tax; glm_ocr detects `vision_title`
  natively but is cloud opt-in (§4). **Conclusion: no credible local model
  beats the free heuristic on this axis; treat layout models as the opt-in
  escalation if anchors prove the heuristic insufficient.**
- Cheap upgrade inside the heuristic family: combine box-height with
  short-line, no-sentence-terminator, vertical-gap-before signals (all
  derivable from boxes+txts alone). This mirrors what the glm hybrid
  pattern does with `native_label` — minus the cloud.

## 4. glm_ocr `layout_details` as the opt-in structure source

Local sources (read-only): `~/bootools/skills/glm-ocr/SKILL.md` +
`~/parser_choose/pdf_parse_multimodal_glm.py` — both consistent with the
retired adapter notes carried in `src/pdf_router/ocr.py:343-359`.

Structure contract (SKILL.md "Response shape" + parser code):

- `layout_details: list[list[block]]` — per page; block fields:
  `bbox_2d [x0,y0,x1,y1]` (**pixels, top-left origin** — needs the y-flip
  when mapping to PDF points; parser `bbox_to_rect()`,
  `pdf_parse_multimodal_glm.py:65-68`), `label` ∈ {`text`, `table`,
  `image`, `figure`, `formula`, ...}, `native_label` (finer: `vision_title`,
  `vision_footnote`, ...), `content` (Markdown / **HTML table** / LaTeX;
  `None`/`""` for image/figure — guard with `block.get("content") or ""`),
  `index` (reading order), `width`/`height` (raster px).
- Titles: `label` = broad routing, `native_label` = `vision_title` etc.;
  neither is a PDF structural role (no native struct tree for scans).
- Tables: `label="table"` + HTML in `content`; the parser converts HTML →
  `{headers, rows}` via a stdlib `HTMLParser` (`pdf_parse_multimodal_glm.py:122-177`)
  — note rowspan/colspan are **not** handled there.
- `md_results` is whole-doc Markdown (~**1.8k tokens/page** per the retired
  adapter note, `ocr.py:355-357`); the skill's measured usage: input
  ~440 tokens/page linear, output NOT linear (5-page synthetic: 3,335
  tokens/page), 1 HTTP call per document, `usage` always populated.

Cost & caveats: **$0.03/1M tokens** in=out (docs.z.ai pricing page); 50-page
mixed doc ≈ $0.0013 (skill table). Cloud-only ⇒ never the default path (map
constraint); geo-caveats: `/files/parser` endpoint blocked overseas, use
`/layout_parsing`; coding-plan keys 429 without the document package;
Cyrillic body text fine, **dense stamps garble** («Страптельство» for
«Строительство», `ocr.py:357-359`); `md_results` and `layout_details` text
can diverge (rebuild from `layout_details`, SKILL.md gotchas table); LLM
sampling ⇒ not bit-deterministic run-to-run — matters for the map's
determinism workstream. Opt-in role: structure refinement for pages where
the local stack fails or where `vision_title` fidelity pays for its token.

## Local rapidocr introspection (the question under question 1's wire-up)

Method: read-only `uv run python -c` probes in the repo env; rapidocr
**3.9.2** (`importlib.metadata.version`), wheel `rapidocr-3.9.2-py3-none-any`
(27.3 MB, model-bundled). Findings:

- `RapidOCR.__call__` params: `img_content, use_det/use_cls/use_rec,
  return_word_box, return_single_char_box, text_score, box_thresh,
  unclip_ratio` — output type annotation:
  `Union[TextDetOutput, TextClsOutput, TextRecOutput, RapidOCROutput]`.
- `RapidOCROutput` (dataclass source read from
  `.venv/.../rapidocr/utils/output.py`): `img, boxes, txts, scores,
  word_results, elapse_list, elapse, viser` — **no table/structure mode, no
  font-size field, no reading-order groups** beyond `to_markdown()`'s
  y-overlap line joining.
- Practical consequence: the structure stage consumes `result.boxes/txts/
  scores` (and optionally `word_results` for single-char cell matching);
  nothing else in the result object helps. Box heights for the heading
  heuristic come free from `result.boxes`.

## Recommendation axis (NOT a ruling — TKT-ST-03 decides, HITL with user)

Two independent dials, each with a cheap default and an escalation:

- **Table structure axis:**

  wired unet (8.3 MB, onnx, Apache-2.0)
    → wired+lineless behind table_cls (three packages, ~97 MB weights)
    → + rapid-table slanet-plus (6.8 MB, in-wheel; best TEDS among local
       options, seq2seq HTML, no cell polygons)
    → glm_ocr `layout_details` (opt-in cloud; HTML tables + titles, ~1.8k
       tokens/page, $0.03/1M)
    → pure-CV line projection (zero-dep last resort for ruled tables).

  The minimal-resources end of the axis is served by wired-unet alone; the
  quality end (lineless 0.777 combo TEDS / slanet-plus 0.845) still fits
  onnxruntime-scale. The rapidocr version-window mismatch and the
  wired model_type pin are the two integration risks to retire in a probe.

- **Heading axis:**

  box-height statistics from the rapidocr result the router already has
  (zero new deps, thresholds in TOML)
    → + short-line / gap context signals
    → glm_ocr `vision_title` (opt-in cloud, per-persona)
    → (distant) onnx layout model — no acceptable local candidate found
       (RapidLayout ships no pip-installable title model; DocLayout-YOLO is
       AGPL + torch).

- **Cost framing for the decision:** OCR today is 24–40 s/page CPU; the
  table models add ~0.1–0.5 s/page CPU and 8–97 MB disk. Heading heuristics
  add ~0. The glm escalation adds ~1.8k tokens/page of cloud cost and
  non-determinism. Whatever the ruling, the seam is the same: feed
  `(raster, ocr_result)` to a structure stage, get HTML + logic points +
  cell polygons back, join to `TableBlock.rows`.

## Live probe (TKT-ST-03 ruling, 2026-09-13)

Both table engines installed in a uv overlay (`--with wired-table-rec
--with lineless-table-rec --with table_cls`; current PyPI releases) and
run on the two anchor pages: our `page_raster_array` @150 dpi + our
rapidocr result handed over as `[(box(4,2), text, score), ...]` — the
documented `ocr_result` contract, verified live (a first attempt with
bare point-lists crashed in `match_ocr_cell`, confirming the tuple
shape). CPU, single run per cell:

| page | wired unet (8.3 MB) | lineless lore (88.5 MB) |
|---|---|---|
| Рыбы p. 14 (ruled header, unruled body) | 3.8 s → 8 cells (header only; body lost) | 7.3 s → 144 cells (header + body + data rows in place) |
| Генетические p. 34 (ruled) | 3.9 s → 94 cells | 7.7 s → 149 cells |

- The user's on-disk correction proved decisive: Рыбы p. 14 is NOT a
  fully ruled table — wired's line-projection trick loses the body,
  lineless handles the mixed page. This drove the TKT-ST-03 ruling:
  default engine `lineless`; wired stays a TOML value.
- Known shared weakness for the quality bar: glued sub-columns — «V,%»/
  «h²» twin sub-columns merge into single cells («42,326,0»,
  «h²»→«$h{$») on BOTH engines; `logic_points` (94/149 spans) still
  carry row/col indices to split on at implementation time. TKT-ST-05
  gates must fuzzy-match cell text, not exact-match.
- Raw log `/tmp/st03_bench2.log` (transient); numbers recorded here.

## Appendix: evidence trail

- **GitHub READMEs** (fetched 2026-09-13):
  <https://github.com/RapidAI/TableStructureRec> (`README.md` CN +
  `README_en.md`; license badge Apache-2.0, API examples, TEDS table,
  model/compat tables, FAQ, usage guidance); `RapidAI/RapidTable`
  `README.md` (model zoo incl. slanet-plus 6.8 MB bundled, unitable 500 MB
  torch, compat matrix rapid_table v2.x/v3.x ↔ rapidocr≥3.0.0, batch API,
  "RapidTable and TableStructureRec are both table-rec repos — try both");
  `opendatalab/DocLayout-YOLO` `README.md` (pip install, torch dep);
  `RapidAI/RapidLayout` `README.md` (layout zoo, docs-site model list).
- **PyPI JSON** (`https://pypi.org/pypi/<pkg>/json`, fetched 2026-09-13):
  `wired-table-rec`, `lineless-table-rec`, `table_cls`, `rapid-table`,
  `rapidocr`, `rapidocr_onnxruntime`, `paddlepaddle`, `torch`, `paddlex`,
  `doclayout-yolo`, `rapid-layout` — versions, upload dates,
  `requires_python`, license fields, dependency lists, wheel sizes.
- **GitHub REST API** (2026-09-13): repo metadata (TableStructureRec:
  Apache-2.0, pushed 2025-08-03, 962★; DocLayout-YOLO: AGPL-3.0;
  unitable: MIT, 534★, pushed 2026-04-21; RapidLayout: pushed 2026-03-24);
  releases list (wired_v1.1.8→1.1.10, lineless_v0.1.0, table_cls_v1.2.0
  dates); issue search (cyrillic 0 hits, skew/rotation 0 hits — control
  query `is:issue` returns 9 issues total, so the zero-result counts are
  meaningful; merge/rowspan/colspan → #123 closed, #153 open).
- **HF model API** (`?blobs=true`): `microsoft/table-transformer-detection`
  (MIT, 115.32 MB safetensors), `microsoft/table-transformer-structure-recognition`
  (+ v1.1, MIT, ~115 MB each).
- **ModelScope** (RapidAI/RapidTable repo, 2026-09-13): actual downloads —
  `unet.onnx` 8,335,007 B; `cycle_center_net.onnx` 72,182,085 B;
  `lore/detect.onnx` 44,331,958 B; `lore/process.onnx` 44,239,345 B
  (URLs extracted from the shipped wheels' `main.py`).
- **Wheel introspection** (downloaded via `uv run --no-project --with pip
  python -m pip download --no-deps` to /tmp, unpacked read-only):
  wired 1.2.0 + lineless 0.1.0 — METADATA (Requires-Python `>=3.6,<3.13`,
  **no rapidocr dependency**), dataclass Input/Output definitions,
  `ocr_result` parameter contract, `need_ocr` semantics (default True;
  False → geometry-only output), `fill_blank_rec` (pads empty cells, no
  internal re-OCR), model URL tables, `table_structure_unet.py`
  (1024×1024 input, line masks → classical cell recovery, ±45°
  rotation correction).
- **Local probes** (repo env, `uv run python`, read-only): rapidocr 3.9.2
  metadata + `RapidOCROutput` dataclass source + `ToMarkdown` source;
  `pyproject.toml` (rapidocr[onnxruntime]>=3.9.2, onnxruntime>=1.29).
- **glm sources** (read-only): `~/bootools/skills/glm-ocr/SKILL.md`
  (endpoint, block schema, bbox y-flip, hybrid pattern, token table,
  gotchas); `~/parser_choose/pdf_parse_multimodal_glm.py` (block →
  `{headers, rows}` HTML parsing, `bbox_to_rect` y-flip, `label` routing
  sets); `src/pdf_router/ocr.py:343-400` (`_ocr_glm` adapter notes:
  ~1.8k tokens/page, stamp garbling).
- Constraint compliance: read-only everywhere except
  `docs/wayfinder/research/ocr-structure-tools.md` (this note);
  `pyproject.toml`/`uv.lock` untouched; all downloads to /tmp; nothing
  committed (user commits via `gaa && forge commit && gp`).
