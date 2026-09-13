"""docx adapter tests (ADR-0003): synthetic control + golden corpus volumes.

Two layers, both fast (the native parse is milliseconds — TKT-DX-05):

- **Synthetic control**: a hand-built OOXML package pinning every structural
  claim of the adapter — style resolution through styles.xml (obfuscated
  styleIds, basedOn chains), direct outlineLvl, exact 2D table rows with
  gridSpan/vMerge repeat semantics, body + header images, caption
  attachment, title from core.xml, sidecar placeholder/file agreement,
  IR round-trip, and the end-to-end facade/CLI doors.

- **Golden volumes**: the two project-documentation tomes from the real
  corpus (ИГИ 00845.2024.07, ПР ИГМИ Урицкого) — the quality bar TKT-DX-05
  locked. Skipped (not failed) when the corpus is not mounted.
"""

from __future__ import annotations

import base64
import io
import json
import time
import zipfile
from pathlib import Path

import pytest

from pdf_router import (
    convert,
    from_dict,
    process_pdf,
    render_chunks,
    render_markdown,
    to_dict,
)
from pdf_router.adapters.docx import (
    docx_info,
    extract_docx_images,
    extract_ir,
)
from pdf_router.ir import FigureBlock, HeadingBlock, TableBlock

# ---------------------------------------------------------------------------
# Synthetic OOXML builder
# ---------------------------------------------------------------------------

_W_NS = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"
_R_NS = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"
_A_NS = "http://schemas.openxmlformats.org/drawingml/2006/main"
_PIC_NS = "http://schemas.openxmlformats.org/drawingml/2006/picture"
_WP_NS = "http://schemas.openxmlformats.org/drawingml/2006/wordprocessingDrawing"
_V_NS = "urn:schemas-microsoft-com:vml"
_CT_NS = "http://schemas.openxmlformats.org/package/2006/content-types"
_REL_NS = "http://schemas.openxmlformats.org/package/2006/relationships"
_DC_NS = "http://purl.org/dc/elements/1.1/"
_CP_NS = "http://schemas.openxmlformats.org/package/2006/metadata/core-properties"

# A real 1x1 transparent PNG — media bytes are copied verbatim by the
# sidecar extractor, so the file must be a valid image.
_PNG_1PX = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJ"
    "AAAADUlEQVR42mNkYPhfDwAChwGA60e6kgAAAABJRU5ErkJggg=="
)


def _p(text: str, *, style: str | None = None, outline: int | None = None) -> str:
    ppr = ""
    if style:
        ppr += f'<w:pStyle w:val="{style}"/>'
    if outline is not None:
        ppr += f'<w:outlineLvl w:val="{outline}"/>'
    ppr = f"<w:pPr>{ppr}</w:pPr>" if ppr else ""
    return (
        f'<w:p>{ppr}<w:r><w:t xml:space="preserve">{text}</w:t></w:r></w:p>'
    )


def _p_image(rid: str) -> str:
    """Paragraph holding a DrawingML image (minimal but realistic shape)."""
    return (
        "<w:p><w:r><w:drawing>"
        f'<wp:inline><wp:extent cx="100" cy="100"/><a:graphic>'
        f'<a:graphicData uri="{_PIC_NS}">'
        f'<pic:pic><pic:blipFill><a:blip r:embed="{rid}"/></pic:blipFill></pic:pic>'
        "</a:graphicData></a:graphic></wp:inline>"
        "</w:drawing></w:r></w:p>"
    )


def _p_vml_image(rid: str) -> str:
    """Paragraph holding a legacy VML image (header-logo flavour)."""
    return (
        f'<w:p><w:r><w:pict><v:shape><v:imagedata r:id="{rid}"/>'
        "</v:shape></w:pict></w:r></w:p>"
    )


def _cell(text: str, *, gridspan: int | None = None, vmerge: str | None = None) -> str:
    tcpr = ""
    if gridspan is not None:
        tcpr += f'<w:gridSpan w:val="{gridspan}"/>'
    if vmerge == "restart":
        tcpr += '<w:vMerge w:val="restart"/>'
    elif vmerge == "continue":
        tcpr += "<w:vMerge/>"  # no val = continuation
    tcpr = f"<w:tcPr>{tcpr}</w:tcPr>" if tcpr else "<w:tcPr/>"
    inner = f"<w:p><w:r><w:t>{text}</w:t></w:r></w:p>" if text else "<w:p/>"
    return f"<w:tc>{tcpr}{inner}</w:tc>"


