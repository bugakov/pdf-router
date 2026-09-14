"""Native docx inspector — stdlib only (ADR-0003).

docx is a zip of XML; the structure a PDF pipeline must *recover* with
heuristics (font-size headings, re-parsed markdown tables) is stated here
*explicitly*: ``w:pStyle`` -> exact heading levels, ``w:tbl`` -> exact 2D
rows, ``word/media/*`` -> sidecar images. Zero dependencies beyond the
standard library (zipfile + xml.etree), milliseconds per document.

Page semantics (TKT-DX-02): docx is pageless — the whole document is
page 0. The IR page vocabulary is preserved honestly: ``pdf_type``
describes how text was obtained ("text_based", confidence 1.0 — no OCR
happened), ``Metadata.source_format`` says the container was docx.

Known limitations (accepted in ADR-0003, none present in the corpus):
- no OCR path — a scanned docx (image-only) yields near-empty text;
- no pagination — the document is one IR page;
- nested tables are flattened into their parent cell's text;
- EMF/WMF media are stored verbatim (no stdlib conversion).

Style resolution rule (the corpus obfuscates styleIds — "a3", "ab"):
never match raw IDs. Resolve through ``word/styles.xml``: direct
``w:outlineLvl`` first, then the built-in name (``heading N``), walking
the ``basedOn`` chain with a cycle guard.
"""

from __future__ import annotations

import posixpath
import re
import time
import zipfile
from pathlib import Path, PurePosixPath
from xml.etree import ElementTree as ET

from pdf_router.ir import (
    FigureBlock,
    FormulaBlock,
    HeadingBlock,
    IR,
    Metadata,
    PageResult,
    SrcTool,
    TableBlock,
)

_W = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"
_R = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"
_A = "http://schemas.openxmlformats.org/drawingml/2006/main"
_V = "urn:schemas-microsoft-com:vml"
_DC = "http://purl.org/dc/elements/1.1/"

_TAG_P = f"{{{_W}}}p"
_TAG_TBL = f"{{{_W}}}tbl"
_TAG_SDT = f"{{{_W}}}sdt"
_TAG_SDT_CONTENT = f"{{{_W}}}sdtContent"
_TAG_TC = f"{{{_W}}}tc"
_TAG_TR = f"{{{_W}}}tr"
_TAG_TCPR = f"{{{_W}}}tcPr"
_TAG_PPR = f"{{{_W}}}pPr"
_TAG_PSTYLE = f"{{{_W}}}pStyle"
_TAG_OUTLINE = f"{{{_W}}}outlineLvl"
_TAG_T = f"{{{_W}}}t"
_TAG_TAB = f"{{{_W}}}tab"
_TAG_BR = f"{{{_W}}}br"
_TAG_CR = f"{{{_W}}}cr"
_TAG_GRIDSPAN = f"{{{_W}}}gridSpan"
_TAG_VMERGE = f"{{{_W}}}vMerge"
_TAG_BLIP = f"{{{_A}}}blip"
_TAG_IMAGEDATA = f"{{{_V}}}imagedata"
_ATTR_VAL = f"{{{_W}}}val"
_ATTR_EMBED = f"{{{_R}}}embed"
_ATTR_RID = f"{{{_R}}}id"

#: Sidecar naming (TKT-RT-12 schema, docx flavour): page is 1-indexed like
#: the PDF sidecars, ``image`` is the kind, the index counts unique media
#: parts in canonical order (body document order, then headers/footers).
_IMAGE_KIND = "image"

_HEADING_NAME_RE = re.compile(r"^heading\s*(\d)$", re.IGNORECASE)
_CAPTION_RE = re.compile(r"^\s*(?:Рис|Fig|Табл)\.?\s*\d", re.IGNORECASE)
_MAX_HEADING = 6  # markdown vocabulary; outlineLvl 6-8 collapses to H6


# ---------------------------------------------------------------------------
# Package plumbing
# ---------------------------------------------------------------------------


def _load_rels(zf: zipfile.ZipFile, part: str) -> dict[str, str]:
    """Relationship map for one package part: rId -> internal target.

    ``part`` is like ``word/document.xml``; its rels live at
    ``word/_rels/document.xml.rels``. External targets (hyperlinks) and
    missing rels files are skipped.
    """
    base, name = part.rsplit("/", 1)
    rels_path = f"{base}/_rels/{name}.rels"
    try:
        root = ET.fromstring(zf.read(rels_path))
    except (KeyError, ET.ParseError):
        return {}
    out: dict[str, str] = {}
    for rel in root:
        rid = rel.get("Id")
        target = rel.get("Target")
        if not rid or not target or rel.get("TargetMode") == "External":
            continue
        if target.startswith("/"):
            resolved = target.lstrip("/")
        else:
            resolved = posixpath.normpath(f"{base}/{target}")
        out[rid] = resolved
    return out


