"""pdf-inspector text-path adapter — the only always-loaded adapter.

One ``process_pdf`` call gives detection + extraction + concatenated Markdown
(single Rust pass, ~63ms on a 1-page text PDF). ``PdfResult`` carries no
per-page Markdown, so a second cheap call ``extract_pages_markdown`` supplies
the per-page ``pages[]`` array. The adapter itself never runs OCR; the
orchestrator (core.py) decides routing. See DESIGN.md §5 and ticket 03.
"""

from __future__ import annotations

from typing import ClassVar

from pdf_router.ir import IR, Metadata, OcrReasonEntry, PageResult, SrcTool


class AdapterPdfInspector:
    name: ClassVar[str] = "pdf_inspector"

    def available(self) -> bool:
        try:
            import pdf_inspector  # noqa: F401
        except Exception:
            return False
        return True

    def process(self, path: str) -> IR:
        import pdf_inspector

        result = pdf_inspector.process_pdf(path)  # single Rust pass
        pages_result = pdf_inspector.extract_pages_markdown(path)  # per-page markdown

        metadata = Metadata(
            pdf_type=result.pdf_type,
            page_count=result.page_count,
            confidence=result.confidence,
            processing_time_ms=result.processing_time_ms,
            title=result.title,
            is_complex_layout=result.is_complex_layout,
            has_encoding_issues=result.has_encoding_issues,
            pages_with_tables=list(result.pages_with_tables),
            pages_with_columns=list(result.pages_with_columns),
            pages_needing_ocr=list(result.pages_needing_ocr),
            ocr_reasons_by_page=[
                OcrReasonEntry(page=int(e.page), reasons=list(e.reasons))
                for e in getattr(result, "ocr_reasons_by_page", []) or []
            ],
        )

        # ocr_reason lookup keyed by 1-indexed page number
        reasons_by_page = {e.page: e for e in metadata.ocr_reasons_by_page}

        pages: list[PageResult] = []
        for pm in pages_result.pages:  # pm.page is 0-indexed
            one_indexed = pm.page + 1
            needs_ocr = one_indexed in metadata.pages_needing_ocr
            reason_entry = reasons_by_page.get(one_indexed)
            pages.append(
                PageResult(
                    page=pm.page,
                    markdown=pm.markdown or "",
                    needs_ocr=needs_ocr,
                    ocr_reason=(
                        ", ".join(reason_entry.reasons)
                        if reason_entry and reason_entry.reasons
                        else (pm.ocr_reason if hasattr(pm, "ocr_reason") else None)
                    ),
                    src_tool=SrcTool.PDF_INSPECTOR,
                )
            )
        return IR(metadata=metadata, pages=pages)