def _row(*cells: str) -> str:
    return "<w:tr>" + "".join(cells) + "</w:tr>"


def _build_docx(
    tmp_path: Path,
    *,
    body: str,
    styles: str = "",
    header: str | None = None,
    title: str | None = None,
    media: dict[str, bytes] | None = None,
    doc_rels: str = "",
    header_rels: str = "",
    name: str = "control.docx",
) -> Path:
    """Assemble a structurally-complete OOXML package (the synthetic control).

    Only parts that are actually referenced get written; the adapter must
    tolerate absent styles/headers/core-props (they are optional in OOXML).
    """
    content_types = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        f'<Types xmlns="{_CT_NS}">'
        '<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>'
        '<Default Extension="xml" ContentType="application/xml"/>'
        '<Default Extension="png" ContentType="image/png"/>'
        '<Override PartName="/word/document.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml"/>'
        + (
            '<Override PartName="/docProps/core.xml" ContentType="application/vnd.openxmlformats-package.core-properties+xml"/>'
            if title
            else ""
        )
        + "</Types>"
    )
    root_rels = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        f'<Relationships xmlns="{_REL_NS}">'
        '<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="word/document.xml"/>'
        + (
            '<Relationship Id="rId2" Type="http://schemas.openxmlformats.org/package/2006/relationships/metadata/core-properties" Target="docProps/core.xml"/>'
            if title
            else ""
        )
        + "</Relationships>"
    )
    document = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        f'<w:document xmlns:w="{_W_NS}" xmlns:r="{_R_NS}" xmlns:a="{_A_NS}" '
        f'xmlns:pic="{_PIC_NS}" xmlns:wp="{_WP_NS}" xmlns:v="{_V_NS}">'
        f"<w:body>{body}</w:body></w:document>"
    )
    parts: dict[str, str | bytes] = {
        "[Content_Types].xml": content_types,
        "_rels/.rels": root_rels,
        "word/document.xml": document,
    }
    if styles:
        parts["word/styles.xml"] = (
            '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
            f'<w:styles xmlns:w="{_W_NS}">{styles}</w:styles>'
        )
    if header is not None:
        parts["word/header1.xml"] = (
            '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
            f'<w:hdr xmlns:w="{_W_NS}" xmlns:r="{_R_NS}" xmlns:v="{_V_NS}">'
            f"{header}</w:hdr>"
        )
        parts["word/_rels/header1.xml.rels"] = (
            f'<Relationships xmlns="{_REL_NS}">{header_rels}</Relationships>'
        )
    if title:
        parts["docProps/core.xml"] = (
            '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
            f'<cp:coreProperties xmlns:cp="{_CP_NS}" xmlns:dc="{_DC_NS}">'
            f"<dc:title>{title}</dc:title></cp:coreProperties>"
        )
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        for part, content in parts.items():
            zf.writestr(part, content)
        for mname, mbytes in (media or {}).items():
            zf.writestr(f"word/media/{mname}", mbytes)
        zf.writestr(
            "word/_rels/document.xml.rels",
            f'<Relationships xmlns="{_REL_NS}">{doc_rels}</Relationships>',
        )
    p = tmp_path / name
    p.write_bytes(buf.getvalue())
    return p


_DOC_IMAGE_REL = (
    '<Relationship Id="rIdImg" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/image" Target="media/image1.png"/>'
)
_HEADER_IMAGE_REL = (
    '<Relationship Id="rIdH1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/image" Target="media/image1.png"/>'
)


# ---------------------------------------------------------------------------
# Style resolution: never match raw styleIds (the corpus obfuscates them)
# ---------------------------------------------------------------------------


