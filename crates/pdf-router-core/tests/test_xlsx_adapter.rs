use std::io::{Cursor, Write};
use zip::write::SimpleFileOptions;

use pdf_router_core::adapters::xlsx::{
    clean_number_text, parse_rels, parse_shared_strings, parse_workbook_sheets, process_xlsx,
    split_ref,
};
use pdf_router_core::formats::{detect_format, Format};
use pdf_router_core::ir::{Block, SrcTool};
use pdf_router_core::process_document;

#[test]
fn test_split_ref() {
    assert_eq!(split_ref("A1"), Some((1, 1)));
    assert_eq!(split_ref("B2"), Some((2, 2)));
    assert_eq!(split_ref("Z100"), Some((100, 26)));
    assert_eq!(split_ref("AA1"), Some((1, 27)));
    assert_eq!(split_ref("BC12"), Some((12, 55)));

    assert_eq!(split_ref(""), None);
    assert_eq!(split_ref("12"), None);
    assert_eq!(split_ref("ABC"), None);
    assert_eq!(split_ref("12BC"), None);
    assert_eq!(split_ref("a1"), None);
}

#[test]
fn test_clean_number_text() {
    assert_eq!(clean_number_text("1000.0"), "1000");
    assert_eq!(clean_number_text("0.0"), "0");
    assert_eq!(clean_number_text("-5.0"), "-5");
    assert_eq!(clean_number_text("4.8000000000000001E-2"), "0.048");
    assert_eq!(clean_number_text("hello"), "hello");
}

#[test]
fn test_parse_shared_strings() {
    let xml = r#"<?xml version="1.0" encoding="UTF-8"?>
    <sst xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">
        <si><t>First String</t></si>
        <si><r><t>Part 1 </t></r><r><t>Part 2</t></r></si>
        <si><t xml:space="preserve"> With spaces </t></si>
    </sst>"#;

    let strings = parse_shared_strings(xml);
    assert_eq!(strings.len(), 3);
    assert_eq!(strings[0], "First String");
    assert_eq!(strings[1], "Part 1 Part 2");
    assert_eq!(strings[2], " With spaces ");
}

#[test]
fn test_parse_rels_and_sheets() {
    let rels_xml = r#"<?xml version="1.0" encoding="UTF-8"?>
    <Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">
        <Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/worksheet" Target="worksheets/sheet1.xml"/>
        <Relationship Id="rId2" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/worksheet" Target="/xl/worksheets/sheet2.xml"/>
    </Relationships>"#;

    let rels = parse_rels(rels_xml);
    assert_eq!(
        rels.get("rId1"),
        Some(&"xl/worksheets/sheet1.xml".to_string())
    );
    assert_eq!(
        rels.get("rId2"),
        Some(&"xl/worksheets/sheet2.xml".to_string())
    );

    let wb_xml = r#"<?xml version="1.0" encoding="UTF-8"?>
    <workbook xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships">
        <sheets>
            <sheet name="Смета" sheetId="1" r:id="rId1"/>
            <sheet name="Лист2" sheetId="2" r:id="rId2"/>
        </sheets>
    </workbook>"#;

    let sheets = parse_workbook_sheets(wb_xml);
    assert_eq!(sheets.len(), 2);
    assert_eq!(sheets[0].name, "Смета");
    assert_eq!(sheets[0].rel_id, "rId1");
    assert_eq!(sheets[1].name, "Лист2");
    assert_eq!(sheets[1].rel_id, "rId2");
}

