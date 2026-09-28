"""Legacy .doc inspector — office-oxide binding (ADR-0008).

``office-oxide==0.1.12`` (pinned core dep, lazy-imported per the
heavy-deps rule) parses the OLE2/CFB container in-process and hands us
its ``to_ir()`` dict; this module maps that dict onto the router's IR:

- one ``PageResult(page=0)`` for the whole document (docx precedent);
- oxide ``table`` elements -> ``TableBlock.rows`` (cell text = the
  docx rule: paragraphs joined with a single space), the pipe markdown
  generated from the *same* rows (``rows_to_markdown`` — exact match
  for the chunks subtraction);
- oxide ``heading`` elements -> ``HeadingBlock`` (levels clamped 1-6);
- oxide ``image`` elements (base64 in the parse tree) -> RT-08 sidecar
  placeholders ``![](images/page0001_image_XX.ext)`` + ``FigureBlock``;
  the bytes are written only by :func:`extract_doc_images` when the
  pipeline gets an ``output_dir`` — IR 0.8 doctrine: paths, never
  base64 (TKT-RT-08);
- ``Metadata.title`` from the oxide metadata block (author/created/
  modified are parsed by oxide but unmapped — IR ``Metadata`` has no
  fields for them).

Honest consequences (recorded in ADR-0008 / TKT-OI-04):

- headings are best-effort: oxide's F3 gap is 1 heading where
  LibreOffice detects 42; on F2 it yields 7 of 9 (ground truth 9);
- section titles are dropped: in the corpus each one duplicates the
  in-body heading that opens its section (counting both would double
  every title);
- list numbering: oxide gives items as text — we prefix ``"N. "`` /
  ``"-"`` ourselves so order survives (Word numbering is not text);
- corrupt/unknown CFB raises oxide's typed ``OfficeOxideError``,
  re-raised as ``ValueError`` (document-level failure, exit 3).

Page semantics: ``page_count == 1``, ``needs_ocr=False``,
``pdf_type="text_based"``, ``confidence=1.0`` — born-digital text.
"""

from __future__ import annotations

import base64
import binascii
import re
import time
from pathlib import Path
from typing import Any

from pdf_router.adapters.ooxml import rows_to_markdown
from pdf_router.ir import (
    IR,
    FigureBlock,
    HeadingBlock,
    Metadata,
    PageResult,
    SrcTool,
    TableBlock,
)

#: Sidecar kind token (same schema as docx/pdf: page{N}_{kind}_{idx}).
_IMAGE_KIND = "image"
_IMAGE_FORMATS = frozenset({"jpeg", "jpg", "png", "gif", "bmp", "tiff", "tif", "emf", "wmf"})


def _oxide() -> tuple[Any, type[Exception]]:
    """Lazy-import office-oxide (heavy dep: only when a .doc is parsed)."""
    from office_oxide import Document, OfficeOxideError

    return Document, OfficeOxideError


def _run_text(content: list | None) -> str:
    """Join inline runs (``text`` / ``line_break``) — docx-style plain prose.

    Bold/italic marks are dropped on purpose: the docx adapter emits
    plain prose too (structure blocks, not inline markup, carry the
    document).
    """
    parts: list[str] = []
    for node in content or []:
        if not isinstance(node, dict):
            continue
        kind = node.get("type")
        if kind == "text":
            parts.append(str(node.get("text") or ""))
        elif kind == "line_break":
            parts.append(" ")
    return "".join(parts).replace("\n", " ").replace("\t", " ").strip()


def _cell_text(cell: dict) -> str:
    """Table cell: paragraphs joined with a single space (pipe-safe)."""
    paras = [
        _run_text(p.get("content"))
        for p in cell.get("content") or []
        if isinstance(p, dict) and p.get("type") == "paragraph"
    ]
    text = " ".join(t for t in paras if t)
    return text.replace("\n", " ").replace("\t", " ").strip()


def _image_bytes(img: dict) -> bytes | None:
    """Decode the base64 payload of an oxide image element."""
    data = img.get("data")
    if not data or not isinstance(data, str):
        return None
    try:
        raw = base64.b64decode(data)
    except (binascii.Error, ValueError):
        return None
    return raw or None


def _image_ext(img: dict, raw: bytes) -> str:
    """Sidecar extension: oxide's ``format`` field, else magic sniff.

    Unknown payloads get an honest ``bin`` — the bytes are still written,
    the name does not pretend to be a raster it may not be.
    """
    fmt = str(img.get("format") or "").lower()
    fmt = re.sub(r"[^a-z0-9]", "", fmt)
    if fmt in _IMAGE_FORMATS:
        return fmt
    if raw.startswith(b"\xff\xd8"):
        return "jpeg"
    if raw.startswith(b"\x89PNG"):
        return "png"
    if raw.startswith((b"GIF87a", b"GIF89a")):
        return "gif"
    if raw.startswith(b"BM"):
        return "bmp"
    if raw.startswith(b"II*\x00") or raw.startswith(b"MM\x00*"):
        return "tiff"
    if raw.startswith(b"\x01\x00\x00\x00"):
        return "emf"
    if raw.startswith(b"\xd7\xcd\xc6\x9a"):
        return "wmf"
    return "bin"


