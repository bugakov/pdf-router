use std::io::Write;
use zip::write::SimpleFileOptions;

use pdf_router_core::adapters::docx::{
    parse_core_title, parse_styles_xml, process_docx, rows_to_markdown, style_heading_level,
};
use pdf_router_core::formats::{detect_format, Format};
use pdf_router_core::ir::{Block, SrcTool};
use pdf_router_core::process_document;

fn build_test_docx(body_xml: &str, styles_xml: Option<&str>, title: Option<&str>) -> Vec<u8> {
    let mut buf = std::io::Cursor::new(Vec::new());
    let mut zip = zip::ZipWriter::new(&mut buf);
    let options = SimpleFileOptions::default();

    zip.start_file("word/document.xml", options).unwrap();
    let doc_content = format!(
        r#"<?xml version="1.0" encoding="UTF-8"?><w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"><w:body>{}</w:body></w:document>"#,
        body_xml
    );
    zip.write_all(doc_content.as_bytes()).unwrap();

    if let Some(styles) = styles_xml {
        zip.start_file("word/styles.xml", options).unwrap();
        let styles_content = format!(
            r#"<?xml version="1.0" encoding="UTF-8"?><w:styles xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">{}</w:styles>"#,
            styles
        );
        zip.write_all(styles_content.as_bytes()).unwrap();
    }

    if let Some(t) = title {
        zip.start_file("docProps/core.xml", options).unwrap();
        let core_content = format!(
            r#"<?xml version="1.0" encoding="UTF-8"?><cp:coreProperties xmlns:cp="http://schemas.openxmlformats.org/package/2006/metadata/core-properties" xmlns:dc="http://purl.org/dc/elements/1.1/"><dc:title>{}</dc:title></cp:coreProperties>"#,
            t
        );
        zip.write_all(core_content.as_bytes()).unwrap();
    }

    zip.finish().unwrap();
    buf.into_inner()
}

#[test]
fn test_parse_styles_and_heading_resolution() {
    let styles_xml = r#"
        <w:style w:styleId="a1">
            <w:name w:val="Заголовок 1"/>
            <w:pPr><w:outlineLvl w:val="0"/></w:pPr>
        </w:style>
        <w:style w:styleId="custom_h2">
            <w:name w:val="heading 2"/>
        </w:style>
        <w:style w:styleId="derived">
            <w:name w:val="Derived Style"/>
            <w:basedOn w:val="a1"/>
        </w:style>
        <w:style w:styleId="title_style">
            <w:name w:val="Title"/>
        </w:style>
    "#;

    let styles = parse_styles_xml(styles_xml);
    assert_eq!(styles.len(), 4);

    assert_eq!(style_heading_level(Some("a1"), &styles), Some(1));
    assert_eq!(style_heading_level(Some("custom_h2"), &styles), Some(2));
    assert_eq!(style_heading_level(Some("derived"), &styles), Some(1));
    assert_eq!(style_heading_level(Some("title_style"), &styles), Some(1));
    assert_eq!(style_heading_level(Some("non_existent"), &styles), None);
    assert_eq!(style_heading_level(None, &styles), None);
}

#[test]
fn test_parse_core_title() {
    let core_xml = r#"
        <cp:coreProperties xmlns:cp="http://schemas.openxmlformats.org/package/2006/metadata/core-properties" xmlns:dc="http://purl.org/dc/elements/1.1/">
            <dc:title>  Technical Report on Bridges  </dc:title>
        </cp:coreProperties>
    "#;
    assert_eq!(
        parse_core_title(core_xml),
        Some("Technical Report on Bridges".to_string())
    );
    assert_eq!(parse_core_title("<empty/>"), None);
}

#[test]
fn test_rows_to_markdown() {
    let rows = vec![
        vec!["Col A".to_string(), "Col B".to_string()],
        vec!["1".to_string(), "2".to_string()],
        vec!["3 | with pipe".to_string(), "4".to_string()],
    ];

    let md = rows_to_markdown(&rows);
    assert!(md.contains("| Col A | Col B |"));
    assert!(md.contains("| --- | --- |"));
    assert!(md.contains(r"| 3 \| with pipe | 4 |"));
}

