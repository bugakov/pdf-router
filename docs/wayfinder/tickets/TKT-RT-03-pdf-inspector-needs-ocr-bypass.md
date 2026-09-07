# TKT-RT-03 — `page.needs_ocr = True` bypasses `looks_like_text`

## Question

If pdf-inspector has already flagged `page.needs_ocr = True`, do we still run our own `looks_like_text` check?

## Resolution

No — we skip `looks_like_text` entirely when `page.needs_ocr` is True and go straight to OCR fallback. pdf-inspector's detection is more authoritative than our text-statistics heuristic (it knows about scanned pages, missing fonts, no ToUnicode, etc.). Our `looks_like_text` exists to catch the cases pdf-inspector missed — primarily CID-font no-ToUnicode, which can render correctly but extract as garbage.