fn build_control_xlsx() -> Vec<u8> {
    let mut buf = Cursor::new(Vec::new());
    let mut zip = zip::ZipWriter::new(&mut buf);
    let options = SimpleFileOptions::default();

    // 1. [Content_Types].xml
    zip.start_file("[Content_Types].xml", options).unwrap();
    zip.write_all(
        r#"<?xml version="1.0" encoding="UTF-8"?>
    <Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">
        <Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>
        <Default Extension="xml" ContentType="application/xml"/>
        <Override PartName="/xl/workbook.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml"/>
        <Override PartName="/xl/worksheets/sheet1.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml"/>
        <Override PartName="/xl/worksheets/sheet2.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml"/>
        <Override PartName="/xl/sharedStrings.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.sharedStrings+xml"/>
    </Types>"#
            .as_bytes(),
    )
    .unwrap();

    // 2. _rels/.rels
    zip.start_file("_rels/.rels", options).unwrap();
    zip.write_all(
        r#"<?xml version="1.0" encoding="UTF-8"?>
    <Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">
        <Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="xl/workbook.xml"/>
        <Relationship Id="rId2" Type="http://schemas.openxmlformats.org/package/2006/relationships/metadata/core-properties" Target="docProps/core.xml"/>
    </Relationships>"#
            .as_bytes(),
    )
    .unwrap();

    // 3. docProps/core.xml
    zip.start_file("docProps/core.xml", options).unwrap();
    zip.write_all(
        r#"<?xml version="1.0" encoding="UTF-8"?>
    <cp:coreProperties xmlns:cp="http://schemas.openxmlformats.org/package/2006/metadata/core-properties" xmlns:dc="http://purl.org/dc/elements/1.1/">
        <dc:title>Контрольный файл</dc:title>
    </cp:coreProperties>"#
            .as_bytes(),
    )
    .unwrap();

    // 4. xl/workbook.xml
    zip.start_file("xl/workbook.xml", options).unwrap();
    zip.write_all(
        r#"<?xml version="1.0" encoding="UTF-8"?>
    <workbook xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships">
        <sheets>
            <sheet name="Смета" sheetId="1" r:id="rId1"/>
            <sheet name="Лист2" sheetId="2" r:id="rId2"/>
        </sheets>
    </workbook>"#
            .as_bytes(),
    )
    .unwrap();

    // 5. xl/_rels/workbook.xml.rels
    zip.start_file("xl/_rels/workbook.xml.rels", options)
        .unwrap();
    zip.write_all(
        r#"<?xml version="1.0" encoding="UTF-8"?>
    <Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">
        <Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/worksheet" Target="worksheets/sheet1.xml"/>
        <Relationship Id="rId2" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/worksheet" Target="worksheets/sheet2.xml"/>
    </Relationships>"#
            .as_bytes(),
    )
    .unwrap();

    // 6. xl/sharedStrings.xml
    let shared = [
        "Рабочая преамбула к смете", // 0
        "Позиция",                   // 1
        "Ед.",                       // 2
        "Цена",                      // 3
        "Цемент",                    // 4
        "т",                         // 5
        "скрытая колонка",           // 6
        "тоже скрыто",               // 7
        "Группа",                    // 8
        "x",                         // 9
        "y",                         // 10
        "z",                         // 11
        "Итого",                     // 12
        "скрытая строка",            // 13
        "Общая графа",               // 14
        "   ",                       // 15
        "После пробелов",            // 16
        "Второй лист",               // 17
    ];
    let mut sst_xml = String::from(
        r#"<?xml version="1.0" encoding="UTF-8"?><sst xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">"#,
    );
    for s in shared {
        sst_xml.push_str(&format!("<si><t>{s}</t></si>"));
    }
    sst_xml.push_str("</sst>");
    zip.start_file("xl/sharedStrings.xml", options).unwrap();
    zip.write_all(sst_xml.as_bytes()).unwrap();

    // 7. xl/worksheets/sheet1.xml ("Смета")
    let sheet1_xml = r#"<?xml version="1.0" encoding="UTF-8"?>
    <worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">
        <cols>
            <col min="3" max="3" hidden="1" width="8.43" customWidth="1"/>
        </cols>
        <sheetData>
            <row r="1"><c r="A1" t="s"><v>0</v></c></row>
            <row r="2">
                <c r="A2" t="s"><v>1</v></c>
                <c r="B2" t="s"><v>2</v></c>
                <c r="D2" t="s"><v>3</v></c>
            </row>
            <row r="3">
                <c r="A3" t="s"><v>4</v></c>
                <c r="B3" t="s"><v>5</v></c>
                <c r="C3" t="s"><v>6</v></c>
                <c r="D3"><v>1000</v></c>
            </row>
            <row r="4"><c r="C4" t="s"><v>7</v></c></row>
            <row r="5">
                <c r="A5" t="s"><v>8</v></c>
                <c r="B5" t="s"><v>9</v></c>
                <c r="D5"><v>1</v></c>
            </row>
            <row r="6">
                <c r="B6" t="s"><v>10</v></c>
                <c r="D6"><v>2</v></c>
            </row>
            <row r="7">
                <c r="B7" t="s"><v>11</v></c>
                <c r="D7"><v>3</v></c>
            </row>
            <row r="8">
                <c r="A8" t="s"><v>12</v></c>
                <c r="D8"><f>=СУММ(D5:D7)</f><v>3000</v></c>
            </row>
            <row r="9" hidden="1">
                <c r="A9" t="s"><v>13</v></c>
                <c r="D9"><v>99</v></c>
            </row>
            <row r="10"><c r="A10" t="s"><v>14</v></c></row>
            <row r="11">
                <c r="A11" t="s"><v>15</v></c>
                <c r="B11" t="s"><v>16</v></c>
            </row>
            <row r="12"><c r="D12"><v>4.8000000000000001E-2</v></c></row>
        </sheetData>
        <mergeCells count="2">
            <mergeCell ref="A5:A7"/>
            <mergeCell ref="A10:D10"/>
        </mergeCells>
    </worksheet>"#;
    zip.start_file("xl/worksheets/sheet1.xml", options).unwrap();
    zip.write_all(sheet1_xml.as_bytes()).unwrap();

    // 8. xl/worksheets/sheet2.xml ("Лист2")
    let sheet2_xml = r#"<?xml version="1.0" encoding="UTF-8"?>
    <worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">
        <sheetData>
            <row r="1"><c r="A1" t="s"><v>17</v></c></row>
        </sheetData>
    </worksheet>"#;
    zip.start_file("xl/worksheets/sheet2.xml", options).unwrap();
    zip.write_all(sheet2_xml.as_bytes()).unwrap();

    zip.finish().unwrap();
    buf.into_inner()
}

