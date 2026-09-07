"""Ticket 02: IR serialization contract — round-trip must preserve equality."""

from pdf_router.ir import IR_VERSION, IR, Metadata, OcrReasonEntry, PageResult, SrcTool, from_dict, to_dict


def make_ir() -> IR:
    return IR(
        metadata=Metadata(
            pdf_type="mixed",
            page_count=2,
            confidence=0.93,
            processing_time_ms=63.0,
            title="Sample",
            is_complex_layout=True,
            has_encoding_issues=False,
            pages_with_tables=[1],
            pages_with_columns=[1, 2],
            pages_needing_ocr=[2],
            ocr_reasons_by_page=[OcrReasonEntry(page=2, reasons=["low_text_density"])],
        ),
        pages=[
            PageResult(page=0, markdown="# Title\n\ntext", src_tool=SrcTool.PDF_INSPECTOR),
            PageResult(
                page=1,
                markdown="",
                needs_ocr=True,
                ocr_reason="low_text_density",
                src_tool=SrcTool.PDF_INSPECTOR,
                error="no_ocr_configured",
            ),
        ],
    )


def test_empty_ir_round_trip() -> None:
    ir = IR()
    assert from_dict(to_dict(ir)) == ir


def test_full_ir_round_trip() -> None:
    ir = make_ir()
    d = to_dict(ir)
    assert from_dict(d) == ir


def test_ocr_failed_page_round_trip() -> None:
    ir = make_ir()
    page = from_dict(to_dict(ir)).pages[1]
    assert page.error == "no_ocr_configured"
    assert page.src_tool is SrcTool.PDF_INSPECTOR
    assert page.needs_ocr is True


def test_page_bbox_stripped_by_default() -> None:
    ir = make_ir()
    ir.pages[0].page_bbox = [595.0, 842.0]
    d = to_dict(ir)
    assert "page_bbox" not in d["pages"][0]
    d2 = to_dict(ir, include_page_bbox=True)
    assert d2["pages"][0]["page_bbox"] == [595.0, 842.0]
    assert from_dict(d2) == ir


def test_ir_version_is_nonempty_string() -> None:
    assert isinstance(IR_VERSION, str) and IR_VERSION


def test_to_dict_is_json_serializable() -> None:
    import json

    json.dumps(to_dict(make_ir()), ensure_ascii=False)


def test_big_test_golden_anchor(big_test_pdf) -> None:
    """Ticket 03: big_test.pdf metadata via the text-path adapter."""
    from pdf_router.adapters.pdf_inspector import extract_ir

    ir = extract_ir(str(big_test_pdf))

    assert ir.metadata.pdf_type == "mixed"
    assert len(ir.metadata.pages_needing_ocr) == 10
    assert ir.pages[0].page == 0
    assert ir.pages[0].src_tool is SrcTool.PDF_INSPECTOR  # first page is text-based
    # pages 1-25 (1-indexed) are native
    for p in ir.pages[:25]:
        assert p.src_tool is SrcTool.PDF_INSPECTOR
        assert p.needs_ocr is False