def _figure(
    img: dict,
    blocks: list,
    md_parts: list,
    image_count: list[int],
    image_sink: list[dict] | None,
) -> None:
    """Emit FigureBlock + markdown placeholder for one image element.

    The index is consumed only when a placeholder is actually emitted —
    undecodable payloads skip without leaving a gap, so the sidecar pass
    (``enumerate`` over the sink) lines up 1:1 with the placeholder set.
    ``image_sink`` receives the very same element the moment a
    placeholder is emitted: one code path, no drift.
    """
    raw = _image_bytes(img)
    if raw is None:
        return  # undecodable payload: no placeholder, no block (honest skip)
    if image_sink is not None:
        image_sink.append(img)
    idx = image_count[0]
    image_count[0] = idx + 1
    ext = _image_ext(img, raw)
    blocks.append(FigureBlock(bbox=None, caption=None, image_index=idx))
    md_parts.append(f"![](images/page0001_{_IMAGE_KIND}_{idx:02d}.{ext})")


def _walk(
    node,
    *,
    md_parts: list[str],
    blocks: list,
    image_count: list[int],
    image_sink: list[dict] | None = None,
) -> None:
    """Document-order walk over the oxide element tree.

    ``image_count`` is a one-element list (the running index);
    ``image_sink`` collects the emitted image elements (sidecar pass).
    """
    if isinstance(node, list):
        for item in node:
            _walk(
                item,
                md_parts=md_parts,
                blocks=blocks,
                image_count=image_count,
                image_sink=image_sink,
            )
        return
    if not isinstance(node, dict):
        return
    kind = node.get("type")

    if kind == "heading":
        text = _run_text(node.get("content"))
        if not text:
            return
        try:
            level = int(node.get("level") or 1)
        except (TypeError, ValueError):
            level = 1
        level = min(6, max(1, level))
        md_parts.append("#" * level + " " + text)
        blocks.append(HeadingBlock(level=level, text=text, bbox=None, source="oxide_style"))

    elif kind == "paragraph":
        buf: list[str] = []
        for sub in node.get("content") or []:
            if isinstance(sub, dict) and sub.get("type") == "image":
                if buf:
                    md_parts.append(" ".join(buf).strip())
                    buf = []
                _figure(sub, blocks, md_parts, image_count, image_sink)
            else:
                text = _run_text([sub]) if isinstance(sub, dict) else ""
                if text:
                    buf.append(text)
        if buf:
            md_parts.append(" ".join(buf).strip())

    elif kind == "table":
        rows: list[list[str]] = []
        for row in node.get("rows") or []:
            cells = row.get("cells") or []
            rows.append([_cell_text(c) for c in cells if isinstance(c, dict)])
        if not rows:
            return
        width = max(len(r) for r in rows)
        rows = [r + [""] * (width - len(r)) for r in rows]
        md = rows_to_markdown(rows)
        blocks.append(
            TableBlock(
                markdown=md,
                rows=rows,
                header_rows=1 if len(rows) > 1 else 0,
                bbox=None,
            )
        )
        md_parts.append(md)

    elif kind == "list":
        ordered = bool(node.get("ordered"))
        for i, item in enumerate(node.get("items") or [], start=1):
            pieces = _run_text(item.get("content")) if isinstance(item, dict) else ""
            if not pieces:
                continue
            prefix = f"{i}. " if ordered else "- "
            md_parts.append(prefix + pieces)

    elif kind == "text":
        text = str(node.get("text") or "").replace("\n", " ").strip()
        if text:
            md_parts.append(text)

    elif kind == "image":
        _figure(node, blocks, md_parts, image_count, image_sink)

    # Unknown element kinds with nested content keep walking (forward
    # compatibility with oxide adding node types): descend into any
    # dict/list values under well-known containers.
    if kind not in {"heading", "paragraph", "table", "list", "text", "image"}:
        for value in node.values():
            if isinstance(value, (dict, list)):
                _walk(
                    value,
                    md_parts=md_parts,
                    blocks=blocks,
                    image_count=image_count,
                    image_sink=image_sink,
                )


def _collect_image_elements(data: dict) -> list[dict]:
    """The exact image set a placeholder was emitted for, document order.

    Reuses :func:`_walk` with throwaway md/block sinks — the placeholder
    set and the sidecar set cannot drift because they come from one path.
    """
    sink: list[dict] = []
    _walk(
        data.get("sections") or [],
        md_parts=[],
        blocks=[],
        image_count=[0],
        image_sink=sink,
    )
    return sink


