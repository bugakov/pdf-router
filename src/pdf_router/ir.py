"""Intermediate Representation (IR) — the single shared contract.

Every adapter produces it, every renderer consumes it. Adapters never import
renderers and vice versa; both may import this module. See DESIGN.md §4.

Indexing convention (locked):
- global lists in ``Metadata`` (``pages_with_*``, ``pages_needing_ocr``) are
  **1-indexed**, matching pdf-inspector's convention;
- ``PageResult.page`` is **0-indexed**, matching Python iteration.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from enum import Enum
from typing import Any

IR_VERSION = "0.1"


class SrcTool(str, Enum):
    """Provenance tag: which tool produced a page's markdown."""

    PDF_INSPECTOR = "pdf_inspector"
    PDF_INSPECTOR_OCR = "pdf_inspector_ocr"
    TESSERACT = "tesseract"
    GLM_OCR = "glm_ocr"
    DEEPSEEK_OCR = "deepseek_ocr"


@dataclass
class OcrReasonEntry:
    """One entry of ``ocr_reasons_by_page`` (1-indexed page)."""

    page: int
    reasons: list[str] = field(default_factory=list)


@dataclass
class Metadata:
    """Document-level facts, mapped from pdf-inspector's ``PdfResult``."""

    pdf_type: str = "unknown"  # text_based | scanned | image_based | mixed
    page_count: int = 0
    confidence: float = 0.0
    processing_time_ms: float = 0.0
    title: str | None = None
    is_complex_layout: bool = False
    has_encoding_issues: bool = False
    pages_with_tables: list[int] = field(default_factory=list)  # 1-indexed
    pages_with_columns: list[int] = field(default_factory=list)  # 1-indexed
    pages_needing_ocr: list[int] = field(default_factory=list)  # 1-indexed
    ocr_reasons_by_page: list[OcrReasonEntry] = field(default_factory=list)


@dataclass
class PageResult:
    """Per-page markdown with provenance (0-indexed ``page``)."""

    page: int
    markdown: str = ""
    needs_ocr: bool = False
    ocr_reason: str | None = None
    src_tool: SrcTool = SrcTool.PDF_INSPECTOR
    page_bbox: list[float] | None = None  # [w, h] in pt; opt-in
    error: str | None = None  # set on per-page failure


@dataclass
class IR:
    """The whole document: metadata + per-page results."""

    metadata: Metadata = field(default_factory=Metadata)
    pages: list[PageResult] = field(default_factory=list)


def to_dict(ir: IR, *, include_page_bbox: bool = False) -> dict[str, Any]:
    """Serialize an IR to a plain dict (JSON-compatible).

    ``page_bbox`` is stripped unless ``include_page_bbox`` is set — it is
    opt-in per DESIGN §6/§7.
    """
    d = asdict(ir)
    d["metadata"]["ocr_reasons_by_page"] = [
        {"page": e["page"], "reasons": e["reasons"]}
        for e in d["metadata"]["ocr_reasons_by_page"]
    ]
    for p in d["pages"]:
        p["src_tool"] = p["src_tool"].value if isinstance(p["src_tool"], SrcTool) else p["src_tool"]
        if not include_page_bbox:
            p.pop("page_bbox", None)
    return d


def from_dict(d: dict[str, Any]) -> IR:
    """Rebuild an IR from :func:`to_dict` output (recursive inverse)."""
    md = d.get("metadata", {})
    metadata = Metadata(
        pdf_type=md.get("pdf_type", "unknown"),
        page_count=md.get("page_count", 0),
        confidence=md.get("confidence", 0.0),
        processing_time_ms=md.get("processing_time_ms", 0.0),
        title=md.get("title"),
        is_complex_layout=md.get("is_complex_layout", False),
        has_encoding_issues=md.get("has_encoding_issues", False),
        pages_with_tables=list(md.get("pages_with_tables", [])),
        pages_with_columns=list(md.get("pages_with_columns", [])),
        pages_needing_ocr=list(md.get("pages_needing_ocr", [])),
        ocr_reasons_by_page=[
            OcrReasonEntry(page=int(e["page"]), reasons=list(e.get("reasons", [])))
            for e in md.get("ocr_reasons_by_page", [])
        ],
    )
    pages = [
        PageResult(
            page=int(p["page"]),
            markdown=p.get("markdown", ""),
            needs_ocr=bool(p.get("needs_ocr", False)),
            ocr_reason=p.get("ocr_reason"),
            src_tool=SrcTool(p.get("src_tool", SrcTool.PDF_INSPECTOR.value)),
            page_bbox=list(p["page_bbox"]) if p.get("page_bbox") is not None else None,
            error=p.get("error"),
        )
        for p in d.get("pages", [])
    ]
    return IR(metadata=metadata, pages=pages)
