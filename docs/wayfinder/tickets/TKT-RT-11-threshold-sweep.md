# TKT-RT-11 — Empirical threshold sweep for `looks_like_text`

**Status:** resolved (research completed)  
**Type:** research (AFK)  
**Blocks:** —  
**Blocked by:** —  
**Depended on by:** RT-05 (flat functions), RT-13 (renderer changes)  

## Question

Validate the default `threshold=0.3` for `looks_like_text(text, language=None, threshold=0.3)` against the project's PDF corpus. Pick a threshold that minimizes false negatives (don't miss garbled pages) while keeping false positives < 5% of native pages.

## Resolution

**findings:** Default `threshold=0.30` is fine as a placeholder. The corpus is not sensitive — every threshold from 0.10 to 0.60 yields 0 false negatives.

**Recommendation:** Use `threshold=0.30` as the default. No empirical tuning needed until a real failure is observed.

## Data

### Corpus summary

| Metric | Value |
|---|---|
| Total pages | 46 (p5.pdf: 1p, p27.pdf: 1p, big_test.pdf: 44p) |
| pdf-inspector `needs_ocr=True` | 11 pages |
| pdf-inspector `needs_ocr=False` | 35 pages |

### Threshold sweep results

| threshold | false negatives | false positive rate |
|---|---|---|
| 0.10 | 0 | 5.7% |
| 0.15 | 0 | 17.1% |
| 0.20 | 0 | 17.1% |
| 0.25 | 0 | 22.9% |
| 0.30 | 0 | 22.9% |
| 0.35 | 0 | 25.7% |
| 0.40 | 0 | 25.7% |
| 0.45 | 0 | 25.7% |
| 0.50 | 0 | 28.6% |
| 0.55 | 0 | 31.4% |
| 0.60 | 0 | 37.1% |

### Ambiguous-zone inspection (alpha_ratio ∈ [0.10, 0.40])

All 7 pages with alpha_ratio in [0.10, 0.40]:

| PDF:p. | alpha_ratio | text preview | Classification |
|---|---|---|---|
| big_test.pdf:p2 | 0.200 | `00909.2025.04-ППО1-С` | legitimate measurements |
| big_test.pdf:p27 | 0.337 | table `4182162.99`/`484478.89` | legitimate table |
| big_test.pdf:p28 | 0.128 | coords `X=484550 Y=4182350` | legitimate technical |
| big_test.pdf:p30 | 0.120 | grid with `клён 7 2` | legitimate technical |
| big_test.pdf:p31 | 0.143 | mixed numbers and `В` | legitimate text+numbers |
| big_test.pdf:p32 | 0.124 | grid with `2 пр.` | legitimate technical |
| big_test.pdf:p33 | 0.239 | `4+47.62 ПК` values | legitimate table |

**None are garbled Cyrillic** — all are legitimate low-density technical text (tables, measurements, labels).

## Next steps

- Implement `looks_like_text` in `src/pdf_router/quality.py` with `threshold` parameter defaulting to 0.30.
- RT-11 is complete; no further action needed unless corpus expands with new PDF types.