#[test]
fn test_control_xlsx_full_parity() {
    let xlsx_bytes = build_control_xlsx();

    // 1. Magic format detection
    assert_eq!(detect_format(&xlsx_bytes), Format::Xlsx);

    // 2. Parse into IR
    let ir = process_xlsx(&xlsx_bytes, Some("test.xlsx")).expect("process_xlsx failed");

    // 3. Metadata validation
    let m = &ir.metadata;
    assert_eq!(m.source_format, "xlsx");
    assert_eq!(m.pdf_type, "text_based");
    assert_eq!(m.page_count, 2);
    assert_eq!(m.confidence, 1.0);
    assert_eq!(m.title, Some("Контрольный файл".to_string()));
    assert_eq!(m.pages_with_tables, vec![1, 2]);

    // 4. Page 0 ("Смета")
    let p0 = &ir.pages[0];
    assert_eq!(p0.page, 0);
    assert_eq!(p0.src_tool, SrcTool::XlsxAdapter);
    assert!(!p0.needs_ocr);
    assert!(p0.markdown.starts_with("## Смета"));

    // Check heading block
    match &p0.blocks[0] {
        Block::Heading(h) => {
            assert_eq!(h.level, 2);
            assert_eq!(h.text, "Смета");
            assert_eq!(h.source, "xlsx_sheet");
        }
        other => panic!("Expected HeadingBlock, got {:?}", other),
    }

    // Check table block
    let table = p0
        .blocks
        .iter()
        .find_map(|b| match b {
            Block::Table(t) => Some(t),
            _ => None,
        })
        .expect("Expected TableBlock in page 0");

    assert_eq!(table.rows.len(), 6);
    assert_eq!(table.header_rows, 1);
    // r2: header
    assert_eq!(table.rows[0], vec!["Позиция", "Ед.", "", "Цена"]);
    // r3: body (hidden C is cut to empty string)
    assert_eq!(table.rows[1], vec!["Цемент", "т", "", "1000"]);
    // r5-r7: vertical merge A5:A7 repeats "Группа" down!
    assert_eq!(table.rows[2], vec!["Группа", "x", "", "1"]);
    assert_eq!(table.rows[3], vec!["Группа", "y", "", "2"]);
    assert_eq!(table.rows[4], vec!["Группа", "z", "", "3"]);
    // r8: formula row with cached value
    assert_eq!(table.rows[5], vec!["Итого", "", "", "3000"]);

    // Prose checks
    assert!(p0.markdown.contains("Рабочая преамбула к смете"));
    assert!(p0.markdown.contains("После пробелов"));
    assert!(p0.markdown.contains("0.048"));

    // Negative checks: hidden column / hidden row / formula syntax never leak
    assert!(!p0.markdown.contains("скрытая колонка"));
    assert!(!p0.markdown.contains("тоже скрыто"));
    assert!(!p0.markdown.contains("скрытая строка"));
    assert!(!p0.markdown.contains("99"));
    assert!(!p0.markdown.contains("=СУММ"));

    // Horizontal merge A10:D10: appears once in prose
    let occurrences = p0.markdown.matches("Общая графа").count();
    assert_eq!(occurrences, 1);

    // 5. Page 1 ("Лист2")
    let p1 = &ir.pages[1];
    assert_eq!(p1.page, 1);
    assert_eq!(p1.src_tool, SrcTool::XlsxAdapter);
    assert_eq!(p1.markdown, "## Лист2\n\n| Второй лист |\n| --- |");
    assert_eq!(p1.blocks.len(), 2); // Heading + 1x1 Table
}

