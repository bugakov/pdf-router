# TKT-DX-01 — Research: ready-made docx tool vs native stdlib parse

## Question

Is there a popular, ultra-light Python tool that turns docx into LLM/RAG-grade
structured material (headings with levels, tables as real rows, access to
embedded media) with quality ≥ and integration effort < a hand-rolled stdlib
parse (zipfile + ElementTree)? If no reliable ready-made solution exists, the
native stdlib parse stands (user's decision rule, 2026-09-12).

Constraints from the project ethos ("ultra-minimal, deterministic,
deploy-anywhere") and the user:

- pure-Python or near-zero transitive dependencies; `lxml` is NOT currently a
  dependency — a C-extension chain needs strong justification;
- no external binaries in the dependency path (LibreOffice rejected as
  wasteful; pandoc allowed only as an offline quality benchmark);
- scanned docx not expected — no OCR requirement;
- output must feed our IR: `HeadingBlock` (exact level from styles),
  `TableBlock` (real rows), `FigureBlock`/images, page markdown.

Candidates to investigate (primary sources: PyPI metadata, GitHub repos,
official docs):

- **mammoth** — docx→HTML/markdown; check markdown table handling (known
  historical limitation), style→heading mapping, image callbacks, dependency
  weight, maintenance state;
- **python-docx** — DOM API over lxml; heading/table access; how much glue
  code to build IR blocks;
- **markitdown** (Microsoft) — what it uses for docx; dependency weight when
  only the docx extra is needed;
- **docx2python**, **docx2txt** — structure fidelity, maintenance state;
- **pandoc** — benchmark-only reference for achievable markdown quality.

Empirical check on real corpus files (text/table-rich, image-light):

- `/home/scanum/fin-fine/data/docs/ФНС_контрольные_соотношения_ЕА-4-15-2048_2023.docx`
  (table-heavy);
- `/home/scanum/kotel-zamechanna/examples/Протокол ответов/№316 от 17.03.2026 г. - Ответы на замечания. Крюково.docx`
  (2 media files);
- `/home/scanum/kotel-zamechanna/archive/ВЫДАЧА_БМК Лехово/Титул на папки передачи БМК Лехово.docx`
  (small).

Run experiments in ephemeral environments without touching the project:
`uv run --no-project --with <pkg> python -c …` — never modify `pyproject.toml`
or `uv.lock`.

## Resolution

_Closed 2026-09-12 by the research subagent; full findings with citations:
[`../research/docx-tools.md`](../research/docx-tools.md)._

**Verdict: no ready-made tool qualifies — the native stdlib parse
(zipfile + ElementTree) stands.**

Evidence:

1. **mammoth** (pure-Python): markdown writer destroys tables — 0 pipe-table
   lines on all 3 corpus files *and* a synthetic control (writer limitation,
   not corpus pathology); cells leak as bold-flattened paragraphs. Its HTML
   writer is exact (28 `<tr>`/140 `<td>` on the ФНС file) but then HTML→md
   conversion is still needed — that is markitdown's job.
2. **markitdown[docx]** — the only quality-adequate candidate (pipe tables +
   built-in heading levels correct on all files), and decisively disqualified:
   base chain pulls `magika` → **numpy + onnxruntime** (an ML content-type
   detector duplicating our magic-byte dispatch seam), ~10 packages with 2 C
   extensions for one format, and images inlined as base64 data-URIs
   (24 KB docx → 657 KB md) — contradicting the locked sidecar-image
   convention (TKT-RT-08).
3. **python-docx / docx2python** — quality passes but require `lxml`
   (C extension, fails the lxml constraint); **docx2txt** — zero deps but
   flattens cell boundaries on 3/3 files, stale (last release 2025-03-24);
   **pandoc** — best row fidelity (colspan/rowspan) but an external binary:
   benchmark only, as locked.

Corpus ground truth discovered en route: all 3 corpus docs carry **zero
heading styles** (obfuscated `List Paragraph` styles) and exactly 1 table
each (28×5, 3×2, 6×3 — with `vMerge`). Heading fidelity is a wash across
tools; tables are the real quality battlefield, and only a native parse
shapes them directly into `TableBlock.rows` (~10 lines of pipe-markdown
generated from our own 2D rows — instead of parsing someone's output back).

**Consequence for the ADR:** native stdlib adapter through the locked
Inspector registry seam — styleId→styles.xml→builtIn map for heading levels,
exact 2D rows with `vMerge` unwrap, `word/media/*` → sidecar paths, title
from `core.xml`. Estimated ~100–150 lines of XML walking.