def extract_ir(path: str) -> IR:
    """Legacy .doc -> IR via office-oxide (page 0 = the whole document).

    Pure: reads the container, writes nothing. Sidecar image extraction
    is :func:`extract_doc_images` (pipeline machinery, ``output_dir=``).

    Raises:
        ValueError: office-oxide rejected the container (corrupt CFB,
            unknown format) — a document-level failure (exit 3), not a
            format rejection (the gate already proved the OLE2 magic).
    """
    Document, OfficeOxideError = _oxide()
    t0 = time.perf_counter()
    if not Path(path).exists():
        # oxide wraps IO errors into OfficeOxideError; a missing file is
        # an IO error (same taxonomy as the docx adapter), not a
        # document-level failure.
        raise FileNotFoundError(path)
    try:
        with Document.open(path) as doc:
            data = doc.to_ir()
    except OfficeOxideError as exc:
        raise ValueError(f"malformed doc (office-oxide): {path}: {exc}") from exc
    if not isinstance(data, dict) or not isinstance(data.get("sections"), list):
        raise ValueError(f"malformed doc (office-oxide returned no sections): {path}")

    md_parts: list[str] = []
    blocks: list = []
    image_count = [0]
    for section in data.get("sections") or []:
        # Section titles intentionally dropped (docstring): they duplicate
        # the in-body heading that opens the section.
        _walk(section.get("elements") or [], md_parts=md_parts, blocks=blocks, image_count=image_count)

    meta = data.get("metadata") or {}
    title = str(meta.get("title") or "").strip() or None
    tables = sum(isinstance(b, TableBlock) for b in blocks)
    markdown = "\n\n".join(p for p in md_parts if p).strip()
    elapsed_ms = (time.perf_counter() - t0) * 1000.0

    metadata = Metadata(
        pdf_type="text_based",
        page_count=1,
        confidence=1.0,
        processing_time_ms=round(elapsed_ms, 1),
        title=title,
        is_complex_layout=False,
        pages_with_tables=[1] if tables else [],
        source_format="doc",
    )
    page = PageResult(
        page=0,
        markdown=markdown,
        needs_ocr=False,
        src_tool=SrcTool.DOC_ADAPTER,
        blocks=blocks,
        images=[],  # pipeline fills from extract_doc_images when output_dir is set
    )
    return IR(metadata=metadata, pages=[page])


# ---------------------------------------------------------------------------
# Sidecar extraction (pipeline machinery; called by core when output_dir set)
# ---------------------------------------------------------------------------


def extract_doc_images(page: PageResult, doc_path: str, output_dir: Path) -> list[str]:
    """Write every oxide image payload to the sidecar image directory.

    Mirrors :func:`pdf_router.extract_images.extract_page_images` for the
    .doc flavour: same sidecar schema
    (``<output_dir>/<doc-id>/images/page{N:04d}_{kind}_{idx:02d}.{ext}``,
    N 1-indexed — the whole document is page 1), same return contract
    (paths relative to ``output_dir``).

    The names come from the same deterministic walk the inspector used
    for its markdown placeholders, so ``![](images/page0001_image_00.jpeg)``
    in the markdown always corresponds to a written file (oxide's
    ``to_ir()`` is deterministic — probed).
    """
    from pdf_router.extract_images import doc_id_from_path, page_images_dir

    Document, _OfficeOxideError = _oxide()
    with Document.open(doc_path) as doc:
        images = _collect_image_elements(doc.to_ir())

    did = doc_id_from_path(doc_path)
    img_dir = page_images_dir(Path(output_dir), did)

    saved: list[str] = []
    for idx, img in enumerate(images):
        raw = _image_bytes(img)
        if raw is None:
            continue
        ext = _image_ext(img, raw)
        fname = f"page{page.page + 1:04d}_{_IMAGE_KIND}_{idx:02d}.{ext}"
        fpath = img_dir / fname
        fpath.write_bytes(raw)
        saved.append(str(fpath.relative_to(output_dir)))
    return saved


# ---------------------------------------------------------------------------
# --info payload (one door: each format answers from its own inspector)
# ---------------------------------------------------------------------------


def doc_info(path: str) -> dict:
    """Metadata summary for ``--info`` — derived from the same inspector.

    One parser, no drift: the counts come from the IR the pipeline would
    build, not from a second lighter-weight probe.
    """
    ir = extract_ir(path)
    page = ir.pages[0]
    return {
        "source_format": ir.metadata.source_format,
        "pdf_type": ir.metadata.pdf_type,
        "page_count": ir.metadata.page_count,
        "confidence": ir.metadata.confidence,
        "title": ir.metadata.title,
        "tables": sum(isinstance(b, TableBlock) for b in page.blocks),
        "images": sum(isinstance(b, FigureBlock) for b in page.blocks),
    }
