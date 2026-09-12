# TKT-DX-07 — ADR-0003: docx native parse strategy

## Question

_(task — records the decisions already made; no new decisions.)_

Write `docs/adr/0003-docx-native-parse.md` following the ADR-0001/0002
conventions (Status/Date/Context/Decision/Alternatives/Consequences/
Implementation/References), synthesizing:

- **Strategy:** native stdlib parse (zipfile + ElementTree); LibreOffice→PDF
  rejected as wasteful (user, 2026-09-12); no ready-made tool qualifies
  (TKT-DX-01, `docs/wayfinder/research/docx-tools.md`).
- **Page semantics:** whole document = page 0 (TKT-DX-02).
- **IR bump:** `source_format`, `SrcTool.DOCX_ADAPTER`, IR 0.3 (TKT-DX-03).
- **Images:** RT-08 sidecar, kind=`image`, honest Nones, header media
  extracted (TKT-DX-04).
- **Quality bar:** golden anchors on project-documentation volumes, vMerge
  repeat policy, all-fast tests (TKT-DX-05).
- **CLI:** `--info` through the front door (TKT-DX-06).
- Constraint: `formats.py` docstring defers the docx inspector to this ADR —
  the ADR must exist before TKT-DX-08 writes the inspector.

## Resolution

_Closed 2026-09-12. ADR written: `docs/adr/0003-docx-native-parse.md` —
native stdlib parse, all six locked sub-decisions synthesized, alternatives
documented (LibreOffice/mammoth/markitdown/python-docx/docx2python/
docx2txt/pandoc/docling with rejection reasons), consequences honest
(no OCR for scanned docx; no pagination; IR 0.3 breaking note)._

Frontier moves to TKT-DX-08 (implementation).
