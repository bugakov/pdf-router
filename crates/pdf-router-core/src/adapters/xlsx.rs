//! Native streaming XLSX adapter.
//!
//! Pure Rust parser using `quick-xml` and `zip`.
//! Treats each sheet as a page (`## <sheet name>`), segments dense rows
//! (>=50% visible columns with content) into `TableBlock`s and sparse rows
//! into prose, strips hidden rows and columns, repeats vertical merges down,
//! and emits IR 0.6.

use std::collections::{HashMap, HashSet};
use std::io::{Cursor, Read};

use quick_xml::events::Event;
use quick_xml::reader::Reader;

use crate::adapters::docx::{parse_core_title, rows_to_markdown};
use crate::error::RouterError;
use crate::ir::{Block, HeadingBlock, Ir, Metadata, PageResult, SrcTool, TableBlock};

/// Helper to get the local name of an XML tag or attribute (stripping `w:`, `r:`, etc.)
pub fn local_name(name: &[u8]) -> &[u8] {
    if let Some(pos) = name.iter().position(|&b| b == b':') {
        &name[pos + 1..]
    } else {
        name
    }
}

/// Helper to get an attribute value matching a local name
fn get_attribute_value(e: &quick_xml::events::BytesStart, attr_name: &[u8]) -> Option<String> {
    for attr in e.attributes().flatten() {
        if local_name(attr.key.into_inner()) == attr_name {
            if let Ok(v) = attr.unescape_value() {
                return Some(v.into_owned());
            }
        }
    }
    None
}

/// "BC12" -> Some((12, 55)); None when the reference is malformed.
pub fn split_ref(ref_str: &str) -> Option<(usize, usize)> {
    let bytes = ref_str.as_bytes();
    let num_start = bytes.iter().position(|b| b.is_ascii_digit())?;
    if num_start == 0 || num_start == bytes.len() {
        return None;
    }
    let letters = &bytes[..num_start];
    let digits = &bytes[num_start..];

    let mut col: usize = 0;
    for &b in letters {
        if !b.is_ascii_uppercase() {
            return None;
        }
        col = col.checked_mul(26)?.checked_add((b - b'A' + 1) as usize)?;
    }

    let row_str = std::str::from_utf8(digits).ok()?;
    let row = row_str.parse::<usize>().ok()?;
    if row == 0 || col == 0 {
        return None;
    }
    Some((row, col))
}

/// Clean raw numeric string to kill float noise (e.g. 4.8000000000000001E-2 -> 0.048).
pub fn clean_number_text(text: &str) -> String {
    if let Ok(f) = text.parse::<f64>() {
        if f.is_finite() && f.fract() == 0.0 && f >= i64::MIN as f64 && f <= i64::MAX as f64 {
            return (f as i64).to_string();
        }
        return f.to_string();
    }
    text.to_string()
}

/// Parse `xl/sharedStrings.xml` into a list of strings.
pub fn parse_shared_strings(xml: &str) -> Vec<String> {
    let mut reader = Reader::from_str(xml);
    reader.config_mut().trim_text(false);
    let mut buf = Vec::new();
    let mut strings = Vec::new();

    let mut in_si = false;
    let mut in_t = false;
    let mut current_si = String::new();

    loop {
        match reader.read_event_into(&mut buf) {
            Ok(Event::Start(ref e)) => match local_name(e.name().into_inner()) {
                b"si" => {
                    in_si = true;
                    current_si.clear();
                }
                b"t" if in_si => {
                    in_t = true;
                }
                _ => {}
            },
            Ok(Event::Text(ref e)) if in_t => {
                if let Ok(txt) = e.unescape() {
                    current_si.push_str(&txt);
                }
            }
            Ok(Event::End(ref e)) => match local_name(e.name().into_inner()) {
                b"t" => {
                    in_t = false;
                }
                b"si" => {
                    in_si = false;
                    strings.push(current_si.clone());
                }
                _ => {}
            },
            Ok(Event::Eof) | Err(_) => break,
            _ => {}
        }
        buf.clear();
    }

    strings
}

