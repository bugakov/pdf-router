# xlsx corpus geometry — research for TKT-OI-02

**Measured:** 2026-09-28 · **Corpus:** all 89 `.xlsx` files under `/home/scanum/kotel-zamechanna` (recursive) · **Method:** stdlib only (`zipfile` + `xml.etree.ElementTree`), read-only; measurement scripts live in `/tmp/opencode/xlsx/` (`measure.py`, `probe2.py`, `probe4.py`, `dump.py`) and are *not* committed; corpus untouched.

**Reading note:** these are XML facts, not visual truth. A *content cell* has `<v>`/`<f>`/inline text; a *styled cell* is a bare `<c>` with a `s=` attribute and no value (77.5% of all `<c>` elements). `<dimension>` is treated as a hint only. Coverage: **89 files / 172 sheets parsed, 0 errors, 0 skipped.**

---

## Area 1 — sheet inventory

| metric | value |
| --- | --- |
| files / sheets | 89 / 172 |
| sheets per file | 1 → 74 files · 2 → 5 files · 8 → 6 files · 10 → 4 files |
| multi-sheet files | 15 |
| distinct sheet names | 77 (most common: `Локальные замечания`, 46 sheets) |
| hidden / veryHidden sheets | 0 |
| sheets with zero cells | 0 |
| sheets missing `<dimension>` | 0 |
| duplicate sheet names inside one file | 0 |
| empty sheet names | 0 |
| sheet-name length > 31 chars | 0 (Excel's cap; names are pre-truncated, e.g. `ВОР № 02-01-01 Блочно-модульная`) |

File families (all 89 files):

| family | files | sheets | notes |
| --- | --- | --- | --- |
| `Локальные замечания` / `*ответы*` | 46 | 46 | one sheet each, creator `Apache POI` (46/46) |
| ВОР (single-sheet) | 16 | 16 | `ВОР № …` |
| `СВОР.xlsx` | 1 | 8 | ВОР with 8 sections |
| `СР-1` строительный контроль | 5 | 10 | 2 sheets each (`индексы по элементам затрат` + `ПП468`) |
| `Сводная ПИР` / `ПИР` | 5 | 40 | 8 sheets each |
| `ЛСР.xlsx` | 4 | 40 | 10 sheets each, by far the largest |
| `ССРСС` | 4 | 4 | includes the 54-column outlier |
| конъюнктурный анализ (`КА`) | 3 | 3 | |
| `СР-2` утилизация | 2 | 2 | the only formula-heavy family |
| smeta singles (`Сводный сметный расчет`, `ВОР_Сети…`, `Сети видеонаблюдения`) | 3 | 3 | |

---

## Area 2 — merges

| metric | value |
| --- | --- |
| files with ≥ 1 merge | 43 / 89 |
| sheets with ≥ 1 merge | 126 / 172 |
| total `<mergeCell>` entries | **27,655** |
| merges per sheet (126 merge sheets) | min 1 · p25 10 · **median 34** · p75 361 · max 2,508 |
| sheets with a merge starting at row 1 | 15 |

Shape classes. One horizontal row merge can be "full width" or not depending on which end of the sheet you compare against — both bases are given because they disagree badly:

| row-merge shape | vs sheet's last **content** column | vs sheet's last **styled** column |
| --- | --- | --- |
| full-width single-row merge | **2,720 merges in 125 sheets** | 331 merges in 48 sheets |
| partial single-row merge | 24,519 | 26,908 |
| vertical span (w=1, h>1) | 291 in 61 sheets | 291 in 61 sheets |
| block (w>1, h>1) | 125 in 45 sheets | 125 in 45 sheets |

- Strict full span (`A…` through the last content column): **1,261 merges**.
- The 2,389-merge gap between the two bases exists because ЛСР sheets declare ~240 styled columns (most hidden) while content stops at column 16 — a merge that visually fills the row never "reaches" the declared edge.
- Dominant shapes: `5×1` = 20,930 · `13×1` = 1,984 · `14×1` = 1,051 · `2×1` = 1,020 · `16×1` = 718 · verticals `1×3` = 145, `1×2` = 129 · blocks `5×3` = 40, `3×2` = 40.
- Merge-heavy tail is entirely `ЛСР.xlsx`: 11 of the 12 densest sheets (125–2,508 merges).

---

## Area 3 — formulas

| metric | value |
| --- | --- |
| formula cells (`<f>` present) | **287** |
| of which cached (`<v>` present) | **287 (100%)** — 0 uncached |
| cached value types | 287 numeric, 0 string, 0 error |
| formula-style | 207 with formula text; **80 are `t="shared"` slaves** (empty `<f/>`, value = master's cached result) |
| sheets with formulas | 17 sheet instances, 4 distinct sheet names |
| files with formulas | 9 basenames (multiple copies) |

Concentrated in 4 sheet names:

| sheet | formula cells | what it is |
| --- | --- | --- |
| `индексы по элементам затрат` | 175 | index/coefficient lookups (СР-1 ×5 files) |
| `Экспертиза` | 65 | summary sheet in `Сводная ПИР` (5 files) |
| `09-01` | 32 | `4. СР-2 (утилизация)` — waste-disposal fee math, e.g. `=D10*E10 ->65397.362079000006` |
| `Свод 421` | 15 | `Сводная ПИР` |

Every other sheet in the corpus stores pre-computed values only (ГРАНД-Смета export behavior).

---

## Area 4 — cell types and text hygiene

| metric | value |
| --- | --- |
| `<c>` elements total | 1,030,640 |
| content cells | **231,455** (22.5%); styled-but-empty = 799,185 |
| `t="s"` shared strings | 150,981 (all of them content) |
| numeric (`t` absent) content cells | 80,474 |
| `inlineStr` | **0** (unused everywhere) |
| `t="str"` / `t="b"` / `t="e"` / `t="d"` | **0 / 0 / 0 / 0** |
| `sharedStrings.xml` present | 89 / 89 files |
| Excel serial date cells | **0** (no cell is a real date) |
| date-styled cells holding `dd.mm.yyyy` **text** | 26 cells in 19 files (`numFmtId 14` + `t="s"`) |
| cells containing a `dd.mm.yyyy` substring | 3,296 (dates are typed as prose) |
| numeric cells whose raw `<v>` needs formatting | 13,700 with ≥ 8 decimals (17%), 5,926 in scientific notation — raw `float(v)` renders `4.8000000000000001E-2` |
| cells containing a pipe `\|` | **0** → pipe-based Markdown is safe |
| cells containing a newline | inside shared strings: 2,885 with LF only, 114 with CRLF |
| literal `_x000D_` escape in shared strings | 430 strings in 36 files (exported `\\r`) |
| NBSP / TAB in shared strings | 205 / 3 |
| `xml:space="preserve"` | 0 occurrences |
| empty-string cells | 15,665 + 691 whitespace-only = **16,356 (7.1% of content cells are blank text)** |
| longest single cell text | 2,657 chars (remarks table, has real newlines) |

---

## Area 5 — geometry

| metric | value |
| --- | --- |
| max actual row with content | 2,717 (`ЛСР.xlsx` Чижевщина, `Раздел ПД№12-СМ-ЛСР-06-03-01`) |
| max styled column | 252 (`ЛСР.xlsx` Лехово, `Сети теплоснабжения`, `dim A1:IR2041`) |
| max columns carrying content | 54 (`2. ССРСС.xlsx` sheet `1`) — the one real wide table |
| columns carrying content (distribution) | 2→5 sheets · 5→35 · 6→2 · 8→9 · 9→26 · 10→5 · 15→77 · 16→9 · 26→2 · 27→1 · 54→1 |
| fill ratio (content cells ÷ used area) | min 1.5% · p25 6.2% · **median 22.8%** · p75 100% · max 100% |
| sheets with ≥ 1 empty column inside the used range | **83 / 172** |
| sheets declaring hidden columns | **114 / 172** |
| content cells living in hidden columns | **41,387 (17.9%)** |
| hidden rows | 47 rows in 32 sheets (all top-frozen remark sheets) |
| `<dimension>` last column ≠ last styled column | 19 sheets |
| `<dimension>` extends past the last content column | **96 / 172 sheets** (trailing styled empty columns) |

Consequences for IR: width/height taken from `<dimension>` or `max_col` is off by up to ~236 columns on ЛСР sheets; "last column with content" and "last styled column" differ on 96 sheets; ~18% of real content sits behind `hidden="1"` and must not be silently dropped.

**The ЛСР far-region (the big surprise):** every ЛСР sheet carries content in columns A–P (16), then declares columns ~17–252 styled, hidden. Those far columns are not junk cells — they hold a **staircase duplicate**: a copy of each row's A–P text pushed progressively rightward. Verified on `Сводный сметный расчет`/`ЛСР`: 423/423 non-empty far cells on `ЛСР.xlsx` Опухлики Sheet1 and 341/341 on `Урицкого` duplicate same-row text (e.g. `r16 … EJ16='Строительство БМК …'`, `r40 GP40='Устройство бетонной подготовки'`). Naive "one cell → one fact" extraction would double-count every smeta line.

---

## Area 6 — extras, links, print settings

| feature | count |
| --- | --- |
| images / media (`xl/media`) | **0** |
| charts | **0** |
| drawings / text boxes | **0** |
| legacy + threaded comments | **0** |
| `xl/tables` (ListObjects) | **0** |
| hyperlinks (cells or `<hyperlinks>`) | **0** |
| pivot caches | **0** |
| data validations | **0** |
| conditional formatting | **0** |
| VML | **0** |
| `autoFilter` | 4 sheets (all `Локальные замечания`, e.g. `A1:P219`) |
| frozen panes | 46 sheets, all `ySplit=1` (freeze top row), all remarks sheets |
| header/footer text | 104 sheets in 36 files, essentially all `&RСтраница &P` (footer page number) |
| `_xlnm.Print_Titles` | 104 (file, sheet) entries in 36 files — repeating header row |
|   ↳ repeating header row numbers | row 38 → 40 sheets · row 19 → 30 · row 14 → 27 · row 24 → 5 · row 12 → 2 |
| `_xlnm.Print_Area` | 10 (file, sheet) entries |
| defined names | 3,282 total in 44 files; **2,870 contain `#REF!`**; 796 point at external workbooks `[n]`; max 1,583 in one file (`4. СР-2` copy) |

The 104 `Print_Titles` are the strongest objective evidence of a non-row-1 header: in those sheets the table header sits at row 38/19/14/24/12 and everything above it is a title + key-value block (see ЛСР dump below: rows 1–34 preamble, 4-row header at 35–38, body from 39).

---

## Area 7 — prose, metadata, non-table sheets

| metric | value |
| --- | --- |
| `docProps/core.xml` `<dc:title>` | **0 / 89** — no ready source for a document title |
| `docProps/app.xml` `TitlesOfParts` | 79 files (sheet names + print-area names only, not prose titles) |
| creators | `Apache POI` 46 · `Электрик 1` 27 · *none* 6 · `Анютка` 5 · `Litc2-001` 3 · `User` 2 |
| prose outside cells | only header/footer text (104 sheets); no text boxes, no comments |
| sheets where every row is part of one table | **167 / 172** — the 5 exceptions are `ПП468` |

`ПП468` (5 sheet instances across 3 file paths: `3. СР-1 Расчет стоительного контроля.xlsx`, `СР-1 Стр.контроль.xlsx`, `СР-1 Строительный контроль.xlsx`) is the only sheet family that is not one table: cover prose rows 1–16 → a 2-column table rows 18–31 → a free-text footnote with a formula as text rows 33–39 (`Н=0,04193*С0,8022/С`). It never exceeds 2 content columns.

But "one table per sheet" is still not enough: on the 104 `Print_Titles` sheets the header is **not** row 1. Observed preamble shapes:

- `ЛСР` — rows 1–34 title + key-value + cost summary, 4-row header 35/37/38, body from 39.
- `СВОР` — rows 1–12 key-value (`Документ`/`Версия`/`Дата составления`/`Составил`), header 13, column-number row 14, body from 15.
- `СР-2` — rows 1–4 title/subtitle, header 5, body 9–19, signature block 20–23.
- `Локальные замечания` — header at row 1 (16 columns), body from row 2, autofilter + frozen row.

---

## Representative dumps (trimmed cell text, `\n` shown as `\\n`)

#### ЛСР.xlsx (Опухлики, largest, 1,201,079 B) — sheet 1 of 10, rows 1-45
- docProps: title=None, creator='Электрик 1'
- definedName: `_xlnm.Print_Titles` = `'ЛСР-02-01-01 Блочно-модульная к'!$38:$38`
- definedName: `_xlnm.Print_Titles` = `'ЛСР-04-01-01 Сети электроснабже'!$38:$38`
- definedName: `_xlnm.Print_Titles` = `'ЛСР-05-01-01 Сети видеонаблюден'!$38:$38`
- definedName: `_xlnm.Print_Titles` = `'ЛСР-06-01-01 Сети водоснабжения'!$38:$38`
- definedName: `_xlnm.Print_Titles` = `'ЛСР-06-02-01 Сети водоотведение'!$38:$38`
- definedName: `_xlnm.Print_Titles` = `'ЛСР-06-03-01 Тепловые сети - ЛС'!$38:$38`
- definedName: `_xlnm.Print_Titles` = `'ЛСР-06-04-01 Сети газоснабжения'!$38:$38`
- definedName: `_xlnm.Print_Titles` = `'ЛСР-07-01-01 Благоустройство - '!$38:$38`
- definedName: `_xlnm.Print_Titles` = `'ЛСР-09-01-01 ПНР БМК - ЛСР по М'!$38:$38`
- definedName: `_xlnm.Print_Titles` = `'ЛСР-09-01-02 ПНР Сети электросн'!$38:$38`

**sheet `ЛСР-02-01-01 Блочно-модульная к`** — dimension `A1:II542`, merges: 461, hidden col ranges: 17-17, 18-18, 28-33, 34-43, 44-49, 50-59
- merge ranges (first 10 of 461): `C504:N504, A506:P506, A507:P507, A508:P508, C501:H501, I501:N501, C502:N502, C503:H503, I503:N503, C494:O494`
- headerFooter: {oddFooter:&RСтраница &P}
- r1: P1='Приложение № 3'
- r2: P2='Утверждено приказом Минстроя РФ № 421/пр от 4 августа 2020 г. в редакц..'
- r4: A4='Наименование программного продукта'  G4='ГРАНД-Смета, версия 2025.2'
- r5: A5='Наименование редакции сметных нормативов  '  G5='Приказ Минстроя России от 30.12.2021 № 1046/пр; Приказ Минстроя России..'  AB5='Наименование редакции сметных нормативов  '  AH5='Приказ Минстроя России от 30.12.2021 № 1046/пр; Приказ Минстроя России..'
- r6: A6='Реквизиты приказа  Минстроя России  об утверждении дополнений и измене..'  G6='Приказ Минстроя России от 18 мая 2022 г. № 378/пр, Приказ Минстроя Рос..'  AR6='Реквизиты приказа  Минстроя России  об утверждении дополнений и измене..'  AX6='Приказ Минстроя России от 18 мая 2022 г. № 378/пр, Приказ Минстроя Рос..'
- r7: A7='Реквизиты письма Минстроя России об индексах изменения сметной стоимос..'  G7='Письмо Минстроя России от 25.02.2025 № 10314-ИФ/09'  Q7='Реквизиты письма Минстроя России об индексах изменения сметной стоимос..'  R7='Письмо Минстроя России от 25.02.2025 № 10314-ИФ/09'  BH7='Реквизиты письма Минстроя России об индексах изменения сметной стоимос..'  BN7='Письмо Минстроя России от 25.02.2025 № 10314-ИФ/09'
- r8: A8='Реквизиты нормативного  правового  акта  об утверждении оплаты труда, ..'  G8='Приказ Комитета по строительству и жилищно-коммунальному хозяйству Пск..'  Q8='Реквизиты нормативного  правового  акта  об утверждении оплаты труда, ..'  R8='Приказ Комитета по строительству и жилищно-коммунальному хозяйству Пск..'  BX8='Реквизиты нормативного  правового  акта  об утверждении оплаты труда, ..'  CD8='Приказ Комитета по строительству и жилищно-коммунальному хозяйству Пск..'
- r9: A9='Обоснование принятых текущих цен на строительные ресурсы '  CN9='Обоснование принятых текущих цен на строительные ресурсы '
- r10: A10='Наименование субъекта Российской Федерации '  G10='60. Псковская область'  R10='60. Псковская область'  DD10='Наименование субъекта Российской Федерации '  DJ10='60. Псковская область'
- r11: A11='Наименование зоны субъекта Российской Федерации '  G11='Псковская область'  R11='Псковская область'  DT11='Наименование зоны субъекта Российской Федерации '  DZ11='Псковская область'
- r13: A13='Строительство БМК на природном газе в д. Опухлики'  EJ13='Строительство БМК на природном газе в д. Опухлики'
- r14: A14='(наименование стройки)'
- r16: A16='Строительство БМК на природном газе в д. Опухлики'  EZ16='Строительство БМК на природном газе в д. Опухлики'
- r17: A17='(наименование объекта капитального строительства)'
- r18: A18='ЛОКАЛЬНЫЙ СМЕТНЫЙ РАСЧЕТ (СМЕТА) № ЛСР-02-01-01'
- r20: A20='Блочно-модульная котельная'  FP20='Блочно-модульная котельная'
- r21: A21=' (наименование работ и затрат)'
- r22: A22='Составлен '  B22='ресурсно-индексным'  C22='методом'
- r23: A23='Основание'  B23='00846.2024.07-КР'  GF23='00846.2024.07-КР'
- r24: B24='(проектная и (или) иная техническая документация)'
- r26: A26='Составлен(а) в текущем уровне цен '  C26='I квартал 2025 года'  GK26='I квартал 2025 года'
- r28: A28='Сметная стоимость '  D28='14119.99'  E28='тыс.руб.'
- r29: B29='в том числе:'
- r30: B30='строительных работ'  D30='651.95000000000005'  E30='тыс.руб.'  K30='Средства на оплату труда рабочих'  O30='50.05'  P30='тыс.руб.'
- r31: B31='монтажных работ'  D31='0'  E31='тыс.руб.'  K31='Средства на оплату труда машинистов'  O31='3.64'  P31='тыс.руб.'
- r32: B32='оборудования'  D32='13468.03'  E32='тыс.руб.'  K32='Нормативные затраты труда рабочих'  O32='165.15'  P32='чел.-ч.'
- r33: B33='прочих затрат'  D33='0'  E33='тыс.руб.'  K33='Нормативные затраты труда машинистов'  O33='8.89'  P33='чел.-ч.'
- r35: A35='№ п/п'  B35='Обоснование'  C35='Наименование работ и затрат'  H35='Единица измерения'  I35='Количество'  L35='Сметная стоимость, руб.'
- r37: I37='на единицу измерения'  J37='коэффициенты'  K37='всего с учетом коэффициентов'  L37='на единицу измерения в базисном уровне цен'  M37='индекс'  N37='на единицу измерения в текущем уровне цен'  O37='коэффициенты'  P37='всего в текущем уровне цен'
- r38: A38='1'  B38='2'  C38='3'  H38='4'  I38='5'  J38='6'  K38='7'  L38='8'  M38='9'  N38='10'  O38='11'  P38='12'
- r39: A39='Раздел 1. Фундамент под БМК'  GO39='Раздел 1. Фундамент под БМК'
- r40: A40='1'  B40='ГЭСН06-01-001-01'  C40='Устройство бетонной подготовки'  H40='100 м3'  I40='4.8000000000000001E-2'  J40='1'  K40='4.8000000000000001E-2'  GP40='Устройство бетонной подготовки'
- r41: C41='Объем=4,8 / 100'  GU41='Объем=4,8 / 100'
- r42: B42='1'  C42='ОТ(ЗТ)'  H42='чел.-ч'  K42='6.48'  P42='1735.15'  GV42='ОТ(ЗТ)'
- r43: B43='1-100-20'  C43='Средний разряд работы 2,0'  H43='чел.-ч'  I43='135'  K43='6.48'  N43='267.77'  P43='1735.15'  GW43='Средний разряд работы 2,0'
- r44: B44='2'  C44='ЭМ'  P44='717.44'  GV44='ЭМ'
- r45: C45='ОТм(ЗТм)'  H45='чел.-ч'  K45='0.86975999999999998'  P45='383.95'  GV45='ОТм(ЗТм)'

- *other 9 sheets of this file: same preamble rows 1-34, 4-row header at 35-38, body from 39;*
  *dimensions `A1:IG1312` … `A1:IJ2162`; merges per sheet 361-1919; all 10 have Print_Titles `$38:$38`.*

#### 4. СР-2 (утилизация).xlsx (97,467 B) — sheet `09-01`, all rows (formulas)
- docProps: title=None, creator='User'
- definedName: `_xlnm.Print_Area` = `'09-01'!$A$1:$F$23`

**sheet `09-01`** — dimension `A1:F26`, merges: 16
- merge ranges (first 10 of 16): `A2:F2, A1:F1, B23:E23, B3:E3, B5:B8, B17:F17, A5:A8, D5:D8, E5:E8, C5:C8`
- r1: A1='СР - 2'
- r2: A2='Затраты на утилизацию строительных отходов по объекту: \\n"Строительств..'
- r4: B4='Составлен по состоянию на 1 квартал 2025г'
- r5: A5='№№                                   п/п'  B5='Наименование ЛСР'  C5='Ед. изм.'  D5='Масса отхода,           т'  E5='Величина тарифа\\n(НДС не облагается)                      '  F5='Сумма платы за размещение отхода, руб.'
- r9: A9='1'  B9='ЛСР-06-01-01 Сети водоснабжения'
- r10: A10='поз. 3\\nпоз. 12\\nпоз. 17'  B10='=0.11618+31.35+8.74 ->40.206180000000003'  C10='т'  D10='=B10 ->40.206180000000003'  E10='1626.55'  F10='=D10*E10 ->65397.362079000006'
- r11: A11='2'  B11='ЛСР-06-02-01 Сети водоотведения'
- r12: A12='поз .9\\nпоз. 14'  B12='=5.852+9.234 ->15.086'  C12='т'  D12='=B12 ->15.086'  E12='1626.55'  F12='=D12*E12 ->24538.133300000001'
- r13: A13='3'  B13='ЛСР-06-03-01 Тепловые сети'
- r14: A14='поз.15'  B14='=0.105 ->0.105'  C14='т'  D14='=B14 ->0.105'  E14='1626.55'  F14='=D14*E14 ->170.78774999999999'
- r15: A15='4'  B15='ЛСР-06-04-01 Сети газоснабжения'
- r16: A16='поз.4\\nпоз. 26'  B16='=4.237+0.00014 ->4.2371400000000001'  C16='т'  D16='=B16 ->4.2371400000000001'  E16='1626.55'  F16='=D16*E16 ->6891.920067'
- r17: A17='5'  B17='ЛСР-07-01-01 Благоустройство'
- r18: A18='поз.2'  B18='453.05500000000001'  C18='т'  D18='=B18 ->453.05500000000001'  E18='1626.55'  F18='=D18*E18 ->736916.61025000003'
- r19: A19='6'  B19='ИТОГО'  C19='т'  D19='=D10+D12+D14+D16+D18 ->512.68931999999995'  E19='1626.55'  F19='=F10+F12+F14+F16+F18 ->833914.81344599999'
- r20: A20='Расчет выполнен на основании Приложение N 3 к приказу Комитета по тари..'
- r23: B23='Составил  ____________________________Веселова К.В.                   ..'

#### СВОР.xlsx (89,097 B) — sheet 1 of 8, rows 1-15
- docProps: title=None, creator='Электрик 1'
- definedName: `_xlnm.Print_Titles` = `'ВОР № 02-01-01 Блочно-модульная'!$14:$14`
- definedName: `_xlnm.Print_Titles` = `'ВОР № 04-01-01 Электроснабжение'!$14:$14`
- definedName: `_xlnm.Print_Titles` = `'ВОР № 05-01-01 Видеонаблюдение '!$14:$14`
- definedName: `_xlnm.Print_Titles` = `'ВОР № 06-01-01 Водоснабжение - '!$14:$14`
- definedName: `_xlnm.Print_Titles` = `'ВОР № 06-02-01 Водоотведение - '!$14:$14`
- definedName: `_xlnm.Print_Titles` = `'ВОР № 06-03-01 Тепловые сети - '!$14:$14`
- definedName: `_xlnm.Print_Titles` = `'ВОР № 06-04-01 Газоснабжение - '!$14:$14`
- definedName: `_xlnm.Print_Titles` = `'ВОР № 07-01-01 Благоустройство '!$14:$14`

**sheet `ВОР № 02-01-01 Блочно-модульная`** — dimension `A1:I52`, merges: 3
- merge ranges (first 10 of 3): `A15:I15, A41:I41, A51:I51`
- headerFooter: {oddFooter:&RСтраница &P}
- r1: A1='Документ'  C1='Ведомость объемов работ'
- r2: A2='Версия'  C2='3_01'
- r4: A4='Наименование стройки'  C4='Строительство БМК на природном газе в д.Чижевщина'
- r5: A5='Наименование объекта капитального строительства'  C5='Блочно-модульная котельная'
- r6: A6='Ведомость объемов работ №'  C6='02-01-01'
- r7: A7='Основание((наименование раздела (подраздела) ПД))'  C7='00845.2024.07-ТХ, КР'
- r8: A8='Дата составления'  C8='20.03.2025'
- r10: A10='Составил ФИО'  C10='Носова Н.Н.'
- r11: A11='Проверил ФИО'  C11='Фролов А.И.'
- r13: A13='№ п.п.'  B13='Наименование работ, ресурсов, затрат по проекту'  C13='Ед. изм.'  D13='Объем работ / Количество'  E13='Формула расчета объемов работ и расхода материалов, потребности ресурсов'  F13='Ссылка на чертежи, спецификации в проектной документации'  G13='Наименование файла'  H13='Номер страниц \\n(через пробел)'  I13='Дополнительная информация (комментарий)'
- r14: A14='1'  B14='2'  C14='3'  D14='4'  E14='5'  F14='6'  G14='6.1'  H14='6.2'  I14='7'
- r15: A15='Раздел 1. Раздел 1. Фундамент под БМК'

#### Локальные замечания 16.05.2025.xlsx (104,046 B) — remarks table, rows 1-3
- docProps: title=None, creator='Apache POI'

**sheet `Локальные замечания`** — dimension `A1:P219`, merges: 0, hidden col ranges: 1-1
- autoFilter: `A1:P219`
- r1: A1='ID замечания'  B1='Номер замечания в проекте'  C1='Дата замечания'  D1='Статус'  E1='Вид замечания'  F1='Направление деятельности эксперта'  G1='ФИО эксперта'  H1='Раздел документации'  I1='Ссылка на материалы'  J1='Пункт'  K1='Предложение по отработке'  L1='Ожидаемый результат'  M1='Вывод о несоответствии'  N1='Основание'  O1='Ответ на замечание'  P1='Причина повторного направления'
- r2: A2='2025/03/27-052-0033-01'  B2='80.2'  C2='28.04.2025 18:00'  D2='Ответ не предоставлен'  E2='-'  F2='15. Ценообразование и сметное нормирование'  G2='Яковлева Светлана Ивановна'  H2='Пояснительная записка к сметной документации'  I2='Раздел 9. Шифр: 00783.2023.11-СМ'  J2='1: Подпункт "а" пункта 27\\n2: Пункт 4\\n3: Пункт 28'  K2='-'  L2='-'  M2='К сметной документации приложена пояснительная записка, не соответству..'  N2='1: Подпункт "а" пункта 27 Методики определения сметной стоимости строи..'  O2='В стадии исправления'  P2='-'
- r3: A3='2025/03/27-052-0035-01'  B3='22.7'  C3='28.04.2025 18:00'  D3='Ответ не предоставлен'  E3='-'  F3='1. Инженерно-геодезические изыскания'  G3='Тиховидов Андрей Вячеславович'  H3='Инженерно-геодезические'  I3='00783.2023.11-ИГДИ, СМЕТА № СР-1 Инженерно-геодезические изыскания'  J3='1: Пункт 4.41\\n2: Часть 1 статьи 15'  K3='-'  L3='-'  M3='Указанные объёмы  выполненных работ (таблица 1.1) не соответствуют пре..'  N3='1: Пункт 4.41 СП 47.13330.2016 «Инженерные изыскания для строительства..'  O3='Замечание принято. Таблица уточнена, стр.8 отчета ИГДИ и смета №СР-1'  P3='-'

#### smallest file: Локальные замечания 26.06.2025 РЗ.xlsx (10,181 B) — all rows
- docProps: title=None, creator='Apache POI'

**sheet `Локальные замечания`** — dimension `A1:P2`, merges: 0, hidden col ranges: 1-1
- r1: A1='ID замечания'  B1='Номер замечания в проекте'  C1='Дата замечания'  D1='Статус'  E1='Вид замечания'  F1='Направление деятельности эксперта'  G1='ФИО эксперта'  H1='Раздел документации'  I1='Ссылка на материалы'  J1='Пункт'  K1='Предложение по отработке'  L1='Ожидаемый результат'  M1='Вывод о несоответствии'  N1='Основание'  O1='Ответ на замечание'  P1='Причина повторного направления'
- r2: A2='2025/03/27-052-0072-06'  B2='29.6'  C2='24.06.2025 14:41'  D2='Ответ не предоставлен'  E2='-'  F2='8. Охрана окружающей среды'  G2='Сырова Екатерина Владимировна'  H2='Раздел 6. Мероприятия по охране окружающей среды'  I2='.'  J2='1: Часть 5 статьи 15, статья 32\\n2: подпункт "а" пункта 40\\n3: подпунк..'  K2='-'  L2='-'  M2='Отсутствует проект рекультивации земель для участков временного отвода..'  N2='1: Часть 5 статьи 15, статья 32 Федерального закона от 30.12.2009 № 38..'  O2='Замечание принято.\\nОткорректированный раздел рекультивации представле..'  P2='Замечание направляется повторно_x000D_\\nотсуствие проекта рекультиваци..'

#### 3. СР-1 Расчет стоительного контроля.xlsx — sheet `ПП468` (not-a-table control), all rows
- docProps: title=None, creator=None
- definedName: `_xlnm.Print_Area` = `ПП468!$A$1:$D$39`

**sheet `ПП468`** — dimension `A1:B40`, merges: 8
- merge ranges (first 10 of 8): `A15:B15, A16:B16, A9:B9, A10:B10, A11:B11, A12:B12, A13:B13, A14:B14`
- r1: B1='Приложение'
- r2: B2='к Положению о проведении'
- r3: B3='строительного контроля при'
- r4: B4='осуществлении строительства,'
- r5: B5='реконструкции и капитального'
- r6: B6='ремонта объектов'
- r7: B7='капитального строительства'
- r9: A9='НОРМАТИВЫ РАСХОДОВ'
- r10: A10='ЗАКАЗЧИКА НА ОСУЩЕСТВЛЕНИЕ СТРОИТЕЛЬНОГО КОНТРОЛЯ'
- r11: A11='ПРИ СТРОИТЕЛЬСТВЕ ОБЪЕКТОВ КАПИТАЛЬНОГО СТРОИТЕЛЬСТВА,'
- r12: A12='ФИНАНСИРУЕМЫХ ПОЛНОСТЬЮ ИЛИ ЧАСТИЧНО С ПРИВЛЕЧЕНИЕМ'
- r13: A13='СРЕДСТВ ФЕДЕРАЛЬНОГО БЮДЖЕТА, И НОРМАТИВЫ ЧИСЛЕННОСТИ'
- r14: A14='РАБОТНИКОВ ЗАКАЗЧИКА, НА КОТОРЫХ В УСТАНОВЛЕННОМ'
- r15: A15='ПОРЯДКЕ ВОЗЛАГАЕТСЯ ОБЯЗАННОСТЬ ПО ОСУЩЕСТВЛЕНИЮ'
- r16: A16='СТРОИТЕЛЬНОГО КОНТРОЛЯ'
- r18: A18='Стоимость строительства в базисном уровне цен по состоянию на 1 января..'  B18='Норматив расходов заказчика на осуществление строительного контроля (п..'
- r19: A19='до 30'  B19='2.14'
- r20: A20='от 30 до 50'  B20='1.93'
- r21: A21='от 50 до 70'  B21='1.81'
- r22: A22='от 70 до 90'  B22='1.72'
- r23: A23='от 90 до 125'  B23='1.61'
- r24: A24='от 125 до 150'  B24='1.56'
- r25: A25='от 150 до 200'  B25='1.47'
- r26: A26='от 200 до 300'  B26='1.36'
- r27: A27='от 300 до 400'  B27='1.28'
- r28: A28='от 400 до 500'  B28='1.23'
- r29: A29='от 500 до 600'  B29='1.18'
- r30: A30='от 600 до 750'  B30='1.1299999999999999'
- r31: A31='от 750 до 900'  B31='1.0900000000000001'
- r33: A33='Примечание. При стоимости строительства более 900 млн. рублей в базисн..'
- r34: A34='а) нормативы расходов на осуществление строительного контроля заказчик..'
- r35: A35='Н=0,04193*С0,8022/С'
- r36: A36='где:'
- r37: A37='Н - норматив расходов на осуществление строительного контроля заказчик..'
- r38: A38='С - стоимость строительства в базисном уровне цен по состоянию на 1 ян..'
- r39: A39='С0,8022 - стоимость строительства в базисном уровне цен по состоянию н..'

---

## Open questions for TKT-OI-03

1. **Merges → IR row/col spans:** emit `TableBlock` cells with `rowspan`/`colspan`, or unmerge (top-left text wins, followers dropped)? Matters at scale — 27,655 merges, and 2,720 single-row merges are visually full-width (their text *is* the row's only content).
2. **Which width basis drives `TableBlock`?** Last content column (15–16 on ЛСР), last styled column (236–252), or `max(dim, content)`? The ЛСР staircase duplicate (100% duplicate content, all hidden) says "content basis + drop hidden far columns", but that discards information a human reader never sees either.
3. **Hidden columns:** drop silently, keep with a flag, or keep only when content differs from the row's visible text? 41,387 content cells (17.9%) are affected.
4. **Header row detection:** trust `_xlnm.Print_Titles` (104 sheets, 5 distinct row numbers) as the header row, fall back to "first row", and what about the 68 sheets without it (do the 4-row ЛСР header at 35–37 then get split)? Do the column-number rows (`1 2 3 … 16` at row 38/14) become a body row or get discarded?
5. **Preamble rows (title + key-value):** emit as `Metadata`/prose blocks, or as a two-column table? СВОР's rows 1–12 are a clean label/value pair; ЛСР's rows 28–33 are a cost summary with numbers in columns D/E/O.
6. **Numbers:** format cached `<v>` through `numFmt` (13,700 cells have float noise, 5,926 scientific) or keep raw strings? There are no real dates — should `20.03.2025`-style text become a date type, and what about `28.04.2025 18:00`?
7. **Formulas:** publish the cached value only (100% cached here) or also the formula text? The 80 `t="shared"` slaves have no formula text at all — do they need master resolution?
8. **Blank text cells:** 16,356 content cells are empty/whitespace-only (7.1%) — drop them, or do they encode intentional empty table cells worth preserving?
9. **`ПП468` and other prose-heavy sheets:** how does a sheet that is prose → table → prose map onto the docx IR (which already has prose + tables)? Should `SheetBlock` be allowed to contain a sequence of blocks instead of exactly one?
10. **Broken defined names:** 2,870 of 3,282 contain `#REF!` — ignore all `definedNames`, or salvage `Print_Titles`/`Print_Area` first (they are valid and load-bearing for header detection)?
