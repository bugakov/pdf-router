use pdf_router_core::formats::{detect_format, Format};
use pdf_router_core::ir::{
    BBox, Block, HeadingBlock, Ir, Metadata, PageResult, SrcTool, TableBlock, IR_VERSION,
};
use pdf_router_core::renderers::{render_json, render_markdown};

#[test]
fn test_magic_bytes_detection() {
    assert_eq!(detect_format(b"%PDF-1.7\n..."), Format::Pdf);
    assert_eq!(
        detect_format(b"\xD0\xCF\x11\xE0\xA1\xB1\x1A\xE1\x00"),
        Format::Doc
    );
    assert_eq!(detect_format(b"AT&TFORM\x00\x00\x00\x00DJVU"), Format::Djvu);
    assert_eq!(
        detect_format(b"<!DOCTYPE html><html><body>test</body></html>"),
        Format::Html
    );
    assert_eq!(detect_format(b"<html lang=\"ru\">..."), Format::Html);
    assert_eq!(detect_format(b"PK\x03\x04\x14\x00..."), Format::Zip);
    assert_eq!(detect_format(b"random text without magic"), Format::Unknown);
    assert_eq!(detect_format(b""), Format::Unknown);
}

#[test]
fn test_ir_version_is_0_6() {
    assert_eq!(IR_VERSION, "0.6");
}

#[test]
fn test_ir_serialization_roundtrip() {
    let ir = Ir {
        ir_version: IR_VERSION.to_string(),
        metadata: Metadata {
            source_format: "pdf".to_string(),
            source_path: Some("/test/doc.pdf".to_string()),
            pdf_type: "text_based".to_string(),
            page_count: 1,
            pages_with_tables: vec![1],
            pages_needing_ocr: vec![],
            confidence: 1.0,
            title: Some("Test Document".to_string()),
        },
        pages: vec![PageResult {
            page: 0,
            markdown: "# Section 1\n\n| A | B |\n|---|---|\n| 1 | 2 |".to_string(),
            src_tool: SrcTool::PdfInspector,
            blocks: vec![
                Block::Heading(HeadingBlock {
                    level: 1,
                    text: "Section 1".to_string(),
                    bbox: Some(BBox {
                        x0: 10.0,
                        y0: 20.0,
                        x1: 200.0,
                        y1: 40.0,
                    }),
                    source: "structure_tree".to_string(),
                }),
                Block::Table(TableBlock {
                    markdown: "| A | B |\n|---|---|\n| 1 | 2 |".to_string(),
                    rows: vec![
                        vec!["A".to_string(), "B".to_string()],
                        vec!["1".to_string(), "2".to_string()],
                    ],
                    header_rows: 1,
                    bbox: None,
                    source: "markdown_pipe".to_string(),
                }),
            ],
            images: vec![],
            error: None,
            needs_ocr: false,
        }],
    };

    let json_str = render_json(&ir).expect("render_json failed");
    assert!(json_str.contains("\"ir_version\": \"0.6\""));
    assert!(json_str.contains("\"source_format\": \"pdf\""));
    assert!(json_str.contains("\"kind\": \"HeadingBlock\""));
    assert!(json_str.contains("\"kind\": \"TableBlock\""));

    let deserialized: Ir = serde_json::from_str(&json_str).expect("deserialization failed");
    assert_eq!(deserialized.ir_version, "0.6");
    assert_eq!(deserialized.pages.len(), 1);
    assert_eq!(deserialized.pages[0].blocks.len(), 2);
}

#[test]
fn test_render_markdown() {
    let ir = Ir {
        ir_version: IR_VERSION.to_string(),
        metadata: Metadata {
            source_format: "pdf".to_string(),
            source_path: None,
            pdf_type: "text_based".to_string(),
            page_count: 2,
            pages_with_tables: vec![],
            pages_needing_ocr: vec![],
            confidence: 1.0,
            title: None,
        },
        pages: vec![
            PageResult {
                page: 0,
                markdown: "Page 1 content".to_string(),
                src_tool: SrcTool::PdfInspector,
                blocks: vec![],
                images: vec![],
                error: None,
                needs_ocr: false,
            },
            PageResult {
                page: 1,
                markdown: "Page 2 content".to_string(),
                src_tool: SrcTool::PdfInspector,
                blocks: vec![],
                images: vec![],
                error: None,
                needs_ocr: false,
            },
        ],
    };

    let md = render_markdown(&ir);
    assert_eq!(md.trim(), "Page 1 content\n\nPage 2 content");
}