#[test]
fn test_docx_synthetic_control_headings_and_tables() {
    let styles_xml = r#"
        <w:style w:styleId="Heading1">
            <w:name w:val="heading 1"/>
        </w:style>
        <w:style w:styleId="ObfuscatedH2">
            <w:name w:val="Раздел"/>
            <w:pPr><w:outlineLvl w:val="1"/></w:pPr>
        </w:style>
    "#;

    let body_xml = r#"
        <w:p>
            <w:pPr><w:pStyle w:val="Heading1"/></w:pPr>
            <w:r><w:t>Main Section</w:t></w:r>
        </w:p>
        <w:p>
            <w:pPr><w:pStyle w:val="ObfuscatedH2"/></w:pPr>
            <w:r><w:t>Subsection A</w:t></w:r>
        </w:p>
        <w:p>
            <w:pPr><w:outlineLvl w:val="2"/></w:pPr>
            <w:r><w:t>Direct Outline Subsection</w:t></w:r>
        </w:p>
        <w:p>
            <w:r><w:t>Normal prose text paragraph with </w:t><w:tab/><w:t>a tab.</w:t></w:r>
        </w:p>
        <w:tbl>
            <w:tr>
                <w:tc><w:p><w:r><w:t>Header A</w:t></w:r></w:p></w:tc>
                <w:tc><w:p><w:r><w:t>Header B</w:t></w:r></w:p></w:tc>
            </w:tr>
            <w:tr>
                <w:tc>
                    <w:tcPr><w:gridSpan w:val="2"/></w:tcPr>
                    <w:p><w:r><w:t>Spanned Cell</w:t></w:r></w:p>
                </w:tc>
            </w:tr>
            <w:tr>
                <w:tc>
                    <w:tcPr><w:vMerge w:val="restart"/></w:tcPr>
                    <w:p><w:r><w:t>Merged Row Header</w:t></w:r></w:p>
                </w:tc>
                <w:tc><w:p><w:r><w:t>Value 1</w:t></w:r></w:p></w:tc>
            </w:tr>
            <w:tr>
                <w:tc>
                    <w:tcPr><w:vMerge/></w:tcPr>
                    <w:p/>
                </w:tc>
                <w:tc><w:p><w:r><w:t>Value 2</w:t></w:r></w:p></w:tc>
            </w:tr>
        </w:tbl>
    "#;

    let docx_bytes = build_test_docx(body_xml, Some(styles_xml), Some("Document Title"));

    // Verify format detection
    assert_eq!(detect_format(&docx_bytes), Format::Docx);

    let ir = process_docx(&docx_bytes, Some("test.docx")).expect("process_docx failed");

    assert_eq!(ir.metadata.source_format, "docx");
    assert_eq!(ir.metadata.pdf_type, "text_based");
    assert_eq!(ir.metadata.page_count, 1);
    assert_eq!(ir.metadata.title, Some("Document Title".to_string()));
    assert_eq!(ir.metadata.pages_with_tables, vec![1]);

    assert_eq!(ir.pages.len(), 1);
    let page = &ir.pages[0];
    assert_eq!(page.page, 0);
    assert_eq!(page.src_tool, SrcTool::DocxAdapter);
    assert!(!page.needs_ocr);

    // Markdown checks
    assert!(page.markdown.contains("# Main Section"));
    assert!(page.markdown.contains("## Subsection A"));
    assert!(page.markdown.contains("### Direct Outline Subsection"));
    assert!(page
        .markdown
        .contains("Normal prose text paragraph with \ta tab."));
    assert!(page.markdown.contains("| Header A | Header B |"));
    assert!(page.markdown.contains("| Spanned Cell | Spanned Cell |"));

    // Structural blocks
    assert_eq!(page.blocks.len(), 4); // 3 headings + 1 table

    match &page.blocks[0] {
        Block::Heading(h) => {
            assert_eq!(h.level, 1);
            assert_eq!(h.text, "Main Section");
            assert_eq!(h.source, "docx_style");
        }
        other => panic!("Expected Heading, got {:?}", other),
    }

    match &page.blocks[1] {
        Block::Heading(h) => {
            assert_eq!(h.level, 2);
            assert_eq!(h.text, "Subsection A");
            assert_eq!(h.source, "docx_style");
        }
        other => panic!("Expected Heading, got {:?}", other),
    }

    match &page.blocks[2] {
        Block::Heading(h) => {
            assert_eq!(h.level, 3);
            assert_eq!(h.text, "Direct Outline Subsection");
            assert_eq!(h.source, "docx_style");
        }
        other => panic!("Expected Heading, got {:?}", other),
    }

    match &page.blocks[3] {
        Block::Table(tbl) => {
            assert_eq!(tbl.header_rows, 1);
            assert_eq!(tbl.rows.len(), 4);
            // Row 0: Header
            assert_eq!(tbl.rows[0], vec!["Header A", "Header B"]);
            // Row 1: gridSpan = 2 expands to duplicate across columns
            assert_eq!(tbl.rows[1], vec!["Spanned Cell", "Spanned Cell"]);
            // Row 2: vMerge restart
            assert_eq!(tbl.rows[2], vec!["Merged Row Header", "Value 1"]);
            // Row 3: vMerge continue repeats the restart value
            assert_eq!(tbl.rows[3], vec!["Merged Row Header", "Value 2"]);
        }
        other => panic!("Expected Table, got {:?}", other),
    }
}

#[test]
fn test_process_document_routes_docx() {
    let docx_bytes = build_test_docx(
        "<w:p><w:r><w:t>Hello from DOCX!</w:t></w:r></w:p>",
        None,
        Some("My Document"),
    );
    let tmp_dir = tempfile::tempdir().unwrap();
    let file_path = tmp_dir.path().join("sample.docx");
    std::fs::write(&file_path, docx_bytes).unwrap();

    let ir = process_document(&file_path).expect("process_document failed");
    assert_eq!(ir.metadata.source_format, "docx");
    assert_eq!(ir.metadata.title, Some("My Document".to_string()));
    assert_eq!(ir.pages[0].markdown, "Hello from DOCX!");
    assert_eq!(ir.pages[0].src_tool, SrcTool::DocxAdapter);
}
