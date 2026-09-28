"""Minimal HTML ingest — stdlib tag-strip (ADR-0008 / TKT-OI-04 ruling 3).

The corpus holds 7 law files saved with a ``.doc`` extension that are
HTML documents (190-ФЗ, 7-ФЗ, …). The gate sniffs their magic and routes
them here; a full HTML inspector (links, nesting, structure) is out of
scope for this map, so this adapter claims exactly one thing: **tag-
stripped prose paragraphs with honest metadata** —

- ``html.parser.HTMLParser`` (stdlib) walks the markup; ``<script>``,
  ``<style>`` and the rest of ``<head>`` never reach the output (JS/CSS
  is not document text; the ``<title>`` is captured as
  ``Metadata.title``);
- block elements split paragraphs; ``<h1>``–``<h6>`` become
  ``HeadingBlock``s (level 1-6, ``source="html_tag"``) — the one piece
  of structure a tag-strip can honestly recover;
- one ``PageResult(page=0)``, ``source_format="html"``,
  ``SrcTool.HTML_ADAPTER``, ``needs_ocr=False``, no tables (structure
  beyond headings is *not* claimed), no images, no OCR pass.

Honest consequences: inline tags and emphasis vanish (plain prose, like
the docx adapter); HTML comments are dropped; ``<ol>`` counters and link
targets are dropped (the link *text* is kept — numbering that exists
only in a counter is lost); undecodable bytes decode with
``errors="replace"`` and flip ``Metadata.has_encoding_issues`` when
U+FFFD lands in the text.
"""

from __future__ import annotations

import time
from html.parser import HTMLParser
from pathlib import Path

from pdf_router.ir import IR, HeadingBlock, Metadata, PageResult, SrcTool

#: Tags whose payloads are not document text.
_SKIP_TAGS = frozenset({"script", "style", "noscript", "template", "iframe", "svg"})
#: Block-level tags that end the current paragraph (start or end).
_BLOCK_TAGS = frozenset(
    {
        "p",
        "div",
        "li",
        "ul",
        "ol",
        "dl",
        "dt",
        "dd",
        "tr",
        "table",
        "section",
        "article",
        "aside",
        "header",
        "footer",
        "main",
        "nav",
        "blockquote",
        "pre",
        "hr",
        "address",
        "figure",
        "figcaption",
        "form",
        "fieldset",
        "br",
    }
)
_HEADING_TAGS = frozenset({"h1", "h2", "h3", "h4", "h5", "h6"})


def _collapse(text: str) -> str:
    """Collapse every whitespace run (incl. U+00A0 from ``&nbsp;``)."""
    return " ".join(text.split())


class _ProseExtractor(HTMLParser):
    """Collects prose paragraphs, headings and the document title."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.md_parts: list[str] = []
        self.blocks: list = []
        self.title: str | None = None
        self._skip_depth = 0
        self._in_head = False
        self._in_title = False
        self._title_buf: list[str] = []
        self._buf: list[str] = []
        self._heading_level: int | None = None

    # -- flushing -----------------------------------------------------------

    def _flush_paragraph(self) -> None:
        if self._heading_level is not None:
            self._flush_heading()
            return
        text = _collapse("".join(self._buf))
        self._buf.clear()
        if text:
            self.md_parts.append(text)

    def _flush_heading(self) -> None:
        text = _collapse("".join(self._buf))
        self._buf.clear()
        level = self._heading_level
        self._heading_level = None
        if not text or level is None:
            return
        self.md_parts.append("#" * level + " " + text)
        self.blocks.append(
            HeadingBlock(level=level, text=text, bbox=None, source="html_tag")
        )

    # -- parser hooks -------------------------------------------------------

    def handle_starttag(self, tag: str, attrs) -> None:
        if tag in _SKIP_TAGS:
            self._skip_depth += 1
            return
        if tag == "head":
            self._in_head = True
            return
        if tag == "title":
            self._flush_paragraph()
            self._in_title = True
            return
        if self._skip_depth or (self._in_head and not self._in_title):
            return
        if tag in _HEADING_TAGS:
            self._flush_paragraph()
            self._heading_level = int(tag[1])
            return
        if tag == "br":
            self._flush_paragraph()
            return
        if tag in _BLOCK_TAGS:
            self._flush_paragraph()

    def handle_endtag(self, tag: str) -> None:
        if tag in _SKIP_TAGS:
            self._skip_depth = max(0, self._skip_depth - 1)
            return
        if tag == "head":
            self._in_head = False
            return
        if tag == "title":
            if self._in_title:
                self.title = _collapse("".join(self._title_buf)) or None
            self._in_title = False
            self._title_buf.clear()
            return
        if self._skip_depth or (self._in_head and not self._in_title):
            return
        if tag in _HEADING_TAGS:
            self._flush_heading()
            return
        if tag in _BLOCK_TAGS:
            self._flush_paragraph()

    def handle_data(self, data: str) -> None:
        if self._in_title:
            self._title_buf.append(data)
            return
        if self._skip_depth or self._in_head:
            return
        self._buf.append(data)

    def handle_comment(self, data: str) -> None:
        # Comments never reach the output (README: no markup noise).
        return


def extract_ir(path: str) -> IR:
    """HTML file -> IR: one page of tag-stripped prose (page 0).

    Pure: reads and parses, writes nothing.

    Raises:
        FileNotFoundError: the path does not exist.
    """
    t0 = time.perf_counter()
    raw = Path(path).read_bytes()
    if raw.startswith(b"\xef\xbb\xbf"):
        raw = raw[3:]
    text = raw.decode("utf-8", errors="replace")

    parser = _ProseExtractor()
    parser.feed(text)
    parser.close()
    parser._flush_paragraph()

    markdown = "\n\n".join(parser.md_parts).strip()
    elapsed_ms = (time.perf_counter() - t0) * 1000.0
    metadata = Metadata(
        pdf_type="text_based",
        page_count=1,
        confidence=1.0,
        processing_time_ms=round(elapsed_ms, 1),
        title=parser.title,
        is_complex_layout=False,
        has_encoding_issues="�" in markdown,
        pages_with_tables=[],  # structure beyond headings is not claimed
        source_format="html",
    )
    page = PageResult(
        page=0,
        markdown=markdown,
        needs_ocr=False,
        src_tool=SrcTool.HTML_ADAPTER,
        blocks=parser.blocks,
        images=[],
    )
    return IR(metadata=metadata, pages=[page])


# ---------------------------------------------------------------------------
# --info payload (one door: same parser, no drift)
# ---------------------------------------------------------------------------


def html_info(path: str) -> dict:
    """Metadata summary for ``--info`` — derived from the same inspector."""
    ir = extract_ir(path)
    return {
        "source_format": ir.metadata.source_format,
        "pdf_type": ir.metadata.pdf_type,
        "page_count": ir.metadata.page_count,
        "confidence": ir.metadata.confidence,
        "title": ir.metadata.title,
        "tables": 0,
        "images": 0,
    }