def _read_xml(zf: zipfile.ZipFile, part: str) -> ET.Element | None:
    try:
        return ET.fromstring(zf.read(part))
    except (KeyError, ET.ParseError):
        return None


def _image_ext(target: str) -> str:
    ext = PurePosixPath(target).suffix.lower().lstrip(".")
    return ext or "png"


def _image_sidecar_name(index: int) -> str:
    """Doc-id-free, portable placeholder path (README: docx images).

    The file itself lands under ``<out>/<doc-id>/images/`` (the sidecar
    contract); the markdown placeholder stays doc-id-free so the rendered
    material survives the document being moved. The extension is appended
    by the caller from the media part's own suffix.
    """
    return f"images/page0001_{_IMAGE_KIND}_{index:02d}"


# ---------------------------------------------------------------------------
# Styles -> heading levels (never match raw styleIds)
# ---------------------------------------------------------------------------


def _load_styles(zf: zipfile.ZipFile) -> dict[str, dict]:
    """styleId -> {name, outline (0-8 or None), based_on} from styles.xml."""
    root = _read_xml(zf, "word/styles.xml")
    if root is None:
        return {}
    styles: dict[str, dict] = {}
    for st in root.findall(f"{{{_W}}}style"):
        sid = st.get(f"{{{_W}}}styleId")
        if not sid:
            continue
        name_el = st.find(f"{{{_W}}}name")
        outline_el = st.find(f"{{{_W}}}pPr/{{{_W}}}outlineLvl")
        based_el = st.find(f"{{{_W}}}basedOn")
        outline: int | None = None
        if outline_el is not None:
            try:
                outline = int(outline_el.get(_ATTR_VAL, ""))
            except ValueError:
                outline = None
        styles[sid] = {
            "name": (name_el.get(_ATTR_VAL) if name_el is not None else None) or "",
            "outline": outline,
            "based_on": based_el.get(_ATTR_VAL) if based_el is not None else None,
        }
    return styles


def _style_heading_level(style_id: str | None, styles: dict[str, dict]) -> int | None:
    """Heading level (1-6) for a styleId, or None.

    Walks the basedOn chain (cycle-guarded): the first style carrying an
    ``outlineLvl`` (0-8) or a built-in ``heading N`` name decides. Localized
    custom styles usually lack the English name but set outlineLvl — that
    is the signal Word itself uses for the outline pane.
    """
    seen: set[str] = set()
    sid = style_id
    while sid and sid in styles and sid not in seen:
        seen.add(sid)
        info = styles[sid]
        outline = info["outline"]
        if outline is not None and 0 <= outline <= 8:
            return min(outline + 1, _MAX_HEADING)
        m = _HEADING_NAME_RE.match(info["name"])
        if m:
            return min(int(m.group(1)), _MAX_HEADING)
        if info["name"].lower() == "title":
            return 1
        sid = info["based_on"]
    return None


# ---------------------------------------------------------------------------
# Text / image extraction primitives
# ---------------------------------------------------------------------------


def _para_text(p: ET.Element) -> str:
    """Concatenate a paragraph's text: w:t runs, tabs, line breaks.

    Descends into everything (hyperlinks, smart tags, text boxes) — text
    boxes are rare in the corpus and inlining beats dropping. Field
    instructions (``w:instrText``) and tracked deletions (``w:delText``)
    are not ``w:t`` and are excluded naturally.
    """
    parts: list[str] = []
    for node in p.iter():
        if node.tag == _TAG_T:
            parts.append(node.text or "")
        elif node.tag == _TAG_TAB:
            parts.append("\t")
        elif node.tag in (_TAG_BR, _TAG_CR):
            parts.append("\n")
    return "".join(parts)


def _para_image_targets(p: ET.Element, rels: dict[str, str]) -> list[str]:
    """Media targets referenced by a paragraph (DrawingML + legacy VML)."""
    targets: list[str] = []
    for node in p.iter():
        rid = None
        if node.tag == _TAG_BLIP:
            rid = node.get(_ATTR_EMBED)
        elif node.tag == _TAG_IMAGEDATA:
            rid = node.get(_ATTR_RID)
        if rid and rid in rels:
            targets.append(rels[rid])
    return targets


