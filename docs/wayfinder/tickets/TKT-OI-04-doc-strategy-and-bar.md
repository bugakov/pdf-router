# TKT-OI-04 — `.doc`: structure bar, strategy, and the fringe cases

## Question

With TKT-OI-01's evidence on the table, rule the `.doc` path:

1. **Strategy.** Which candidate (if any) is admitted — external binary
   (antiword/wv/soffice), JVM (Tika), Python package, or a hand-rolled
   OLE2/WordDocument parse — or does nothing qualify and `.doc` goes to
   Out of scope with the honest typed error we already raise? The
   decision rule: quality ≥ honest baseline AND deps ≤ baseline, weighed
   against the ethos ("ultra-minimal, deterministic, deploy-anywhere")
   and the per-format precedent (docx: no binary, ADR-0003; djvu:
   binary admitted, ADR-0004).
2. **Structure bar.** Whatever the tool, what does its output have to
   satisfy to be IR-worthy? Position on the docx↔flat-text spectrum:
   - must it fill `TableBlock.rows` (docx bar), or is
     markdown-table-in-`page.markdown` enough, or
   - is flat text acceptable with honest metadata (`needs_ocr=False`,
     `pdf_type` honestly typed, a way for consumers to see structure is
     absent)?
   The corpus profile matters: the 33 KB–1 MB `.doc` files are
   appendices/tables (Таблица 8, Ведомости), the 50–276 MB ones are
   narrative project texts. One bar for all, or per-class?
3. **Resource policy for the big files.** 276 MB / 118 MB inputs: time
   budget, memory, whether extraction is streamed or slurped, whether a
   size cap with a typed error is acceptable (and at what limit, informed
   by TKT-OI-01 timings). No temp files left behind; the 5-minute shell
   deadline applies to tests too — golden anchors on big files may need
   the slow tier.
4. **The fringe**, each with an explicit verdict:
   - **7 HTML-as-`.doc` files** (laws: 190-ФЗ, 44-ФЗ, …): detect by
     magic (`<!DOCTYP…`/`<html`) inside the `.doc` branch — ingest as
     text (there are 7 html files in the corpus anyway), or typed
     rejection "HTML with .doc extension"? (Building an HTML inspector
     is Out of scope for this map — so "ingest" here means the cheap
     honest option, whatever grilling lands on.)
   - **2 lock files** (`~$…`, 162 B, not OLE2): must never reach a
     parser — rejected at the gate with a recognisable message?
   - **Corrupt/truncated OLE2**: error taxonomy (gate `ValueError` vs
     `UnsupportedFormatError`), exit codes unchanged (README: 1
     unsupported / 3 unreadable).
5. **`--info` and metadata.** What does `docx_info`-style `--info` report
   for `.doc`; new `source_format` value(s); `SrcTool` member.

Inputs: TKT-OI-01 research; map Notes fringe facts. Output: rulings here
→ TKT-OI-05 (gate), TKT-OI-06 (ADR-0008), TKT-OI-09 (implementation).
If the verdict is "nothing qualifies", record it plainly — closing the
`.doc` question *is* reaching the destination for it (Out of scope, not
fog).

Blocked by: TKT-OI-01.

## Resolution

_Closed 2026-09-28 (grilling session; user confirmed all rulings;
evidence: [`../research/doc-tools.md`](../research/doc-tools.md))._

**Locked rulings:**

1. **Strategy: `office-oxide==0.1.12`** as a core dependency (abi3
   wheel 1.8 MB, zero runtime deps, MIT OR Apache-2.0, version pinned
   in `uv.lock`), lazy-imported per the heavy-deps rule. Rejected with
   measurement: native parse (420 s → killed at 56 MB), LibreOffice
   (256 MB on disk / 915 MB RSS / seconds per file — QA oracle only),
   antiword (57.3% recall at 56 MB), wv (zero headings + timeout),
   catdoc (hangs 2/5), Tika (≈276 MB JRE+jar, 3 cold-start failures),
   OfficeReadCpp (`license: null`). Docx/xlsx stay native — oxide does
   not replace them (ADR-0003 doctrine; OI-03).
2. **Structure bar = full docx-level IR:** whole document = single
   `PageResult(page=0)`; oxide tables → `TableBlock.rows` (row fidelity
   = LibreOffice ground truth: 184 = 184 at 56 MB); headings → levels
   best-effort with the F3 gap (1 vs 42) recorded honestly in ADR-0008;
   images → RT-08 sidecar (`images/page0001_image_XX.ext`, written only
   when `output_dir` is set — 74 images on F3); oxide metadata block →
   `Metadata.title` (title/author/created).
3. **HTML masquerade (7 law files): minimal stdlib text ingest.**
   Gate branches on magic `<!DOCTYP`/`<html` → `HTMLParser`-based
   tag-strip → prose paragraphs; `source_format="html"`,
   `SrcTool.HTML_ADAPTER`; no images, no OCR. A full HTML inspector
   remains out of scope for this map (map ruling).
4. **Resource policy: no artificial cap.** oxide parses 56 MB in
   4.099 s in-process; the 276 MB `ООС …Опухлики` file gets a
   slow-tier anchor in TKT-OI-07 (budget ≈ 60 s, run via `nohup`);
   a size cap is adopted only if that measurement demands it. The 276
   MB file is the main ООС text — it must not be cut off.
5. **Fringe (mechanical, decided):** Word lock files (`~$…`, 162 B)
   rejected at the gate with a recognisable message (exit 1); corrupt
   OLE2 → oxide's typed `OfficeOxideError` → `ValueError` → exit 3
   (existing taxonomy, README: 1 unsupported / 3 unreadable); `--info`
   routed through the adapter's own metadata summary (one-door
   pattern); `.doc`/`.html` never enter the OCR text pass
   (`core.py:213`) or the pdf image branch.
6. **IR typing:** `source_format` gains `"doc"` and `"html"`;
   `SrcTool` gains `DOC_ADAPTER` and `HTML_ADAPTER`; **IR version stays
   0.6** — the bump was already taken in TKT-OI-03 and both formats
   ship inside it.

Feeds: TKT-OI-05 (gate predicates: OLE2 / HTML magic / lock files),
TKT-OI-06 (ADR-0008 alternatives with research citations), TKT-OI-07
(.doc anchors incl. 276 MB slow tier + oxide pin), TKT-OI-09
(implementation).
