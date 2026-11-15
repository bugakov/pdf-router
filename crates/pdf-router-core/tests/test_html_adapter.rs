use pdf_router_core::adapters::html::process_html;
use pdf_router_core::formats::{detect_format, detect_format_path, Format};
use pdf_router_core::ir::{Block, HeadingBlock, SrcTool};
use pdf_router_core::process_document;

const CONTROL_HTML: &str = r#"<!DOCTYPE html>
<html><head><title>Тестовый документ</title>
<style>p { color: red; }</style>
<script>var x = "<div>";</script>
</head>
<body>
<h1>Раздел первый</h1>
<p>Ампер &amp; сбоку&nbsp;улица</p>
<p>Второй абзац<br>с переносом</p>
<h2>Второй раздел</h2>
<ul><li>Элемент</li><li>Второй элемент</li></ul>
<!-- комментарий не должен попасть в вывод -->
</body></html>"#;

#[test]
fn test_html_control_metadata_and_page() {
    let ir = process_html(CONTROL_HTML.as_bytes(), Some("test.html")).unwrap();
    let m = &ir.metadata;
    assert_eq!(m.page_count, 1);
    assert_eq!(m.source_format, "html");
    assert_eq!(m.pdf_type, "text_based");
    assert_eq!(m.confidence, 1.0);
    assert!(!m.is_complex_layout);
    assert!(m.pages_with_tables.is_empty());
    assert_eq!(m.title.as_deref(), Some("Тестовый документ"));
    assert!(!m.has_encoding_issues);

    assert_eq!(ir.pages.len(), 1);
    let page = &ir.pages[0];
    assert_eq!(page.page, 0);
    assert!(!page.needs_ocr);
    assert_eq!(page.src_tool, SrcTool::HtmlAdapter);
    assert!(page.images.is_empty());
    assert!(page.error.is_none());
}

#[test]
fn test_html_tags_strip_to_prose() {
    let ir = process_html(CONTROL_HTML.as_bytes(), None).unwrap();
    let md = &ir.pages[0].markdown;
    assert_eq!(
        md,
        "# Раздел первый\n\n\
         Ампер & сбоку улица\n\n\
         Второй абзац\n\n\
         с переносом\n\n\
         ## Второй раздел\n\n\
         Элемент\n\n\
         Второй элемент"
    );

    // Ensure style and script payloads and comments are dropped
    assert!(!md.contains("color: red"));
    assert!(!md.contains("var x"));
    assert!(!md.contains("комментарий"));
    assert!(!md.contains('<'));
    assert!(!md.contains('>'));
}

#[test]
fn test_html_headings_become_blocks() {
    let ir = process_html(CONTROL_HTML.as_bytes(), None).unwrap();
    let heads: Vec<&HeadingBlock> = ir.pages[0]
        .blocks
        .iter()
        .filter_map(|b| match b {
            Block::Heading(h) => Some(h),
            _ => None,
        })
        .collect();

    assert_eq!(heads.len(), 2);
    assert_eq!(heads[0].level, 1);
    assert_eq!(heads[0].text, "Раздел первый");
    assert_eq!(heads[0].source, "html_tag");

    assert_eq!(heads[1].level, 2);
    assert_eq!(heads[1].text, "Второй раздел");
    assert_eq!(heads[1].source, "html_tag");
}

#[test]
fn test_html_bom_handling() {
    let mut bytes = vec![0xef, 0xbb, 0xbf];
    bytes.extend_from_slice(r#"<html><body><p>Текст</p></body></html>"#.as_bytes());

    let ir = process_html(&bytes, None).unwrap();
    assert_eq!(ir.pages[0].markdown, "Текст");
    assert!(!ir.metadata.has_encoding_issues);
}

#[test]
fn test_html_encoding_issues_flag() {
    let mut bytes = Vec::new();
    bytes.extend_from_slice(b"<html><body><p>caf");
    bytes.push(0xe9); // Latin-1 é
    bytes.extend_from_slice(b"</p></body></html>");

    let ir = process_html(&bytes, None).unwrap();
    assert!(ir.metadata.has_encoding_issues);
    assert!(ir.pages[0].markdown.contains("caf"));
}

#[test]
fn test_html_dispatch_via_process_document() {
    let dir = tempfile::tempdir().unwrap();
    let file_path = dir.path().join("law.doc"); // .doc extension with HTML content
    std::fs::write(&file_path, CONTROL_HTML).unwrap();

    assert_eq!(detect_format(CONTROL_HTML.as_bytes()), Format::Html);
    assert_eq!(detect_format_path(&file_path).unwrap(), Format::Html);

    let ir = process_document(&file_path).unwrap();
    assert_eq!(ir.metadata.source_format, "html");
    assert_eq!(ir.pages[0].src_tool, SrcTool::HtmlAdapter);
    assert_eq!(ir.metadata.title.as_deref(), Some("Тестовый документ"));
}
