use pdf_inspector::types::TextItem;
use pdf_inspector::StructureElement;
use pdf_router_core::adapters::pdf::{
    extract_tables_from_markdown, headings_from_structure_tree, headings_from_text_items,
    parse_markdown_table,
};
use pdf_router_core::ir::{BBox, Block, SrcTool};
use pdf_router_core::process_document;
use std::path::PathBuf;

fn fixture_path(rel: &str) -> PathBuf {
    let manifest_dir = PathBuf::from(env!("CARGO_MANIFEST_DIR"));
    // crates/pdf-router-core -> workspace root
    manifest_dir.parent().unwrap().parent().unwrap().join(rel)
}

#[allow(clippy::too_many_arguments)]
fn dummy_text_item(
    page: u32,
    text: &str,
    font_size: f32,
    x: f32,
    y: f32,
    width: f32,
    height: f32,
    mcid: Option<i64>,
) -> TextItem {
    TextItem {
        text: text.to_string(),
        x,
        y,
        width,
        height,
        rotation: 0.0,
        advance_known: true,
        font: "Helvetica".to_string(),
        font_tag: "F1".to_string(),
        legacy_symbol_rewrite: false,
        font_size,
        page,
        is_bold: false,
        is_italic: false,
        font_weight: None,
        bold_source: None,
        fixed_pitch: None,
        fill_color: None,
        stroke_color: None,
        render_mode: None,
        is_underline: false,
        is_strikeout: false,
        layout_strikeout: false,
        item_type: pdf_inspector::types::ItemType::Text,
        mcid,
        baseline_shift: 0.0,
    }
}

#[test]
fn test_parse_markdown_table_single_header() {
    let md = "| A | B |\n|---|---|\n| 1 | 2 |\n| 3 | 4 |";
    let (rows, hdr) = parse_markdown_table(md);
    assert_eq!(hdr, 1);
    assert_eq!(
        rows,
        vec![
            vec!["A".to_string(), "B".to_string()],
            vec!["1".to_string(), "2".to_string()],
            vec!["3".to_string(), "4".to_string()],
        ]
    );
}

#[test]
fn test_parse_markdown_table_two_line_header_no_duplicate() {
    let md = "| A | B |\n|---|---|\n| Голова | Шапка |\n| 1 | 2 |\n| 3 | 4 |";
    let (rows, hdr) = parse_markdown_table(md);
    assert_eq!(hdr, 2);
    assert_eq!(
        rows,
        vec![
            vec!["A".to_string(), "B".to_string()],
            vec!["Голова".to_string(), "Шапка".to_string()],
            vec!["1".to_string(), "2".to_string()],
            vec!["3".to_string(), "4".to_string()],
        ]
    );
}

#[test]
fn test_parse_markdown_table_text_body_not_header() {
    let (rows, hdr) = parse_markdown_table("| A | B |\n|---|---|\n| Голова | Шапка |");
    assert_eq!(hdr, 1);
    assert_eq!(
        rows,
        vec![
            vec!["A".to_string(), "B".to_string()],
            vec!["Голова".to_string(), "Шапка".to_string()],
        ]
    );

    let (rows2, hdr2) = parse_markdown_table("| A | B |\n|---|---|\n| Голова | Шапка |\n| 1 | 2 |");
    assert_eq!(hdr2, 2);
    assert_eq!(
        rows2,
        vec![
            vec!["A".to_string(), "B".to_string()],
            vec!["Голова".to_string(), "Шапка".to_string()],
            vec!["1".to_string(), "2".to_string()],
        ]
    );
}

#[test]
fn test_parse_markdown_table_rejects_garbage() {
    assert_eq!(parse_markdown_table("plain text"), (vec![], 0));
    assert_eq!(
        parse_markdown_table("| A | B |\nno separator\n| 1 | 2 |"),
        (vec![], 0)
    );
    let (rows, hdr) = parse_markdown_table("| A | B |\n|---|---|\n|---|---|\n| 1 | 2 |");
    assert_eq!(hdr, 1);
    assert_eq!(
        rows,
        vec![
            vec!["A".to_string(), "B".to_string()],
            vec!["1".to_string(), "2".to_string()],
        ]
    );
}

#[test]
fn test_extract_tables_from_markdown() {
    let md = "# Title\n\nSome text.\n\n| Col 1 | Col 2 |\n|---|---|\n| v1 | v2 |\n\nFooter.";
    let tables = extract_tables_from_markdown(md);
    assert_eq!(tables.len(), 1);
    assert_eq!(tables[0].header_rows, 1);
    assert_eq!(tables[0].rows.len(), 2);
    assert_eq!(tables[0].source, "markdown_pipe");
}