def test_heading_via_builtin_name(tmp_path: Path) -> None:
    """styleId "a3" carries the built-in name "heading 1" — level 1."""
    docx = _build_docx(
        tmp_path,
        body=_p("Введение", style="a3") + _p("Обычный текст"),
        styles=(
            '<w:style w:type="paragraph" w:styleId="a3"><w:name w:val="heading 1"/></w:style>'
        ),
    )
    ir = extract_ir(str(docx))
    heads = [b for b in ir.pages[0].blocks if isinstance(b, HeadingBlock)]
    assert [(h.level, h.text) for h in heads] == [(1, "Введение")]
    assert heads[0].source == "docx_style"
    assert "Введение" in ir.pages[0].markdown
    assert ir.pages[0].markdown.startswith("# Введение")


def test_heading_via_basedon_chain(tmp_path: Path) -> None:
    """A custom style based on a heading style inherits the level (cycle-guarded walk)."""
    docx = _build_docx(
        tmp_path,
        body=_p("Подраздел", style="CustomSub"),
        styles=(
            '<w:style w:type="paragraph" w:styleId="a3"><w:name w:val="heading 1"/></w:style>'
            '<w:style w:type="paragraph" w:styleId="CustomSub"><w:name w:val="custom sub"/>'
            "<w:basedOn w:val=\"a3\"/></w:style>"
        ),
    )
    ir = extract_ir(str(docx))
    heads = [b for b in ir.pages[0].blocks if isinstance(b, HeadingBlock)]
    assert [(h.level, h.text) for h in heads] == [(1, "Подраздел")]


def test_heading_via_outline_lvl(tmp_path: Path) -> None:
    """outlineLvl 0-8 maps to H1-H6 (8+ collapses to H6); direct paragraph
    outline wins over the style chain."""
    docx = _build_docx(
        tmp_path,
        body=_p("Уровень три", style="Obf") + _p("Уровень шесть", outline=7),
        styles=(
            '<w:style w:type="paragraph" w:styleId="Obf"><w:name w:val="zz obf"/>'
            '<w:pPr><w:outlineLvl w:val="2"/></w:pPr></w:style>'
        ),
    )
    ir = extract_ir(str(docx))
    heads = [b for b in ir.pages[0].blocks if isinstance(b, HeadingBlock)]
    assert [(h.level, h.text) for h in heads] == [(3, "Уровень три"), (6, "Уровень шесть")]


def test_style_cycle_guard(tmp_path: Path) -> None:
    """A basedOn cycle (a->b->a) must not hang or crash — no level decided."""
    docx = _build_docx(
        tmp_path,
        body=_p("Текст", style="LoopA"),
        styles=(
            '<w:style w:type="paragraph" w:styleId="LoopA"><w:name w:val="la"/>'
            "<w:basedOn w:val=\"LoopB\"/></w:style>"
            '<w:style w:type="paragraph" w:styleId="LoopB"><w:name w:val="lb"/>'
            "<w:basedOn w:val=\"LoopA\"/></w:style>"
        ),
    )
    ir = extract_ir(str(docx))
    assert ir.pages[0].blocks == []
    assert "Текст" in ir.pages[0].markdown


# ---------------------------------------------------------------------------
# Tables: exact 2D rows, gridSpan / vMerge semantics (TKT-DX-05)
# ---------------------------------------------------------------------------


def _control_table() -> str:
    return (
        "<w:tbl>"
        + _row(
            _cell("Параметр"),
            _cell("Значение"),
        )
        + _row(
            _cell("Грунт", vmerge="restart"),
            _cell("суглинок"),
        )
        + _row(
            _cell("", vmerge="continue"),
            _cell("песок"),
        )
        + _row(
            _cell("итого", gridspan=2),
        )
        + "</w:tbl>"
    )


def test_table_rows_vmerge_repeats_gridspan_expands(tmp_path: Path) -> None:
    """vMerge continuation REPEATS the restart value (each row retrieval-
    self-sufficient); gridSpan repeats across the spanned columns; ragged
    rows pad to width."""
    docx = _build_docx(tmp_path, body=_control_table())
    ir = extract_ir(str(docx))
    tables = [b for b in ir.pages[0].blocks if isinstance(b, TableBlock)]
    assert len(tables) == 1
    assert tables[0].rows == [
        ["Параметр", "Значение"],
        ["Грунт", "суглинок"],
        ["Грунт", "песок"],
        ["итого", "итого"],
    ]
    assert tables[0].header_rows == 1
    assert tables[0].bbox is None
    # pipe markdown derived from the same rows
    assert "| Параметр | Значение |" in tables[0].markdown
    assert "| --- | --- |" in tables[0].markdown
    # page metadata: one table on (the single) page 1
    assert ir.metadata.pages_with_tables == [1]