def _cell_text(tc: ET.Element) -> str:
    """Cell text: paragraphs joined with a single space (pipe-safe).

    Nested tables are flattened into the cell text (their w:p descendants
    are picked up by the same iteration). Newlines/tabs collapse so a cell
    is always one line in both ``rows`` and the pipe markdown.
    """
    paras = [t.strip() for t in (_para_text(p) for p in tc.iter(_TAG_P))]
    text = " ".join(t for t in paras if t)
    return text.replace("\t", " ").replace("\n", " ").strip()


# ---------------------------------------------------------------------------
# Tables: exact 2D rows with gridSpan / vMerge resolution
# ---------------------------------------------------------------------------


def _table_rows(tbl: ET.Element) -> list[list[str]]:
    """Exact 2D cell grid from a ``w:tbl``.

    - ``gridSpan``: the cell value is repeated across the spanned columns
      (each covered column self-sufficient for retrieval);
    - ``vMerge``: continuation cells **repeat the restart value** (TKT-DX-05)
      instead of going empty — a row must survive retrieval on its own;
    - ragged rows are padded with "" to the table width.
    """
    open_merge: dict[int, str] = {}  # grid column -> value to repeat
    rows: list[list[str]] = []
    for tr in tbl.findall(_TAG_TR):
        row: list[str] = []
        col = 0
        for tc in tr.findall(_TAG_TC):
            gridspan, vmerge = 1, None
            tcpr = tc.find(_TAG_TCPR)
            if tcpr is not None:
                gs = tcpr.find(_TAG_GRIDSPAN)
                if gs is not None:
                    try:
                        gridspan = max(1, int(gs.get(_ATTR_VAL, "1")))
                    except ValueError:
                        gridspan = 1
                vm = tcpr.find(_TAG_VMERGE)
                if vm is not None:
                    vmerge = vm.get(_ATTR_VAL) or "continue"
            text = _cell_text(tc)
            if vmerge == "continue":
                text = open_merge.get(col, text)
            else:
                open_merge[col] = text
            row.extend([text] * gridspan)
            col += gridspan
        rows.append(row)
    width = max((len(r) for r in rows), default=0)
    return [r + [""] * (width - len(r)) for r in rows]


def _rows_to_markdown(rows: list[list[str]]) -> str:
    """Pipe-table markdown from exact rows (header row + separator)."""
    if not rows:
        return ""
    width = max(len(r) for r in rows)

    def esc(s: str) -> str:
        return s.replace("|", "\\|").strip()

    def line(cells: list[str]) -> str:
        padded = cells + [""] * (width - len(cells))
        return "| " + " | ".join(esc(c) for c in padded) + " |"

    lines = [line(rows[0]), "|" + "|".join([" --- "] * width) + "|"]
    lines.extend(line(r) for r in rows[1:])
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Canonical image order (single source for placeholders AND sidecar files)
# ---------------------------------------------------------------------------


def _iter_docx_image_targets(zf: zipfile.ZipFile) -> list[str]:
    """Unique media part names in canonical order.

    Body images first (depth-first document order across DrawingML blips
    and legacy VML imagedata), then header/footer images (parts sorted by
    name). Duplicates (one logo referenced from three headers) collapse to
    the first occurrence. The docx inspector derives markdown placeholders
    from this list; the sidecar extractor writes files from it — the two
    cannot drift.
    """
    ordered: list[str] = []
    seen: set[str] = set()

    def walk(part: str) -> None:
        root = _read_xml(zf, part)
        if root is None:
            return
        rels = _load_rels(zf, part)
        for node in root.iter():
            rid = None
            if node.tag == _TAG_BLIP:
                rid = node.get(_ATTR_EMBED)
            elif node.tag == _TAG_IMAGEDATA:
                rid = node.get(_ATTR_RID)
            target = rels.get(rid) if rid else None
            if target and target not in seen:
                seen.add(target)
                ordered.append(target)

    walk("word/document.xml")
    for name in sorted(zf.namelist()):
        if re.fullmatch(r"word/(header|footer)\d*\.xml", name):
            walk(name)
    return ordered


# ---------------------------------------------------------------------------
# The inspector: path -> IR
# ---------------------------------------------------------------------------