/// Relationship mapping (Id -> internal part path in archive).
pub fn parse_rels(xml: &str) -> HashMap<String, String> {
    let mut reader = Reader::from_str(xml);
    reader.config_mut().trim_text(false);
    let mut buf = Vec::new();
    let mut rels = HashMap::new();

    loop {
        match reader.read_event_into(&mut buf) {
            Ok(Event::Start(ref e)) | Ok(Event::Empty(ref e)) => {
                if local_name(e.name().into_inner()) == b"Relationship" {
                    let id = get_attribute_value(e, b"Id");
                    let target = get_attribute_value(e, b"Target");
                    let target_mode = get_attribute_value(e, b"TargetMode");
                    if target_mode.as_deref() != Some("External") {
                        if let (Some(id), Some(mut target)) = (id, target) {
                            if target.starts_with('/') {
                                target = target.trim_start_matches('/').to_string();
                            } else if !target.starts_with("xl/") {
                                target = format!("xl/{target}");
                            }
                            rels.insert(id, target);
                        }
                    }
                }
            }
            Ok(Event::Eof) | Err(_) => break,
            _ => {}
        }
        buf.clear();
    }

    rels
}

#[derive(Debug, Clone)]
pub struct SheetDef {
    pub name: String,
    pub rel_id: String,
}

/// Parse sheet entries from `xl/workbook.xml` in workbook order.
pub fn parse_workbook_sheets(xml: &str) -> Result<Vec<SheetDef>, RouterError> {
    let mut reader = Reader::from_str(xml);
    reader.config_mut().trim_text(false);
    let mut buf = Vec::new();
    let mut sheets = Vec::new();
    let mut sheet_idx = 1;

    loop {
        match reader.read_event_into(&mut buf) {
            Ok(Event::Start(ref e)) | Ok(Event::Empty(ref e)) => {
                if local_name(e.name().into_inner()) == b"sheet" {
                    let name = get_attribute_value(e, b"name")
                        .unwrap_or_else(|| format!("Sheet{}", sheet_idx));
                    let rel_id = get_attribute_value(e, b"id").unwrap_or_default();
                    sheets.push(SheetDef { name, rel_id });
                    sheet_idx += 1;
                }
            }
            Ok(Event::Eof) => break,
            Err(e) => {
                return Err(RouterError::InvalidDocument(format!(
                    "malformed xlsx (unreadable xl/workbook.xml): {e}"
                )));
            }
            _ => {}
        }
        buf.clear();
    }

    if sheets.is_empty() {
        return Err(RouterError::InvalidDocument(
            "malformed xlsx (no sheets found in xl/workbook.xml)".to_string(),
        ));
    }

    Ok(sheets)
}

/// Parsed contents of a single worksheet part.
#[derive(Debug, Default)]
struct WorksheetData {
    hidden_cols: HashSet<usize>,
    hidden_rows: HashSet<usize>,
    cells: HashMap<(usize, usize), String>,
    merges: Vec<(usize, usize, usize, usize)>,
}

/// Extract hidden column range if `<col>` has hidden="1".
fn extract_hidden_col(e: &quick_xml::events::BytesStart, hidden_cols: &mut HashSet<usize>) {
    if get_attribute_value(e, b"hidden")
        .map(|h| h == "1" || h.eq_ignore_ascii_case("true"))
        .unwrap_or(false)
    {
        let min = get_attribute_value(e, b"min").and_then(|v| v.parse::<usize>().ok());
        let max = get_attribute_value(e, b"max").and_then(|v| v.parse::<usize>().ok());
        if let (Some(lo), Some(hi)) = (min, max) {
            if lo >= 1 && lo <= hi {
                for c in lo..=hi {
                    hidden_cols.insert(c);
                }
            }
        }
    }
}

/// Extract `<mergeCell ref="A1:B2"/>` range.
fn extract_merge_cell(
    e: &quick_xml::events::BytesStart,
    merges: &mut Vec<(usize, usize, usize, usize)>,
) {
    if let Some(ref_val) = get_attribute_value(e, b"ref") {
        let parts: Vec<&str> = ref_val.split(':').collect();
        if parts.len() == 2 {
            if let (Some(a), Some(b)) = (split_ref(parts[0]), split_ref(parts[1])) {
                merges.push((a.0.min(b.0), a.1.min(b.1), a.0.max(b.0), a.1.max(b.1)));
            }
        }
    }
}

