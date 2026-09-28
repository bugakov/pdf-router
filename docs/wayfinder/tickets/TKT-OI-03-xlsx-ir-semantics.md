# TKT-OI-03 — xlsx → IR semantics

## Question

How does a workbook become IR? The docx precedent (ADR-0003): whole
document = single `PageResult(page=0)`, structure from the source, honest
`Metadata`, renderers untouched. xlsx needs the same set of rulings, and
the multi-sheet corpus makes page semantics a real question:

1. **Page semantics.** One `PageResult` per sheet (`page = sheet index`,
   `page_count = sheets`)? Or one page for the whole workbook (docx
   style) with sheet names as `##` headings in markdown? Decision inputs:
   the `md` renderer's `<!-- page N -->` markers, `chunks` splitting by
   headings, `core.py`'s per-page passes (text pass runs only for
   pdf/djvu — xlsx is born-digital, so nothing OCR-ish), image-sidecar
   naming (`page0001_image_…`). Are hidden/empty sheets emitted, skipped,
   or counted?
2. **Merged cells policy.** docx repeats the `vMerge` restart value into
   every row ("every row self-contained for retrieval"). xlsx merges come
   in shapes TKT-OI-02 measures: full-width **title rows** (`A13:P13` —
   repeating a caption 16× is retrieval poison), vertical label spans,
   header-group blocks. Pick the rule per shape (e.g. title-row merge →
   one cell, rest empty; data merge → repeat?) and state how the grid is
   built from `<c r="A10"…>` references (gaps → "").
3. **Formulas.** Emit the cached `<v>` value; what when `<f>` has no
   cached value (TKT-OI-02 counts them) — empty cell, `=FORMULA` text,
   or skip-the-sheet? Errors (`t="e"`) — what lands in the cell?
4. **Values as text.** Numbers: raw stored value vs formatted display
   (no `numFmt` engine in stdlib — smeta columns are numeric!). Dates:
   Excel serial → ISO date (needs the ~15 built-in date formats + custom
   `numFmtId`) or raw serial? Booleans, error cells.
5. **Header rows.** Does the IR fill `TableBlock.header_rows` (the smeta
   files have title rows above the real header — same trap as the
   Рыбы p.14 caption-row case already solved in the chunks renderer), or
   leave `header_rows` honest and let the renderer's caption-row demotion
   do it? (Renderer must NOT change — map Out of scope.)
6. **Prose & metadata.** Anything outside cells (text boxes, sheet
   header/footer, comments) — emitted as paragraphs or dropped? Title
   from `docProps/core.xml` (`dc:title`/`dc:creator`) like docx's
   `_core_title`.
7. **IR typing.** `Metadata.source_format` gains `"xlsx"`; new `SrcTool`
   member (`XLSX_ADAPTER`); `pdf_type`/`confidence` values (docx uses
   `"text_based"`/`1.0`); `IR_VERSION` bump — additive field (docx's 0.3
   pattern: change default only) or a real bump? `is_complex_layout`,
   `pages_with_tables` honesty.
8. **Images/charts.** Only what TKT-OI-02 found; if media exists —
   sidecar contract reuse (RT-08 naming), if none — record the zero.

Inputs: TKT-OI-02 findings; the docx adapter's conventions
(`adapters/docx.py`); `renderers/md.py`, `renderers/chunks.py` contracts
(they must keep working unchanged). Output: rulings recorded here,
feeding TKT-OI-06 (ADR-0008) and TKT-OI-08 (implementation).

Blocked by: TKT-OI-02.

## Resolution

_Closed 2026-09-28 (grilling session; user confirmed all 9 rulings)._

**Locked semantics:**

1. **Page semantics:** sheet = page — `page_count` = sheet count, one
   `PageResult` per sheet, `page` = sheet index. 74/89 single-sheet
   files degrade to docx-like behaviour.
2. **Sheet name:** every page's markdown starts with `## <sheet name>`
   — the only section signal there is (`docProps` title = 0/89; the 15
   multi-sheet files get their sections this way).
3. **Hidden columns: cut.** Grid width = last **visible** column with
   content (verified empirically on `ЛСР.xlsx` Sheet1: visible content
   → col 19, hidden staircase → col 236 — the far region dies).
   Honest consequence for ADR-0008: 41,387 content cells (17.9%) are
   dropped, incl. the ЛСР staircase duplicate (100% same-row copies).
   (User first ruled "keep everything", then reversed on the concrete
   width cost — visible-only stands.)
4. **Hidden rows: cut** (symmetry; 47 rows in 32 sheets, all
   top-frozen remark sheets).
5. **Merged cells (27,655):** vertical merge → value **repeated down**
   every spanned row (docx `vMerge` invariant — rows stay
   self-contained); horizontal merge → value in the **first cell only**,
   followers `""`. The horizontal difference from docx `gridSpan`
   expansion is deliberate: chunks' caption-demotion needs a single
   content cell, and a full-width title repeated ×16 is retrieval
   poison. No rowspan/colspan in IR — the grid stays flat (v0.7
   doctrine).
6. **Sheet → blocks = alternating runs** (not one table): a row is
   *dense* when ≥50% of visible columns carry content — dense runs →
   `TableBlock`, sparse runs → prose paragraphs in `page.markdown`;
   empty rows don't break a run, trailing empties are trimmed. This
   catches the smeta shape (preamble → header → body) and the 5
   `ПП468` prose→table→prose sheets with one rule, and gives the IR
   the same shape docx already has (sequence of paragraphs + tables).
7. **`header_rows`** = the existing IR heuristic (leading rows where
   every cell is short text, no digits) — `Print_Titles`/`definedNames`
   are NOT used (2,870/3,282 are `#REF!`-broken; density already does
   the split). Consequence: the all-digit column-number row (r38
   family) fails the heuristic and becomes the first body row.
8. **Values:** numeric `<v>` → `float` → Python `repr` (kills
   `4.8000000000000001E-2` → `0.048`; integral floats → plain digits);
   formulas publish the **cached value only** (100% cached in corpus;
   `t="shared"` slaves need no master resolution); whitespace-only →
   `""` (grid stays rectangular); **no numFmt engine, no date
   conversion** (0 Excel date serials in the corpus — dates are prose);
   booleans/errors don't occur (0/172 sheets).
9. **IR typing:** `Metadata.source_format = "xlsx"`, new
   `SrcTool.XLSX_ADAPTER`, `IR_VERSION` **0.5 → 0.6** (precedent: docx →
   0.3, djvu → 0.4), `pdf_type="text_based"`, `confidence=1.0`,
   `is_complex_layout=False`, `pages_with_tables` = 1-indexed sheet
   numbers that produced tables; `Metadata.title` = `docProps` title
   (honest `None` at 0/89); `pages_with_columns` stays empty.
   **Media/charts: nothing to do** — corpus has 0 (`xl/media`, charts,
   drawings, comments all zero — TKT-OI-02 area 6); no sidecar work,
   no `core.py` image branch for xlsx in v1.

No new tickets graduated; implementation lives in TKT-OI-08, gate in
TKT-OI-05, ADR write-up in TKT-OI-06, anchors in TKT-OI-07.
