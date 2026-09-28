# TKT-OI-07 — Quality bar: golden anchors for xlsx (+ .doc if admitted)

## Question

What proves the new ingest is *good enough* — pinned to real corpus
files, in the v0.4/v0.7 style (fast / middle / slow tiers, skip-if-missing
corpus fixtures, `conftest.py`-style builders for the synthetic control):

1. **Anchor selection** (from TKT-OI-02's geometry + TKT-OI-01's runs):
   - xlsx fast control: smallest/synthetic workbook covering
     sharedStrings, merged title row, formula-with-cache;
   - xlsx middle: a smeta sheet with merges + numeric columns (e.g. the
     `4. СР-2 (утилизация).xlsx` formulas case);
   - xlsx slow: `ЛСР.xlsx` (10+ sheets, 1.2 MB) — sheet count, total
     rows, merge invariants, wall-clock budget;
   - `.doc` anchors only if TKT-OI-04 admitted a path (small appendix +
     one big file in the slow tier with a generous but explicit budget).
2. **Invariants worth asserting** (pick with the semantics ticket's
   rulings): grid rectangularity; merged-cell policy (the xlsx analogue
   of the docx vMerge-repeat invariant); no value loss (spot-check cell
   values vs a stdlib dump); formula cells carry cached values; page/sheet
   count; markdown render → `chunks` renderer end-to-end without
   touching renderer code; `--info` matches `extract_ir` (one-door
   pattern); zero new deps guard still green.
3. **Budgets**: parse-time ceilings per anchor (docx precedent:
   `elapsed < 5.0 s` "native parse, no excuses"); big-`.doc` budget from
   TKT-OI-01 timings.
4. **Suite hygiene**: everything fast stays in the default run; corpus
   anchors skip when unmounted; never two pytest runs in parallel.

Deliverable: the anchor list + invariants + budgets recorded here (the
tests themselves are written inside TKT-OI-08/09).

Blocked by: TKT-OI-05.

## Resolution

_Closed 2026-09-28 (grilling; user confirmed tiers, fixture policy,
exact-count pinning)._

### Fast tier — no corpus, runs in CI (`-m "not slow"`)

1. **Synthetic xlsx builder** (test-only helper, mirror of the docx
   builder in `tests/test_docx.py`): sharedStrings values, vertical /
   horizontal / block merges, a hidden column + a hidden row, a cached
   formula, float-noise numbers, whitespace-only cells, two sheets,
   sparse preamble + dense body. Assertions (exact):
   - grid rectangular; vertical merge **repeated down**, horizontal
     merge **first cell only**; hidden column/row **absent**;
   - every page's markdown opens with `## <sheet name>`;
     `page_count == 2`; `source_format == "xlsx"`;
   - `repr` cleanup pinned (`0.048`, integral floats plain);
   - `from_dict(to_dict(ir)) == ir`; every chunk `embed` non-empty;
     `md` renderer output non-empty (renderers untouched).
2. **Gate tests** (TKT-OI-05 predicates, verbatim): xlsx recognized &
   routable (the old `test_detect_ooxml_not_docx_hint` **flips**),
   pptx precise rejection, OOXML message update, OLE2 → `Format.DOC`,
   HTML magic → `Format.HTML` (BOM/WS-tolerant), `~$` lock message,
   extension-lying cases (OLE2 named `.pdf`, xlsx named `.docx`),
   corrupt zip unchanged, generic zip unchanged.
3. **HTML synthetic**: inline `<!DOCTYPE html>` bytes with sections,
   entities, inline tags → prose paragraphs, no tags in output,
   `source_format == "html"`, IR round-trip, chunks non-empty.

### Corpus tier — skip-if-missing, default run (like the docx goldens)