/// Parse a `worksheets/sheet*.xml` stream.
fn parse_worksheet_xml(xml: &str, shared: &[String]) -> Result<WorksheetData, RouterError> {
    let mut reader = Reader::from_str(xml);
    reader.config_mut().trim_text(false);
    let mut buf = Vec::new();

    let mut data = WorksheetData::default();

    // Cell parser state
    let mut in_c = false;
    let mut current_pos: Option<(usize, usize)> = None;
    let mut current_type = String::new();
    let mut in_v = false;
    let mut in_is = false;
    let mut in_t = false;
    let mut v_text = String::new();
    let mut is_text = String::new();

    loop {
        match reader.read_event_into(&mut buf) {
            Ok(Event::Start(ref e)) => {
                let tag = local_name(e.name().into_inner());
                match tag {
                    b"row" => {
                        let is_hidden = get_attribute_value(e, b"hidden")
                            .map(|h| h == "1" || h.eq_ignore_ascii_case("true"))
                            .unwrap_or(false);
                        if is_hidden {
                            if let Some(r) =
                                get_attribute_value(e, b"r").and_then(|r| r.parse::<usize>().ok())
                            {
                                data.hidden_rows.insert(r);
                            }
                        }
                    }
                    b"col" => {
                        extract_hidden_col(e, &mut data.hidden_cols);
                    }
                    b"c" => {
                        in_c = true;
                        current_type = get_attribute_value(e, b"t").unwrap_or_default();
                        current_pos = get_attribute_value(e, b"r").and_then(|r| split_ref(&r));
                        v_text.clear();
                        is_text.clear();
                    }
                    b"v" if in_c => {
                        in_v = true;
                    }
                    b"is" if in_c => {
                        in_is = true;
                    }
                    b"t" if in_is => {
                        in_t = true;
                    }
                    b"mergeCell" => {
                        extract_merge_cell(e, &mut data.merges);
                    }
                    _ => {}
                }
            }
            Ok(Event::Empty(ref e)) => {
                let tag = local_name(e.name().into_inner());
                match tag {
                    b"col" => {
                        extract_hidden_col(e, &mut data.hidden_cols);
                    }
                    b"mergeCell" => {
                        extract_merge_cell(e, &mut data.merges);
                    }
                    _ => {}
                }
            }
            Ok(Event::Text(ref e)) => {
                if in_v {
                    if let Ok(txt) = e.unescape() {
                        v_text.push_str(&txt);
                    }
                } else if in_t {
                    if let Ok(txt) = e.unescape() {
                        is_text.push_str(&txt);
                    }
                }
            }
            Ok(Event::End(ref e)) => {
                let tag = local_name(e.name().into_inner());
                match tag {
                    b"v" => {
                        in_v = false;
                    }
                    b"t" => {
                        in_t = false;
                    }
                    b"is" => {
                        in_is = false;
                    }
                    b"c" => {
                        in_c = false;
                        if let Some(pos) = current_pos.take() {
                            let (row, col) = pos;
                            if !data.hidden_cols.contains(&col) && !data.hidden_rows.contains(&row)
                            {
                                let raw_val = if current_type == "inlineStr" {
                                    is_text.clone()
                                } else if !v_text.is_empty() {
                                    if current_type == "s" {
                                        if let Ok(idx) = v_text.parse::<usize>() {
                                            shared.get(idx).cloned().unwrap_or_default()
                                        } else {
                                            String::new()
                                        }
                                    } else if matches!(current_type.as_str(), "str" | "e" | "b") {
                                        v_text.clone()
                                    } else {
                                        clean_number_text(&v_text)
                                    }
                                } else {
                                    String::new()
                                };

                                let cleaned = raw_val.replace(['\t', '\n'], " ");
                                if !cleaned.trim().is_empty() {
                                    data.cells.insert((row, col), cleaned);
                                }
                            }
                        }
                    }
                    _ => {}
                }
            }
            Ok(Event::Eof) => break,
            Err(e) => {
                return Err(RouterError::InvalidDocument(format!(
                    "malformed xlsx (unreadable worksheet XML): {e}"
                )));
            }
            _ => {}
        }
        buf.clear();
    }

    Ok(data)
}

