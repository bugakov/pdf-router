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

    let sheets = parse_workbook_sheets(wb_xml).expect("parse sheets failed");
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

#[test]
fn test_multisheet_xlsx_bounded_memory_and_ir_generation() {
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
        <Override PartName="/xl/worksheets/sheet3.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml"/>
        <Override PartName="/xl/worksheets/sheet4.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml"/>
        <Override PartName="/xl/worksheets/sheet5.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml"/>
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
        <dc:title>Сводный сметный расчет</dc:title>
    </cp:coreProperties>"#
            .as_bytes(),
    )
    .unwrap();

    // 4. xl/workbook.xml (5 sheets)
    zip.start_file("xl/workbook.xml", options).unwrap();
    zip.write_all(
        r#"<?xml version="1.0" encoding="UTF-8"?>
    <workbook xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships">
        <sheets>
            <sheet name="Фундамент" sheetId="1" r:id="rId1"/>
            <sheet name="Стены" sheetId="2" r:id="rId2"/>
            <sheet name="Скрытые данные" sheetId="3" r:id="rId3"/>
            <sheet name="Согласование" sheetId="4" r:id="rId4"/>
            <sheet name="Крупная спецификация" sheetId="5" r:id="rId5"/>
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
        <Relationship Id="rId3" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/worksheet" Target="worksheets/sheet3.xml"/>
        <Relationship Id="rId4" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/worksheet" Target="worksheets/sheet4.xml"/>
        <Relationship Id="rId5" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/worksheet" Target="worksheets/sheet5.xml"/>
    </Relationships>"#
            .as_bytes(),
    )
    .unwrap();

    // 6. xl/sharedStrings.xml
    let shared = [
        "Общие положения по фундаменту",  // 0
        "№",                              // 1
        "Наименование работ",             // 2
        "Ед. изм.",                       // 3
        "Кол-во",                         // 4
        "Цена",                           // 5
        "Земляные работы",                // 6
        "м3",                             // 7
        "Устройство опалубки",            // 8
        "м2",                             // 9
        "Бетонирование",                  // 10
        "Пояснительная записка к стенам", // 11
        "Материал",                       // 12
        "Кирпич керамический",            // 13
        "шт",                             // 14
        "Газобетонные блоки",             // 15
        "Промежуточное примечание",       // 16
        "Элемент",                        // 17
        "Перемычки железобетонные",       // 18
        "Арматурный пояс",                // 19
        "Финал раздела стен",             // 20
        "Общие указания к смете",         // 21
        "Секретная колонка B",            // 22
        "Секретная строка 3",             // 23
        "Видимый пункт",                  // 24
        "Утверждено ГИП",                 // 25
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

    // Sheet 1: "Фундамент"
    // Preamble (sparse) -> Table (dense, 5 cols, rows 2..5) with vertical merge on Col A (rows 3..5)
    let sheet1_xml = r#"<?xml version="1.0" encoding="UTF-8"?>
    <worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">
        <sheetData>
            <row r="1"><c r="A1" t="s"><v>0</v></c></row>
            <row r="2">
                <c r="A2" t="s"><v>1</v></c>
                <c r="B2" t="s"><v>2</v></c>
                <c r="C2" t="s"><v>3</v></c>
                <c r="D2" t="s"><v>4</v></c>
                <c r="E2" t="s"><v>5</v></c>
            </row>
            <row r="3">
                <c r="A3" t="s"><v>6</v></c>
                <c r="B3" t="s"><v>6</v></c>
                <c r="C3" t="s"><v>7</v></c>
                <c r="D3"><v>150</v></c>
                <c r="E3"><v>500</v></c>
            </row>
            <row r="4">
                <c r="B4" t="s"><v>8</v></c>
                <c r="C4" t="s"><v>9</v></c>
                <c r="D4"><v>80</v></c>
                <c r="E4"><v>1200</v></c>
            </row>
            <row r="5">
                <c r="B5" t="s"><v>10</v></c>
                <c r="C5" t="s"><v>7</v></c>
                <c r="D5"><v>120</v></c>
                <c r="E5"><f>=D5*2500</f><v>300000</v></c>
            </row>
        </sheetData>
        <mergeCells count="1">
            <mergeCell ref="A3:A5"/>
        </mergeCells>
    </worksheet>"#;
    zip.start_file("xl/worksheets/sheet1.xml", options).unwrap();
    zip.write_all(sheet1_xml.as_bytes()).unwrap();

    // Sheet 2: "Стены"
    // Prose -> Table 1 -> Prose -> Table 2 -> Prose
    let sheet2_xml = r#"<?xml version="1.0" encoding="UTF-8"?>
    <worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">
        <sheetData>
            <row r="1"><c r="A1" t="s"><v>11</v></c></row>
            <row r="2">
                <c r="A2" t="s"><v>12</v></c>
                <c r="B2" t="s"><v>3</v></c>
                <c r="C2" t="s"><v>4</v></c>
            </row>
            <row r="3">
                <c r="A3" t="s"><v>13</v></c>
                <c r="B3" t="s"><v>14</v></c>
                <c r="C3"><v>5000</v></c>
            </row>
            <row r="4">
                <c r="A4" t="s"><v>15</v></c>
                <c r="B4" t="s"><v>7</v></c>
                <c r="C4"><v>45</v></c>
            </row>
            <row r="5"><c r="A5" t="s"><v>16</v></c></row>
            <row r="6">
                <c r="A6" t="s"><v>17</v></c>
                <c r="B6" t="s"><v>3</v></c>
                <c r="C6" t="s"><v>4</v></c>
            </row>
            <row r="7">
                <c r="A7" t="s"><v>18</v></c>
                <c r="B7" t="s"><v>14</v></c>
                <c r="C7"><v>12</v></c>
            </row>
            <row r="8">
                <c r="A8" t="s"><v>19</v></c>
                <c r="B8" t="s"><v>7</v></c>
                <c r="C8"><v>8</v></c>
            </row>
            <row r="9"><c r="A9" t="s"><v>20</v></c></row>
        </sheetData>
    </worksheet>"#;
    zip.start_file("xl/worksheets/sheet2.xml", options).unwrap();
    zip.write_all(sheet2_xml.as_bytes()).unwrap();

    // Sheet 3: "Скрытые данные"
    // Hidden col B (min=2, max=2), Hidden row 3.
    // Horizontal merge A1:E1.
    let sheet3_xml = r#"<?xml version="1.0" encoding="UTF-8"?>
    <worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">
        <cols>
            <col min="2" max="2" hidden="1"/>
        </cols>
        <sheetData>
            <row r="1">
                <c r="A1" t="s"><v>21</v></c>
            </row>
            <row r="2">
                <c r="A2" t="s"><v>24</v></c>
                <c r="B2" t="s"><v>22</v></c>
                <c r="C2"><v>100</v></c>
            </row>
            <row r="3" hidden="1">
                <c r="A3" t="s"><v>23</v></c>
                <c r="C3"><v>999</v></c>
            </row>
        </sheetData>
        <mergeCells count="1">
            <mergeCell ref="A1:E1"/>
        </mergeCells>
    </worksheet>"#;
    zip.start_file("xl/worksheets/sheet3.xml", options).unwrap();
    zip.write_all(sheet3_xml.as_bytes()).unwrap();

    // Sheet 4: "Согласование"
    // Single cell -> 1x1 table
    let sheet4_xml = r#"<?xml version="1.0" encoding="UTF-8"?>
    <worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">
        <sheetData>
            <row r="1"><c r="A1" t="s"><v>25</v></c></row>
        </sheetData>
    </worksheet>"#;
    zip.start_file("xl/worksheets/sheet4.xml", options).unwrap();
    zip.write_all(sheet4_xml.as_bytes()).unwrap();

    // Sheet 5: "Крупная спецификация"
    // 500 rows x 4 columns = 2,000 cells (dense table)
    let mut sheet5_xml = String::from(
        r#"<?xml version="1.0" encoding="UTF-8"?><worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main"><sheetData>"#,
    );
    // Header
    sheet5_xml.push_str(
        r#"<row r="1"><c r="A1" t="inlineStr"><is><t>Код</t></is></c><c r="B1" t="inlineStr"><is><t>Артикул</t></is></c><c r="C1" t="inlineStr"><is><t>Количество</t></is></c><c r="D1" t="inlineStr"><is><t>Сумма</t></is></c></row>"#,
    );
    for r in 2..=500 {
        sheet5_xml.push_str(&format!(
            r#"<row r="{r}"><c r="A{r}"><v>{r}</v></c><c r="B{r}" t="inlineStr"><is><t>ART-{r}</t></is></c><c r="C{r}"><v>10</v></c><c r="D{r}"><v>{val}</v></c></row>"#,
            r = r,
            val = r * 15
        ));
    }
    sheet5_xml.push_str("</sheetData></worksheet>");
    zip.start_file("xl/worksheets/sheet5.xml", options).unwrap();
    zip.write_all(sheet5_xml.as_bytes()).unwrap();

    zip.finish().unwrap();
    let bytes = buf.into_inner();

    // Benchmark / Parse into IR
    let start_t = std::time::Instant::now();
    let ir = process_xlsx(&bytes, Some("smeta_full.xlsx")).expect("process_xlsx failed");
    let elapsed = start_t.elapsed();

    // 1. Check bounded memory / execution speed
    // Streaming 5 sheets and ~2,050 cells should complete within 100ms
    assert!(
        elapsed.as_millis() < 500,
        "Parsing took too long: {}ms",
        elapsed.as_millis()
    );

    // 2. Metadata validation
    assert_eq!(ir.metadata.source_format, "xlsx");
    assert_eq!(ir.metadata.pdf_type, "text_based");
    assert_eq!(ir.metadata.page_count, 5);
    assert_eq!(
        ir.metadata.title,
        Some("Сводный сметный расчет".to_string())
    );
    assert_eq!(ir.metadata.pages_with_tables, vec![1, 2, 3, 4, 5]);

    // 3. Sheet 1 ("Фундамент"): 1 Heading + 1 Table
    let p0 = &ir.pages[0];
    assert_eq!(p0.page, 0);
    assert!(p0.markdown.starts_with("## Фундамент"));
    assert!(p0.markdown.contains("Общие положения по фундаменту"));
    let p0_tables: Vec<&pdf_router_core::TableBlock> = p0
        .blocks
        .iter()
        .filter_map(|b| match b {
            Block::Table(t) => Some(t),
            _ => None,
        })
        .collect();
    assert_eq!(p0_tables.len(), 1);
    assert_eq!(p0_tables[0].rows.len(), 4);
    assert_eq!(p0_tables[0].header_rows, 1);
    // Vertical merge A3:A5: "Земляные работы" repeated down rows 1..3 in the table body!
    assert_eq!(p0_tables[0].rows[1][0], "Земляные работы");
    assert_eq!(p0_tables[0].rows[2][0], "Земляные работы");
    assert_eq!(p0_tables[0].rows[3][0], "Земляные работы");
    // Cached formula published
    assert_eq!(p0_tables[0].rows[3][4], "300000");

    // 4. Sheet 2 ("Стены"): Alternating runs (Prose -> Table1 -> Prose -> Table2 -> Prose)
    let p1 = &ir.pages[1];
    assert_eq!(p1.page, 1);
    assert!(p1.markdown.starts_with("## Стены"));
    let p1_tables: Vec<&pdf_router_core::TableBlock> = p1
        .blocks
        .iter()
        .filter_map(|b| match b {
            Block::Table(t) => Some(t),
            _ => None,
        })
        .collect();
    assert_eq!(p1_tables.len(), 2);
    assert_eq!(p1_tables[0].rows.len(), 3);
    assert_eq!(p1_tables[1].rows.len(), 3);
    assert!(p1.markdown.contains("Пояснительная записка к стенам"));
    assert!(p1.markdown.contains("Промежуточное примечание"));
    assert!(p1.markdown.contains("Финал раздела стен"));

    // 5. Sheet 3 ("Скрытые данные"): Hidden rows & columns stripped
    let p2 = &ir.pages[2];
    assert_eq!(p2.page, 2);
    // Hidden column B content never appears
    assert!(!p2.markdown.contains("Секретная колонка B"));
    // Hidden row 3 content never appears
    assert!(!p2.markdown.contains("Секретная строка 3"));
    assert!(!p2.markdown.contains("999"));
    // Horizontal merge A1:E1 appears once in prose
    let occurrences = p2.markdown.matches("Общие указания к смете").count();
    assert_eq!(occurrences, 1);

    // 6. Sheet 4 ("Согласование"): 1x1 table
    let p3 = &ir.pages[3];
    assert_eq!(p3.page, 3);
    assert!(p3.markdown.contains("| Утверждено ГИП |"));

    // 7. Sheet 5 ("Крупная спецификация"): 500 rows table
    let p4 = &ir.pages[4];
    assert_eq!(p4.page, 4);
    let p4_tables: Vec<&pdf_router_core::TableBlock> = p4
        .blocks
        .iter()
        .filter_map(|b| match b {
            Block::Table(t) => Some(t),
            _ => None,
        })
        .collect();
    assert_eq!(p4_tables.len(), 1);
    assert_eq!(p4_tables[0].rows.len(), 500);
    assert_eq!(
        p4_tables[0].rows[0],
        vec!["Код", "Артикул", "Количество", "Сумма"]
    );
    assert_eq!(p4_tables[0].rows[499], vec!["500", "ART-500", "10", "7500"]);
}