| anchor | path | exact pins | budget |
|---|---|---|---|
| ЛСР (multi-sheet) | `…/ВЫДАЧА_БМК Опухлики/ПД ред. формат/11. СМ ред. формат/ЛСР.xlsx` | `page_count == 10`; all 10 `##` sheet headings present; **no staircase**: per-sheet grid width ≤ last visible content column (sheet 1 → ≤19); rectangular grids; ≥1 vertical-merge repeat invariant spot-check; horizontal title row = single content cell; chunks non-empty; IR round-trip | `elapsed < 10.0` (measured 3.51–4.65 s warm; cold first run ~7 s, loaded sessions up to 9.7 s — **amended from `<5.0` during TKT-OI-08**, same run) |
| СР-2 (formulas) | `…/ВЫДАЧА_БМК Горького/…/4. СР-2 (утилизация).xlsx` | 16 formula cells → cached values; `B10 == D10 == "40.20618"` (repr pin); no `<f>` text in output | `elapsed < 5.0` |
| smallest xlsx | `…/examples/_Архив замечаний/Локальные замечания 26.06.2025 РЗ.xlsx` (10,181 B) | 1 sheet; header = row 1 → `header_rows` per heuristic; known cell text present | `elapsed < 5.0` |
| `.doc` F2 274 KB | `…/ВЫДАЧА_БМК Чижевщина/ИИ ред. формат/ИГДИ ред. формат/2. Приложения/Приложение Б. Программа работ/ПР ИГДИ (00845.2024.07).doc` | **4 tables / 32 rows** (dims `[1,2] [16,2] [5,4] [10,3]`), **7 headings** (oxide `to_ir`; **amended 9 → 7 during OI-09** — 9 was the LibreOffice round-trip ground truth, the admitted tool yields 7 of 9; the 2-heading gap is recorded honestly, mirroring F3's 1-vs-42), `page_count == 1`, `source_format == "doc"`, known Cyrillic token, chunks non-empty, IR round-trip, 1 image placeholder ↔ sidecar file | `elapsed < 5.0` (measured 21.6 ms) |
| HTML law | `…/normas/01_ФЗ_и_ПП_РФ/190-ФЗ_Градостроительный_кодекс.doc` (3,059,959 B) | prose paragraphs, no `<` tags in markdown, `markdown length > 1_000_000` (measured 1,335,359), known token «Градостроительный кодекс», `source_format == "html"` | `elapsed < 5.0` (measured 1.28 s) |

### Slow tier — `-m slow` (deselect with `-m 'not slow'`, CI stays fast)

| anchor | path | exact pins | budget |
|---|---|---|---|
| `.doc` F3 56 MB | `…/ВЫДАЧА_БМК Лехово/ИИ ред. формат/ИГМИ ред. формат/00847.2024.07-ИГМИ_Изм.1.doc` | **184 total table rows** (= LibreOffice ground truth), 79 tables, ≥1 heading (oxide's honest 1-vs-42), **74 images**: every markdown image placeholder resolves to a written sidecar when `output_dir` set, `markdown length > 50_000` (**amended from `> 1 MB` during OI-09**: the 1 MB figure assumed base64-in-IR, which RT-08 forbids — sidecar placeholders are small; measured 58,618), known token, IR round-trip | `elapsed < 15.0` (measured **8.43 s**, RSS 406 MB, 2026-09-28) |
| `.doc` 276 MB | `…/ВЫДАЧА_БМК Опухлики/ПД ред. формат/8. ООС ред. формат/ООС Котельная Опухлики СЗЗ_ред 4_2.doc` | parses without error, `page_count == 1`, `markdown length > 400_000` (**amended from `> 1 MB` during OI-09**, measured **490,316**), 1413 tables / 1820 rows, 6 image placeholders ↔ sidecars, known token «Котельная»; **actual wall/RSS recorded (the TKT-OI-04 measurement promise): 6.97 s wall, 454 MB peak RSS, 2026-09-28** — no size cap needed (OI-04 ruling 4 holds) | `elapsed < 60.0` |

### Policies locked with the bar

- **Exact-count pinning** (docx precedent: 23 tables / ≥40 headings):
  structural drift turns the suite red — user's choice.
- **No committed `.doc` fixture** — user's choice: every `.doc` test
  is corpus-skip; a clean machine verifies gate predicates + HTML +
  xlsx only. (Accepted trade-off; revisit if CI must cover OLE2.)
- **Corpus anchors are never committed.** Repo weight verified
  2026-09-28: largest tracked file `uv.lock` 252 KB, fixtures
  172–192 KB, `.git` 4.8 MB — nothing heavy to remove *today*; if a
  future addition grows the repo, it gets pruned (user's standing
  instruction, recorded here).
- Hygiene carried: never two pytest runs in parallel; 5-min shell
  deadline → slow tier via `nohup … &` + poll when run manually;
  long corpus tests must `pytest.skip` gracefully when unmounted.

Feeds TKT-OI-08 (writes fast + xlsx corpus tests) and TKT-OI-09 (writes
.doc/HTML tests, runs both slow anchors, records the 276 MB numbers
back into this ticket).