/// Vertical span: value repeated down. Horizontal span: first cell only.
fn apply_merges(
    cells: &mut HashMap<(usize, usize), String>,
    merges: &[(usize, usize, usize, usize)],
    width: usize,
    hidden_cols: &HashSet<usize>,
    hidden_rows: &HashSet<usize>,
) {
    for &(r1, c1, r2, c2) in merges {
        if c1 > width || hidden_cols.contains(&c1) || hidden_rows.contains(&r1) {
            continue;
        }
        let val = cells.get(&(r1, c1)).cloned().unwrap_or_default();
        for r in r1..=r2 {
            if hidden_rows.contains(&r) {
                continue;
            }
            cells.insert((r, c1), val.clone());
            for c in (c1 + 1)..=c2.min(width) {
                if hidden_cols.contains(&c) {
                    continue;
                }
                cells.insert((r, c), String::new());
            }
        }
    }
}

/// Convert cell map to rectangular 2D rows (empty rows trimmed).
fn build_grid(cells: &HashMap<(usize, usize), String>, width: usize) -> Vec<Vec<String>> {
    let mut row_indices: Vec<usize> = cells.keys().map(|&(r, _)| r).collect();
    row_indices.sort_unstable();
    row_indices.dedup();

    let mut out = Vec::new();
    for r in row_indices {
        let mut row = Vec::with_capacity(width);
        let mut has_any = false;
        for c in 1..=width {
            let val = cells.get(&(r, c)).cloned().unwrap_or_default();
            if !val.is_empty() {
                has_any = true;
            }
            row.push(val);
        }
        if has_any {
            out.push(row);
        }
    }
    out
}

/// Dense = at least half the visible columns carry content.
fn is_dense(row: &[String], visible_cols: usize) -> bool {
    if visible_cols == 0 {
        return false;
    }
    let content = row.iter().filter(|c| !c.trim().is_empty()).count();
    content * 2 >= visible_cols
}

/// Split rows into maximal dense / sparse runs.
fn partition_runs(rows: Vec<Vec<String>>, visible_cols: usize) -> Vec<(bool, Vec<Vec<String>>)> {
    let mut runs: Vec<(bool, Vec<Vec<String>>)> = Vec::new();
    for row in rows {
        let dense = is_dense(&row, visible_cols);
        if let Some(last) = runs.last_mut() {
            if last.0 == dense {
                last.1.push(row);
                continue;
            }
        }
        runs.push((dense, vec![row]));
    }
    runs
}

/// Leading rows where every cell is short text without digits.
fn header_rows(rows: &[Vec<String>]) -> usize {
    let mut n = 0;
    for row in rows {
        let cells: Vec<&str> = row
            .iter()
            .map(|c| c.trim())
            .filter(|c| !c.is_empty())
            .collect();
        if cells.is_empty() {
            break;
        }
        if !cells
            .iter()
            .all(|c| c.len() <= 60 && !c.chars().any(|ch| ch.is_ascii_digit()))
        {
            break;
        }
        n += 1;
    }
    n.min(rows.len().saturating_sub(1))
}

/// Build PageResult for one sheet.
fn build_sheet_page(index: usize, sheet_name: &str, mut ws: WorksheetData) -> PageResult {
    let width = ws.cells.keys().map(|&(_, c)| c).max().unwrap_or(0);
    apply_merges(
        &mut ws.cells,
        &ws.merges,
        width,
        &ws.hidden_cols,
        &ws.hidden_rows,
    );
    let visible_cols = width.saturating_sub(ws.hidden_cols.iter().filter(|&&c| c <= width).count());

    let mut md_parts = vec![format!("## {}", sheet_name)];
    let mut blocks: Vec<Block> = vec![Block::Heading(HeadingBlock {
        level: 2,
        text: sheet_name.to_string(),
        bbox: None,
        source: "xlsx_sheet".to_string(),
    })];

    let grid = build_grid(&ws.cells, width);
    for (dense, run_rows) in partition_runs(grid, visible_cols) {
        if dense {
            let table_md = rows_to_markdown(&run_rows);
            let h_rows = header_rows(&run_rows);
            blocks.push(Block::Table(TableBlock {
                markdown: table_md.clone(),
                rows: run_rows,
                header_rows: h_rows,
                bbox: None,
                source: "markdown_pipe".to_string(),
            }));
            md_parts.push(table_md);
        } else {
            for row in run_rows {
                let parts: Vec<&str> = row
                    .iter()
                    .map(|c| c.trim())
                    .filter(|c| !c.is_empty())
                    .collect();
                if !parts.is_empty() {
                    md_parts.push(parts.join(" "));
                }
            }
        }
    }

    PageResult {
        page: index,
        markdown: md_parts.join("\n\n").trim().to_string(),
        src_tool: SrcTool::XlsxAdapter,
        blocks,
        images: vec![],
        error: None,
        needs_ocr: false,
    }
}