def test_table_pipe_escapes_cells(tmp_path: Path) -> None:
    """A literal pipe inside a cell must not break the pipe table."""
    docx = _build_docx(
        tmp_path,
        body="<w:tbl>" + _row(_cell("a|b"), _cell("c")) + "</w:tbl>",
    )
    ir = extract_ir(str(docx))
    tbl = next(b for b in ir.pages[0].blocks if isinstance(b, TableBlock))
    assert tbl.rows == [["a|b", "c"]]
    assert "\\|" in tbl.markdown


def test_single_row_table_has_no_header(tmp_path: Path) -> None:
    docx = _build_docx(
        tmp_path, body="<w:tbl>" + _row(_cell("один"), _cell("два")) + "</w:tbl>"
    )
    ir = extract_ir(str(docx))
    tbl = next(b for b in ir.pages[0].blocks if isinstance(b, TableBlock))
    assert tbl.header_rows == 0


# ---------------------------------------------------------------------------
# Images: body + header, canonical order, placeholder/file agreement
# ---------------------------------------------------------------------------


def test_body_and_header_images_sidecars(tmp_path: Path) -> None:
    """Body image (DrawingML) + header logo (VML, same media part): both get
    FigureBlocks, one sidecar file, placeholder paths match written files."""
    docx = _build_docx(
        tmp_path,
        body=_p_image("rIdImg") + _p("Рис. 1 — Схема расположения"),
        header=_p_vml_image("rIdH1"),
        doc_rels=_DOC_IMAGE_REL,
        header_rels=_HEADER_IMAGE_REL,
        media={"image1.png": _PNG_1PX},
    )
    ir = extract_ir(str(docx))
    figs = [b for b in ir.pages[0].blocks if isinstance(b, FigureBlock)]
    # body figure + header figure, both pointing at the single media part
    assert [f.image_index for f in figs] == [0, 0]
    assert figs[0].caption == "Рис. 1 — Схема расположения"
    assert figs[1].caption is None
    assert all(f.bbox is None for f in figs)

    md = ir.pages[0].markdown
    assert "![](images/page0001_image_00.png)" in md

    # sidecar extraction: exactly one file (doc-id = hash of abs path), and
    # every placeholder resolves
    from pdf_router.extract_images import doc_id_from_path

    out = tmp_path / "out"
    saved = extract_docx_images(ir.pages[0], str(docx), out)
    did = doc_id_from_path(str(docx))
    assert saved == [f"{did}/images/page0001_image_00.png"]
    assert (out / saved[0]).read_bytes() == _PNG_1PX
    assert (out / did / "images/page0001_image_00.png").is_file()


def test_image_inside_table_cell_goes_to_tail(tmp_path: Path) -> None:
    """A pipe cell cannot hold an image placeholder — it is deferred to the
    document tail (after the table markdown)."""
    docx = _build_docx(
        tmp_path,
        body=(
            "<w:tbl>"
            + _row(_cell("ячейка"), "<w:tc>" + _p_image("rIdImg") + "</w:tc>")
            + "</w:tbl>"
        ),
        doc_rels=_DOC_IMAGE_REL,
        media={"image1.png": _PNG_1PX},
    )
    ir = extract_ir(str(docx))
    md = ir.pages[0].markdown
    assert md.index("|") < md.index("![](images/")  # table first, placeholder after
    figs = [b for b in ir.pages[0].blocks if isinstance(b, FigureBlock)]
    assert len(figs) == 1


