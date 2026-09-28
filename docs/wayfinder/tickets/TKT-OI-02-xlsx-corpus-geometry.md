# TKT-OI-02 — Research: xlsx corpus geometry

## Question

What exactly must a `sheet → IR` mapping handle on OUR xlsx corpus?
Full statistics over all 89 files + content dumps of representative
sheets, so TKT-OI-03 (xlsx → IR semantics) decides on facts, not on
guesses about Excel in general.

Seed facts (map Notes, 2026-09-28, sampled): 89 files, 172 worksheets,
126 sheets with `<mergeCell>`, 17 with `<f>` formulas, 60/60 with
`sharedStrings.xml`, 0 media in sample, 15/60 multi-sheet, largest 1.2 MB
(`ЛСР.xlsx`, 10+ named sheets, title merges like `A13:P13` next to data
merges `A10:F10`/`G10:P10`, sheet1 dimension `A1:IG465`).

Measure over **all 89 files** (stdlib `zipfile` scripts, no new deps):

1. **Sheets**: count distribution; sheet names (any empty/duplicate?);
   hidden sheets (`state="hidden"`/`"veryHidden"`); empty sheets
   (dimension-less or no `<row>`); how many files are multi-sheet.
2. **Merged cells**: per-file/per-sheet counts; classify the *shape* —
   full-width single-row merges (title/caption rows), single-column
   vertical merges (label spans), block merges (header groups) — so the
   TKT-OI-03 policy question ("repeat value vs leave empty vs first-cell
   only") is grounded in which shapes actually occur.
3. **Formulas**: sheets with `<f>`; how many formula cells have a cached
   `<v>` vs none; which value types the cached values carry (numeric,
   string, bool, error).
4. **Cell content types**: shared vs inline strings; `t="s"` vs
   `inlineStr`; number formats that imply dates (`numFmtId` / custom
   formats → do Excel serial dates appear?); booleans; error cells;
   cells with `xml:space` / newlines inside strings.
5. **Geometry**: max row/column per sheet; how sparse (cells present vs
   dimension); rows beyond used range; columns with no content.
6. **Extras**: `xl/media/*` (images), charts (`xl/charts/*`) and their
   cached text, drawings/text boxes (`xdr:*` with `a:t`), comments
   (`threadedComments`/`legacyDrawing`), defined names, hyperlinks,
   autofilter, `xl/pivotCache*`.
7. **Structure beyond tables**: is there any prose outside cells?
   (paragraph text in sheets, header/footer text, docProps title/creator
   for `Metadata.title`.)

Representative dumps (first ~15 rows of every sheet, values resolved via
sharedStrings) for at least:

- `/home/scanum/kotel-zamechanna/**/ЛСР.xlsx` (multi-sheet smeta,
  merges, biggest);
- `…/4. СР-2 (утилизация).xlsx` (has formulas);
- `…/СВОР.xlsx` and
  `…/examples/_Архив замечаний/Локальные замечания 16.05.2025.xlsx`
  (the non-smeta shape — remarks table);
- smallest xlsx in the corpus (control).

Also answer: does anything in the corpus look like a **sheet that is not
a table at all** (free text, key-value form, blank), which would need a
different rendering than `TableBlock`?

Deliverable: `docs/wayfinder/research/xlsx-corpus.md` — stats tables,
the dumps, and an explicit "open questions for TKT-OI-03" section + a
pointer appended to this ticket. Read-only; no project code changes.

Blocked by: nothing — frontier.

## Research findings

Full report: [`docs/wayfinder/research/xlsx-corpus.md`](../research/xlsx-corpus.md)
(measured 2026-09-28, all 89 files / 172 sheets, 0 parse errors, stdlib
read-only scripts in `/tmp/opencode/xlsx/`).

Gist: 231,455 of 1,030,640 cells carry content (77.5% are styled-but-empty),
all strings are `t="s"` shared (0 `inlineStr`, 0 bool, 0 error, **0 real date
serials** — dates are typed prose), and only 287 formula cells exist (100%
cached) in 4 sheet names. Geometry is the real hazard: 27,655 merges (2,720
row merges are full-width only when measured against the *last content*
column, 331 against the *last styled* column), 96/172 sheets have a
`<dimension>` wider than their content, and 41,387 content cells (17.9%)
hide behind `hidden="1"` columns — including the ЛСР "staircase duplicate",
where 100% of far hidden cells repeat the same row's A–P text. Every sheet is
one table except 5 instances of `ПП468` (prose → 2-col table → footnote), but
on 104 sheets `_xlnm.Print_Titles` proves the header sits at row 38/19/14/24/12
with a title/key-value preamble above it, so "header = row 1" is false for the
smeta families; `docProps` title is 0/89. Ten open questions for TKT-OI-03 are
listed at the end of the report (merge policy, width basis, hidden columns,
header detection, preamble rows, number formatting, formulas, blank text
cells, prose sheets, `#REF!` defined names).

## Resolution

_Closed 2026-09-28 by the research subagent; full stats, dumps and open
questions: [`../research/xlsx-corpus.md`](../research/xlsx-corpus.md)._

Key facts for TKT-OI-03: 89 files / 172 sheets, 0 parse errors; 74
single-sheet vs 15 multi-sheet; 0 hidden/empty sheets; 27,655 merges
(2,720 full-width vs *last content* column but only 331 vs *last styled*
— the two width bases disagree on 2,389); 41,387 content cells (17.9%)
behind hidden columns, incl. the ЛСР staircase duplicate (far hidden
cells repeat the row's A–P text 100%); 287 formula cells, 100% cached; 0
dates (prose text), 0 bool/error/inlineStr, 0 media/charts/comments;
104 sheets declare `_xlnm.Print_Titles` → header is NOT row 1 on smeta
families; only 5 non-table sheets (`ПП468` prose cover); `docProps`
title 0/89; 16,356 whitespace-only cells, float noise in numerics,
`|` never occurs. Ten open questions listed at the report's end feed the
grilling.