#[test]
fn test_empty_sheet_is_still_a_page() {
    let mut buf = Cursor::new(Vec::new());
    let mut zip = zip::ZipWriter::new(&mut buf);
    let options = SimpleFileOptions::default();

    zip.start_file("xl/workbook.xml", options).unwrap();
    zip.write_all(
        r#"<?xml version="1.0" encoding="UTF-8"?>
    <workbook xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships">
        <sheets><sheet name="Пустой" sheetId="1" r:id="rId1"/></sheets>
    </workbook>"#
            .as_bytes(),
    )
    .unwrap();

    zip.start_file("xl/_rels/workbook.xml.rels", options)
        .unwrap();
    zip.write_all(
        r#"<?xml version="1.0" encoding="UTF-8"?>
    <Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">
        <Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/worksheet" Target="worksheets/sheet1.xml"/>
    </Relationships>"#
            .as_bytes(),
    )
    .unwrap();

    zip.start_file("xl/worksheets/sheet1.xml", options).unwrap();
    zip.write_all(
        r#"<?xml version="1.0" encoding="UTF-8"?>
    <worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">
        <sheetData/>
    </worksheet>"#
            .as_bytes(),
    )
    .unwrap();

    zip.finish().unwrap();
    let bytes = buf.into_inner();

    let ir = process_xlsx(&bytes, None).expect("process_xlsx failed");
    assert_eq!(ir.metadata.page_count, 1);
    assert_eq!(ir.metadata.pages_with_tables, Vec::<usize>::new());
    assert_eq!(ir.pages[0].markdown, "## Пустой");
    assert_eq!(ir.pages[0].blocks.len(), 1);
}

#[test]
fn test_malformed_xlsx_errors() {
    // Bad zip
    assert!(process_xlsx(b"not a zip file", None).is_err());

    // Bad workbook
    let mut buf = Cursor::new(Vec::new());
    let mut zip = zip::ZipWriter::new(&mut buf);
    zip.start_file("xl/workbook.xml", SimpleFileOptions::default())
        .unwrap();
    zip.write_all(b"invalid xml <<<").unwrap();
    zip.finish().unwrap();
    assert!(process_xlsx(&buf.into_inner(), None).is_err());
}

#[test]
fn test_process_document_routes_xlsx() {
    let xlsx_bytes = build_control_xlsx();
    let tmp_dir = tempfile::tempdir().unwrap();
    let file_path = tmp_dir.path().join("estimate.xlsx");
    std::fs::write(&file_path, xlsx_bytes).unwrap();

    let ir = process_document(&file_path).expect("process_document failed");
    assert_eq!(ir.metadata.source_format, "xlsx");
    assert_eq!(ir.metadata.page_count, 2);
    assert_eq!(ir.pages[0].src_tool, SrcTool::XlsxAdapter);
    assert!(ir.pages[0].markdown.contains("Рабочая преамбула к смете"));
}