def test_two_images_canonical_order(tmp_path: Path) -> None:
    """Two distinct media parts: document order defines the index; both
    placeholders and files agree."""
    docx = _build_docx(
        tmp_path,
        body=_p_image("rId2") + _p_image("rId1"),
        doc_rels=(
            '<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/image" Target="media/b.png"/>'
            '<Relationship Id="rId2" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/image" Target="media/a.png"/>'
        ),
        media={"a.png": _PNG_1PX, "b.png": _PNG_1PX},
    )
    ir = extract_ir(str(docx))
    figs = [b for b in ir.pages[0].blocks if isinstance(b, FigureBlock)]
    # document order (a.png referenced first via rId2), not rels order
    assert [f.image_index for f in figs] == [0, 1]
    out = tmp_path / "out"
    saved = extract_docx_images(ir.pages[0], str(docx), out)
    assert [Path(s).name for s in saved] == [
        "page0001_image_00.png",
        "page0001_image_01.png",
    ]


# ---------------------------------------------------------------------------
# Metadata: title, honest typing, pageless semantics
# ---------------------------------------------------------------------------


def test_title_from_core_xml(tmp_path: Path) -> None:
    docx = _build_docx(tmp_path, body=_p("Текст"), title="Тестовый том ИГИ")
    ir = extract_ir(str(docx))
    assert ir.metadata.title == "Тестовый том ИГИ"


def test_honest_metadata_typing(tmp_path: Path) -> None:
    """ADR-0003 typing: born-digital text, confidence 1.0, one pageless page,
    DOCX_ADAPTER as the tool, source_format=docx."""
    docx = _build_docx(tmp_path, body=_p("Текст"))
    ir = extract_ir(str(docx))
    m = ir.metadata
    assert m.source_format == "docx"
    assert m.pdf_type == "text_based"
    assert m.confidence == 1.0
    assert m.is_complex_layout is False
    assert m.page_count == 1
    assert ir.pages[0].page == 0
    assert ir.pages[0].needs_ocr is False
    assert ir.pages[0].src_tool.value == "docx_adapter"
    assert ir.pages[0].error is None


def test_scanned_docx_limitation_is_honest(tmp_path: Path) -> None:
    """Accepted limitation (ADR-0003): an image-only docx yields the
    placeholder markdown, no crash, no OCR claim."""
    docx = _build_docx(
        tmp_path,
        body=_p_image("rIdImg"),
        doc_rels=_DOC_IMAGE_REL,
        media={"image1.png": _PNG_1PX},
    )
    ir = extract_ir(str(docx))
    assert ir.pages[0].markdown == "![](images/page0001_image_00.png)"
    assert ir.metadata.pdf_type == "text_based"  # born-digital claim stands


def test_malformed_docx_raises_document_error(tmp_path: Path) -> None:
    """A zip WITH word/document.xml but unreadable XML is a document-level
    failure (ValueError → exit 3), not a format rejection (exit 1)."""
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("word/document.xml", "this is not xml <<<")
    p = tmp_path / "broken.docx"
    p.write_bytes(buf.getvalue())
    with pytest.raises(ValueError, match="malformed docx"):
        extract_ir(str(p))


# ---------------------------------------------------------------------------
# IR round-trip and renderers (format-neutral contract)
# ---------------------------------------------------------------------------


def test_docx_ir_round_trip(tmp_path: Path) -> None:
    docx = _build_docx(
        tmp_path,
        body=_p("Заголовок", style="a3")
        + _control_table()
        + _p_image("rIdImg"),
        styles='<w:style w:type="paragraph" w:styleId="a3"><w:name w:val="heading 1"/></w:style>',
        doc_rels=_DOC_IMAGE_REL,
        media={"image1.png": _PNG_1PX},
        title="Контроль",
    )
    ir = extract_ir(str(docx))
    ir2 = from_dict(to_dict(ir))
    assert ir2 == ir


