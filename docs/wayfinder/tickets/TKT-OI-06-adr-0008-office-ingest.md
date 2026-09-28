# TKT-OI-06 — ADR-0008: office ingest (xlsx + legacy .doc)

## Question

Write `docs/adr/0008-office-ingest.md`, recording the map's decisions in
the house ADR shape (Context / Decision / Alternatives considered /
Consequences / Implementation):

- **xlsx = native stdlib parse** (zipfile + ElementTree) through the
  `_INSPECTORS` seam, with TKT-OI-03's locked sub-decisions (page
  semantics, merged-cell policy, formula/cached-value handling, value
  formatting, header rows, IR typing, `--info`).
- **`.doc` = TKT-OI-04's ruling** — a chosen tool/strategy with its
  dependency class and structure bar, or the explicit Out-of-scope
  verdict ("nothing qualified" is a legitimate ADR outcome, as long as
  the evidence from `research/doc-tools.md` is cited).
- **Format gate** changes per TKT-OI-05 (OLE2 / xlsx / fringe
  predicates).
- **Alternatives** documented in the ADR-0003 survey style: LibreOffice
  (measured, not pre-rejected), antiword/wv, Tika, OfficeReadCpp
  (no license), mammoth/docx2txt-class tools where applicable — each
  with the research citation, not re-argued from memory.
- **Consequences**: honest negatives (what the new formats do NOT get —
  no OCR, no pagination beyond the ruled page semantics, whatever the
  `.doc` bar gave up), IR/version impact, dependency delta (must stay
  zero-new-deps if native; name any exception explicitly).

Blocked by: TKT-OI-03, TKT-OI-04, TKT-OI-05.

## Resolution

_Closed 2026-09-28. ADR written: [`../../adr/0008-office-ingest.md`](../../adr/0008-office-ingest.md)._

Synthesizes TKT-OI-01..05 into the house ADR shape: three ingest paths
(native xlsx / `office-oxide==0.1.12` for OLE2 `.doc` / stdlib HTML
text ingest) + the grown format gate + IR 0.6, with the full
alternatives survey (LibreOffice, antiword, wv, catdoc, Tika,
OfficeReadCpp — all cited to `research/doc-tools.md`) and the honest
negatives (17.9% hidden xlsx content dropped; heading gap 1 vs 42;
0.1.x pin; no OCR path; `~$` name-check exception). Records the
deliberate non-survey of openpyxl with an explicit revisit trigger.