def _walk_blocks(
    elements: list[ET.Element],
    *,
    styles: dict[str, dict],
    rels: dict[str, str],
    target_to_index: dict[str, int],
    md_parts: list[str],
    blocks: list[HeadingBlock | TableBlock | FigureBlock | FormulaBlock],
    paragraphs: list[str],
) -> int:
    """Emit markdown chunks + structural blocks for body-level elements.

    Returns the number of tables emitted. ``paragraphs`` collects non-empty
    paragraph texts in order (caption lookahead for figures).
    """
    tables = 0
    for el in elements:
        if el.tag == _TAG_P:
            text = _para_text(el).strip()
            for target in _para_image_targets(el, rels):
                idx = target_to_index.get(target)
                if idx is None:
                    continue
                blocks.append(FigureBlock(bbox=None, caption=None, image_index=idx))
                md_parts.append(f"![]({_image_sidecar_name(idx)}.{_image_ext(target)})")
            if not text:
                continue
            paragraphs.append(text)
            level = _para_heading_level(el, styles)
            if level is not None:
                md_parts.append("#" * level + " " + text)
                blocks.append(
                    HeadingBlock(level=level, text=text, bbox=None, source="docx_style")
                )
            else:
                md_parts.append(text)
        elif el.tag == _TAG_TBL:
            rows = _table_rows(el)
            if not rows:
                continue
            tables += 1
            blocks.append(
                TableBlock(
                    markdown=_rows_to_markdown(rows),
                    rows=rows,
                    header_rows=1 if len(rows) > 1 else 0,
                    bbox=None,
                )
            )
            md_parts.append(_rows_to_markdown(rows))
            # Images inside table cells: extracted as sidecars, placeholder
            # deferred to the document tail (a pipe cell cannot hold one).
            for tc in el.iter(_TAG_TC):
                for target in _para_image_targets(tc, rels):
                    idx = target_to_index.get(target)
                    if idx is not None:
                        blocks.append(
                            FigureBlock(bbox=None, caption=None, image_index=idx)
                        )
                        md_parts.append(
                            f"![]({_image_sidecar_name(idx)}.{_image_ext(target)})"
                        )
        elif el.tag == _TAG_SDT:
            content = el.find(f"{{{_W}}}sdtContent")
            if content is not None:
                tables += _walk_blocks(
                    list(content),
                    styles=styles,
                    rels=rels,
                    target_to_index=target_to_index,
                    md_parts=md_parts,
                    blocks=blocks,
                    paragraphs=paragraphs,
                )
    return tables


def _para_heading_level(p: ET.Element, styles: dict[str, dict]) -> int | None:
    """Direct ``w:outlineLvl`` first, then the pStyle chain."""
    ppr = p.find(_TAG_PPR)
    if ppr is not None:
        outline_el = ppr.find(_TAG_OUTLINE)
        if outline_el is not None:
            try:
                outline = int(outline_el.get(_ATTR_VAL, ""))
            except ValueError:
                outline = None
            if outline is not None and 0 <= outline <= 8:
                return min(outline + 1, _MAX_HEADING)
        style_el = ppr.find(_TAG_PSTYLE)
        if style_el is not None:
            level = _style_heading_level(style_el.get(_ATTR_VAL), styles)
            if level is not None:
                return level
    return None


def _attach_captions(figures: list[FigureBlock], paragraphs: list[str]) -> None:
    """Nearest following paragraph matching ``Рис. N`` / ``Fig. N`` (advisory).

    Figures are appended to ``blocks`` in document order; a single forward
    scan with a moving start index gives each figure the first caption-like
    paragraph at or after its own position — the conventional placement of
    ``Рис. N`` below the image. Advisory metadata: the sidecar file is the
    payload, this is a bonus for the RAG index.
    """
    para_pos = 0
    for fig in figures:
        for text in paragraphs[para_pos:]:
            if _CAPTION_RE.match(text):
                fig.caption = text.strip()
                para_pos = paragraphs.index(text, para_pos) + 1
                break


def _core_title(zf: zipfile.ZipFile) -> str | None:
    root = _read_xml(zf, "docProps/core.xml")
    if root is None:
        return None
    title = root.find(f"{{{_DC}}}title")
    if title is not None and title.text and title.text.strip():
        return title.text.strip()
    return None