def test_renderers_flow_through_untouched(tmp_path: Path) -> None:
    """The IR serves the renderers, not PDF mimicry: md/chunks render the
    docx material unchanged (TKT-DX-04 frame)."""
    docx = _build_docx(
        tmp_path,
        body=_p("Раздел", style="a3") + _p("Текст раздела."),
        styles='<w:style w:type="paragraph" w:styleId="a3"><w:name w:val="heading 1"/></w:style>',
    )
    ir = extract_ir(str(docx))
    md = render_markdown(ir)
    assert md.startswith("# Раздел")
    assert "Текст раздела." in md
    # chunks: H1 does not open a section (## - #### do, PDF-pipeline rule);
    # both the heading block and the paragraph land as chunks
    payloads = [json.loads(ln) for ln in render_chunks(ir).splitlines()]
    assert payloads[0]["page"] == 0
    joined = "\n".join(p["text"] for p in payloads)
    assert "# Раздел" in joined
    assert "Текст раздела." in joined
    assert all(p["src_tool"] == "docx_adapter" for p in payloads)


# ---------------------------------------------------------------------------
# Facade / CLI doors (one door each)
# ---------------------------------------------------------------------------


def test_convert_end_to_end(tmp_path: Path) -> None:
    docx = _build_docx(
        tmp_path,
        body=_p("Раздел", style="a3") + _p("Содержимое тома."),
        styles='<w:style w:type="paragraph" w:styleId="a3"><w:name w:val="heading 1"/></w:style>',
    )
    md = convert(docx)
    assert md.startswith("# Раздел")
    assert "Содержимое тома." in md


def test_process_pdf_pure_run_writes_nothing(tmp_path: Path, monkeypatch) -> None:
    """The README contract holds for docx too: no output_dir → no files."""
    monkeypatch.chdir(tmp_path)
    docx = _build_docx(tmp_path, body=_p("Текст"))
    ir, report = process_pdf(docx)
    assert report.pages_total == 1
    assert report.pages_native == 1
    assert report.pages_failed == 0
    assert ir.metadata.source_format == "docx"
    assert ir.pages[0].images == []
    assert list(tmp_path.iterdir()) == [tmp_path / "control.docx"]


def test_process_pdf_output_dir_extracts_sidecars(tmp_path: Path) -> None:
    docx = _build_docx(
        tmp_path,
        body=_p_image("rIdImg"),
        doc_rels=_DOC_IMAGE_REL,
        media={"image1.png": _PNG_1PX},
    )
    out = tmp_path / "side"
    ir, report = process_pdf(docx, output_dir=out)
    assert report.pages_image_failed == 0
    from pdf_router.extract_images import doc_id_from_path

    did = doc_id_from_path(str(docx))
    assert ir.pages[0].images == [f"{did}/images/page0001_image_00.png"]
    assert (out / did / "images" / "page0001_image_00.png").is_file()


def test_cli_full_run_writes_three_renderers(tmp_path: Path, monkeypatch) -> None:
    from pdf_router import cli

    monkeypatch.chdir(tmp_path)
    docx = _build_docx(
        tmp_path,
        body=_p("Раздел", style="a3") + _p("Тело документа."),
        styles='<w:style w:type="paragraph" w:styleId="a3"><w:name w:val="heading 1"/></w:style>',
        name="том.docx",
    )
    out = tmp_path / "out"
    code = cli.main(["--out", str(out), str(docx)])
    assert code == 0
    assert (out / "том.md").is_file()
    assert (out / "том.chunks.jsonl").is_file()
    assert (out / "том.ir.json").is_file()
    ir_json = json.loads((out / "том.ir.json").read_text(encoding="utf-8"))
    assert ir_json["metadata"]["source_format"] == "docx"
    assert ir_json["ir_version"] == "0.4"


def test_docx_info_matches_ir(tmp_path: Path) -> None:
    """--info derives from the same inspector — counts must not drift."""
    docx = _build_docx(
        tmp_path,
        body=_p("Раздел", style="a3") + _control_table() + _p_image("rIdImg"),
        styles='<w:style w:type="paragraph" w:styleId="a3"><w:name w:val="heading 1"/></w:style>',
        doc_rels=_DOC_IMAGE_REL,
        media={"image1.png": _PNG_1PX},
        title="Инфо",
    )
    info = docx_info(str(docx))
    ir = extract_ir(str(docx))
    assert info == {
        "source_format": "docx",
        "pdf_type": "text_based",
        "page_count": 1,
        "confidence": 1.0,
        "title": "Инфо",
        "tables": 1,
        "images": 1,
    }
    assert info["tables"] == sum(isinstance(b, TableBlock) for b in ir.pages[0].blocks)


