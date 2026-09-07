# TKT-RT-04 — `looks_like_text` language via config, default None

## Question

How should `looks_like_text` handle language? Hardcoded Russian? Auto-detect? Configurable?

## Resolution

Configurable, default `None`. `cfg.routing.language` is passed through to both `looks_like_text` and the OCR engine. When `language` is set (e.g. `"ru"`), `looks_like_text` is **strict**: it checks the fraction of characters in that language's Unicode range against `text_alpha_threshold`; if the page's text doesn't meet it, OCR fallback fires. When `language` is `None`, the test is **general**: fraction of any alpha character must exceed the threshold.

Rationale: explicit language is treated as a **commitment** by the caller, not a hint. False positives on bilingual documents are the caller's problem, not ours. Auto-detection is deferred to v0.4+.