#[test]
fn test_pdf_adapter_p5() {
    let p5_path = fixture_path("tests/fixtures/pdf/p5.pdf");
    assert!(
        p5_path.exists(),
        "p5.pdf fixture must exist at {:?}",
        p5_path
    );

    let ir = process_document(&p5_path).expect("Failed to process p5.pdf");

    assert_eq!(ir.metadata.source_format, "pdf");
    assert_eq!(ir.metadata.pdf_type, "text_based");
    assert_eq!(ir.metadata.page_count, 1);
    assert_eq!(ir.metadata.pages_with_tables, vec![1]);
    assert!(ir.metadata.pages_needing_ocr.is_empty());
    assert!(ir.metadata.is_complex_layout);

    assert_eq!(ir.pages.len(), 1);
    let page = &ir.pages[0];
    assert_eq!(page.page, 0);
    assert_eq!(page.src_tool, SrcTool::PdfInspector);
    assert!(!page.needs_ocr);
    assert!(page.error.is_none());

    // Golden anchors
    assert!(page.markdown.contains("Обозначение"));
    assert!(page.markdown.contains("ИЛО9.1"));

    // Blocks
    assert_eq!(page.blocks.len(), 1);
    match &page.blocks[0] {
        Block::Table(tbl) => {
            assert_eq!(tbl.header_rows, 1);
            assert!(tbl.rows.len() >= 4);
            assert_eq!(tbl.source, "markdown_pipe");
            assert!(tbl.markdown.contains("Обозначение"));
            assert!(tbl.markdown.contains("ИЛО9.1"));
        }
        other => panic!("Expected TableBlock, got {:?}", other),
    }
}

#[test]
fn test_pdf_adapter_p27() {
    let p27_path = fixture_path("tests/fixtures/pdf/p27.pdf");
    assert!(
        p27_path.exists(),
        "p27.pdf fixture must exist at {:?}",
        p27_path
    );

    let ir = process_document(&p27_path).expect("Failed to process p27.pdf");

    assert_eq!(ir.metadata.source_format, "pdf");
    assert_eq!(ir.metadata.pdf_type, "image_based");
    assert_eq!(ir.metadata.page_count, 1);
    assert_eq!(ir.pages.len(), 1);
    assert_eq!(ir.pages[0].src_tool, SrcTool::PdfInspector);
}

#[test]
fn test_headings_from_structure_tree() {
    let elements = vec![
        StructureElement {
            page: 1,
            mcid: 10,
            role: "H1".to_string(),
        },
        StructureElement {
            page: 1,
            mcid: 20,
            role: "H2".to_string(),
        },
    ];

    let items = vec![
        dummy_text_item(1, "Chapter", 20.0, 50.0, 100.0, 80.0, 20.0, Some(10)),
        dummy_text_item(1, "One", 20.0, 140.0, 100.0, 40.0, 20.0, Some(10)),
        dummy_text_item(1, "Section 1.1", 16.0, 50.0, 200.0, 100.0, 16.0, Some(20)),
    ];

    let by_page = headings_from_structure_tree(&elements, &items);
    assert_eq!(by_page.len(), 1);
    let page0_headings = &by_page[&0];
    assert_eq!(page0_headings.len(), 2);

    assert_eq!(page0_headings[0].level, 1);
    assert_eq!(page0_headings[0].text, "Chapter One");
    assert_eq!(page0_headings[0].source, "structure_tree");
    assert_eq!(
        page0_headings[0].bbox,
        Some(BBox {
            x0: 50.0,
            y0: 100.0,
            x1: 180.0,
            y1: 120.0,
        })
    );

    assert_eq!(page0_headings[1].level, 2);
    assert_eq!(page0_headings[1].text, "Section 1.1");
    assert_eq!(page0_headings[1].source, "structure_tree");
}

#[test]
fn test_headings_from_text_items_font_size_fallback() {
    let items = vec![
        // Body text: font size 10.0
        dummy_text_item(
            1,
            "Normal body text paragraph 1.",
            10.0,
            50.0,
            300.0,
            200.0,
            10.0,
            None,
        ),
        dummy_text_item(
            1,
            "Normal body text paragraph 2.",
            10.0,
            50.0,
            320.0,
            200.0,
            10.0,
            None,
        ),
        dummy_text_item(
            1,
            "Normal body text paragraph 3.",
            10.0,
            50.0,
            340.0,
            200.0,
            10.0,
            None,
        ),
        // Heading 1: font size 20.0 >= 10.0 * 1.8 -> level 1
        dummy_text_item(1, "Main", 20.0, 50.0, 500.0, 50.0, 20.0, None),
        dummy_text_item(1, "Title", 20.0, 110.0, 500.0, 50.0, 20.0, None),
        // Heading 2: font size 16.0 >= 10.0 * 1.5 -> level 2 (y = 400.0)
        dummy_text_item(1, "Subtitle", 16.0, 50.0, 400.0, 80.0, 16.0, None),
    ];

    let headings = headings_from_text_items(&items, 0);
    assert_eq!(headings.len(), 2);

    // Sorted by y descending (y = 500.0 first, then y = 400.0)
    assert_eq!(headings[0].level, 1);
    assert_eq!(headings[0].text, "Main Title");
    assert_eq!(headings[0].source, "font_size_fallback");
    assert_eq!(
        headings[0].bbox,
        Some(BBox {
            x0: 50.0,
            y0: 500.0,
            x1: 160.0,
            y1: 520.0,
        })
    );

    assert_eq!(headings[1].level, 2);
    assert_eq!(headings[1].text, "Subtitle");
    assert_eq!(headings[1].source, "font_size_fallback");
    assert_eq!(
        headings[1].bbox,
        Some(BBox {
            x0: 50.0,
            y0: 400.0,
            x1: 130.0,
            y1: 416.0,
        })
    );
}
