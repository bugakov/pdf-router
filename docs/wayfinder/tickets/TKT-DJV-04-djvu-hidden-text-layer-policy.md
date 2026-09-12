# TKT-DJV-04 — Hidden text layer policy

## Question

djvu files may carry a hidden text layer (TXTz/TXTa from scan-time OCR).
Prevalence/quality evidence arrives from TKT-DJV-01. Policy questions:

- when the layer exists and passes `looks_like_text` — is it **native
  text** (skip OCR) or still re-OCRed for uniformity?
- which `SrcTool` records it (new member, e.g. `DJVU_TEXT_LAYER`, vs
  reuse of an existing one);
- metadata honesty: how the IR says "text from the djvu layer, not our
  OCR";
- what happens when the layer exists but fails the quality gate (strip?
  OCR fallback as usual?);
- whether the layer even survives the chosen strategy (A/B: probably lost
  → this ticket shrinks to a footnote; C: first-class).

Blocked by: TKT-DJV-02 (shape depends on the strategy); evidence from
TKT-DJV-01.

## Resolution

_(open)_