/// Process an XLSX archive from in-memory bytes into an IR 0.6 document.
pub fn process_xlsx(bytes: &[u8], source_path: Option<&str>) -> Result<Ir, RouterError> {
    let mut archive = zip::ZipArchive::new(Cursor::new(bytes))
        .map_err(|e| RouterError::InvalidDocument(format!("malformed xlsx (bad zip): {e}")))?;

    // 1. Read xl/workbook.xml
    let wb_xml = {
        let mut entry = archive.by_name("xl/workbook.xml").map_err(|e| {
            RouterError::InvalidDocument(format!(
                "malformed xlsx (unreadable xl/workbook.xml): {e}"
            ))
        })?;
        let mut xml = String::new();
        entry.read_to_string(&mut xml).map_err(|e| {
            RouterError::InvalidDocument(format!(
                "malformed xlsx (unreadable xl/workbook.xml): {e}"
            ))
        })?;
        xml
    };

    // 2. Read xl/_rels/workbook.xml.rels
    let rels = if let Ok(mut entry) = archive.by_name("xl/_rels/workbook.xml.rels") {
        let mut xml = String::new();
        entry.read_to_string(&mut xml).unwrap_or_default();
        parse_rels(&xml)
    } else {
        HashMap::new()
    };

    // 3. Read xl/sharedStrings.xml if present
    let shared_strings = if let Ok(mut entry) = archive.by_name("xl/sharedStrings.xml") {
        let mut xml = String::new();
        entry.read_to_string(&mut xml).unwrap_or_default();
        parse_shared_strings(&xml)
    } else {
        Vec::new()
    };

    // 4. Read core title if present
    let title = if let Ok(mut entry) = archive.by_name("docProps/core.xml") {
        let mut xml = String::new();
        entry.read_to_string(&mut xml).unwrap_or_default();
        parse_core_title(&xml)
    } else {
        None
    };

    // 5. Parse sheets in order
    let sheet_defs = parse_workbook_sheets(&wb_xml)?;
    let mut pages = Vec::new();
    let mut pages_with_tables = Vec::new();

    for (index, sheet) in sheet_defs.into_iter().enumerate() {
        let part_path = rels
            .get(&sheet.rel_id)
            .cloned()
            .unwrap_or_else(|| format!("xl/worksheets/sheet{}.xml", index + 1));

        let sheet_xml = {
            let mut entry = archive.by_name(&part_path).map_err(|e| {
                RouterError::InvalidDocument(format!(
                    "malformed xlsx (unreadable {part_path}): {e}"
                ))
            })?;
            let mut xml = String::new();
            entry.read_to_string(&mut xml).map_err(|e| {
                RouterError::InvalidDocument(format!(
                    "malformed xlsx (unreadable {part_path}): {e}"
                ))
            })?;
            xml
        };

        let ws = parse_worksheet_xml(&sheet_xml, &shared_strings)?;
        let page = build_sheet_page(index, &sheet.name, ws);
        if page.blocks.iter().any(|b| matches!(b, Block::Table(_))) {
            pages_with_tables.push(index + 1);
        }
        pages.push(page);
    }

    let metadata = Metadata {
        source_format: "xlsx".to_string(),
        source_path: source_path.map(|s| s.to_string()),
        pdf_type: "text_based".to_string(),
        page_count: pages.len(),
        confidence: 1.0,
        title,
        pages_with_tables,
        ..Default::default()
    };

    Ok(Ir::new(metadata, pages))
}