# ---------------------------------------------------------------------------
# Golden volumes (TKT-DX-05): project-documentation tomes from the corpus
# ---------------------------------------------------------------------------

IGI_VOLUME = Path(
    "/home/scanum/kotel-zamechanna/archive/ВЫДАЧА_БМК Чижевщина"
    "/ИИ ред. формат/ИГИ ред. формат/1. Текст/Архив/00845.2024.07 - ИГИ.docx"
)
URITSKY_VOLUME = Path(
    "/home/scanum/kotel-zamechanna/archive/ВЫДАЧА_БМК Урицкого"
    "/ИИ ред. формат/ИГМИ ред. формат/ПР ИГМИ Урицкого.docx"
)


def _corpus_volume(path: Path) -> str:
    if not path.exists():
        pytest.skip(f"golden corpus volume not available: {path}")
    return str(path)


def test_golden_igi_volume_structure() -> None:
    """ИГИ 00845.2024.07: ~1.6 MB of OOXML, 23 top-level tables, 57 headings.
    The invariants: exact counts hold, round-trip survives, parse is fast."""
    path = _corpus_volume(IGI_VOLUME)
    t0 = time.perf_counter()
    ir = extract_ir(path)
    elapsed = time.perf_counter() - t0
    blocks = ir.pages[0].blocks
    tables = [b for b in blocks if isinstance(b, TableBlock)]
    heads = [b for b in blocks if isinstance(b, HeadingBlock)]
    assert len(tables) == 23
    assert len(heads) >= 40
    assert all(t.rows and all(isinstance(r, list) for r in t.rows) for t in tables)
    assert ir.metadata.source_format == "docx"
    assert ir.metadata.pages_with_tables == [1]
    assert elapsed < 5.0  # native parse, no excuses
    assert from_dict(to_dict(ir)) == ir


def test_golden_igi_volume_chunks_nonempty() -> None:
    """The LLM/RAG target: chunks render from the golden volume and none of
    them is empty."""
    path = _corpus_volume(IGI_VOLUME)
    ir = extract_ir(path)
    lines = [ln for ln in render_chunks(ir).splitlines() if ln.strip()]
    assert lines
    payloads = [json.loads(ln) for ln in lines]
    assert all(p["text"].strip() for p in payloads)
    # the volume's title page text survived into the material
    joined = "\n".join(p["text"] for p in payloads)
    assert "ИНЖЕНЕРНО-ГЕОЛОГИЧЕСКИХ ИЗЫСКАНИЙ" in joined


def test_golden_uritsky_volume_media_sidecars(tmp_path: Path) -> None:
    """ПР ИГМИ Урицкого: 12 MB package, 8 media parts (incl. header logo).
    Every markdown placeholder must resolve to a written sidecar file."""
    path = _corpus_volume(URITSKY_VOLUME)
    ir = extract_ir(path)
    figs = [b for b in ir.pages[0].blocks if isinstance(b, FigureBlock)]
    assert len(figs) >= 8
    out = tmp_path / "side"
    saved = extract_docx_images(ir.pages[0], path, out)
    assert len(saved) == len(set(saved))  # no duplicate writes
    for rel in saved:
        assert (out / rel).is_file()
    # every markdown placeholder resolves to a written file under the
    # document's sidecar dir (doc-id = hash of abs path)
    import re

    from pdf_router.extract_images import doc_id_from_path

    doc_dir = out / doc_id_from_path(path)
    for m in re.finditer(r"!\[\]\((images/[^)]+)\)", ir.pages[0].markdown):
        assert (doc_dir / m.group(1)).is_file(), m.group(1)


def test_golden_volumes_metadata_typing() -> None:
    for path in (IGI_VOLUME, URITSKY_VOLUME):
        if not path.exists():
            pytest.skip(f"golden corpus volume not available: {path}")
        info = docx_info(path)
        assert info["source_format"] == "docx"
        assert info["pdf_type"] == "text_based"
        assert info["confidence"] == 1.0
        assert info["page_count"] == 1