def extract_ir(path: str) -> IR:
    """Native docx -> IR (page 0 = the whole document; ADR-0003).

    Pure: reads the package, writes nothing. Sidecar image extraction is
    :func:`extract_docx_images` (pipeline machinery, ``output_dir=``).

    Raises:
        ValueError: the package is a zip with ``word/document.xml`` but the
            XML inside is unreadable — a document-level failure (exit 3),
            not a format rejection.
    """
    t0 = time.perf_counter()
    try:
        zf = zipfile.ZipFile(path)
    except zipfile.BadZipFile as exc:
        raise ValueError(f"malformed docx (bad zip): {path}: {exc}") from exc
    with zf:
        root = _read_xml(zf, "word/document.xml")
        if root is None:
            raise ValueError(f"malformed docx (unreadable word/document.xml): {path}")
        body = root.find(f"{{{_W}}}body")
        if body is None:
            raise ValueError(f"malformed docx (no w:body): {path}")

        styles = _load_styles(zf)
        rels = _load_rels(zf, "word/document.xml")
        image_targets = _iter_docx_image_targets(zf)
        target_to_index = {t: i for i, t in enumerate(image_targets)}

        md_parts: list[str] = []
        blocks: list[HeadingBlock | TableBlock | FigureBlock | FormulaBlock] = []
        paragraphs: list[str] = []
        tables = _walk_blocks(
            list(body),
            styles=styles,
            rels=rels,
            target_to_index=target_to_index,
            md_parts=md_parts,
            blocks=blocks,
            paragraphs=paragraphs,
        )
        _attach_captions([b for b in blocks if isinstance(b, FigureBlock)], paragraphs)

        # Header/footer images (e.g. logos, stamps): extracted and listed at
        # the document tail — they belong to no body position.
        for name in sorted(zf.namelist()):
            if not re.fullmatch(r"word/(header|footer)\d*\.xml", name):
                continue
            part_rels = _load_rels(zf, name)
            part_root = _read_xml(zf, name)
            if part_root is None:
                continue
            for target in _para_image_targets(part_root, part_rels):
                idx = target_to_index.get(target)
                if idx is not None:
                    blocks.append(
                        FigureBlock(bbox=None, caption=None, image_index=idx)
                    )
                    md_parts.append(
                        f"![]({_image_sidecar_name(idx)}.{_image_ext(target)})"
                    )

        title = _core_title(zf)

    markdown = "\n\n".join(md_parts).strip()
    elapsed_ms = (time.perf_counter() - t0) * 1000.0
    metadata = Metadata(
        pdf_type="text_based",
        page_count=1,
        confidence=1.0,
        processing_time_ms=round(elapsed_ms, 1),
        title=title,
        is_complex_layout=False,
        pages_with_tables=[1] if tables else [],
        source_format="docx",
    )
    page = PageResult(
        page=0,
        markdown=markdown,
        needs_ocr=False,
        src_tool=SrcTool.DOCX_ADAPTER,
        blocks=blocks,
        images=[],  # pipeline fills from extract_docx_images when output_dir is set
    )
    return IR(metadata=metadata, pages=[page])


# ---------------------------------------------------------------------------
# Sidecar extraction (pipeline machinery; called by core when output_dir set)
# ---------------------------------------------------------------------------


def extract_docx_images(
    page: PageResult, docx_path: str, output_dir: Path
) -> list[str]:
    """Write every media part of the docx to the sidecar image directory.

    Mirrors :func:`pdf_router.extract_images.extract_page_images` for the
    docx flavour: same sidecar schema
    (``<output_dir>/<doc-id>/images/page{N:04d}_{kind}_{idx:02d}.{ext}``,
    N 1-indexed — the whole document is page 1), same return contract
    (paths relative to ``output_dir``). Media bytes are copied verbatim —
    EMF/WMF included, no re-encoding.

    The names come from the same canonical enumeration the inspector used
    for its markdown placeholders, so ``![](images/page0001_image_00.png)``
    in the markdown always corresponds to a written file.
    """
    # Lazy import: keeps this module importable without the pipeline and
    # reuses the exact doc-id/sidecar conventions of the PDF extractor.
    from pdf_router.extract_images import doc_id_from_path, page_images_dir

    did = doc_id_from_path(docx_path)
    img_dir = page_images_dir(Path(output_dir), did)

    saved: list[str] = []
    with zipfile.ZipFile(docx_path) as zf:
        for idx, target in enumerate(_iter_docx_image_targets(zf)):
            try:
                data = zf.read(target)
            except KeyError:
                continue
            fname = f"page{page.page + 1:04d}_{_IMAGE_KIND}_{idx:02d}.{_image_ext(target)}"
            fpath = img_dir / fname
            fpath.write_bytes(data)
            saved.append(str(fpath.relative_to(output_dir)))
    return saved


# ---------------------------------------------------------------------------
# --info payload (one door: PDF classifies, docx inspects — TKT-DX-06)
# ---------------------------------------------------------------------------


def docx_info(path: str) -> dict:
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
