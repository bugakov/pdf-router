//! Native DOCX streaming adapter.
//!
//! Pure Rust parser using `quick-xml` and `zip`.
//! Resolves styles, headings, tables (gridSpan, vMerge), and emits IR 0.6.

use std::collections::HashMap;
use std::io::{Cursor, Read};
use std::sync::LazyLock;

use quick_xml::events::Event;
use quick_xml::reader::Reader;
use regex::Regex;

use crate::error::RouterError;
use crate::ir::{Block, HeadingBlock, Ir, Metadata, PageResult, SrcTool, TableBlock};

static HEADING_NAME_RE: LazyLock<Regex> =
    LazyLock::new(|| Regex::new(r"(?i)^heading\s*(\d)$").expect("valid regex"));

#[derive(Debug, Clone, Default)]
pub struct StyleInfo {
    pub name: String,
    pub outline_level: Option<usize>,
    pub based_on: Option<String>,
}

/// Helper to get the local name of an XML tag or attribute (stripping `w:`, `w14:`, etc.)
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

/// Parse `word/styles.xml` into a mapping of `styleId -> StyleInfo`.
pub fn parse_styles_xml(xml: &str) -> HashMap<String, StyleInfo> {
    let mut reader = Reader::from_str(xml);
    reader.config_mut().trim_text(false);
    let mut buf = Vec::new();
    let mut styles = HashMap::new();

    let mut current_style_id: Option<String> = None;
    let mut current_name: Option<String> = None;
    let mut current_outline: Option<usize> = None;
    let mut current_based_on: Option<String> = None;

    loop {
        match reader.read_event_into(&mut buf) {
            Ok(Event::Start(ref e)) | Ok(Event::Empty(ref e)) => {
                let tag = local_name(e.name().into_inner());
                match tag {
                    b"style" => {
                        current_style_id = get_attribute_value(e, b"styleId");
                        current_name = None;
                        current_outline = None;
                        current_based_on = None;
                    }
                    b"name" => {
                        if let Some(val) = get_attribute_value(e, b"val") {
                            current_name = Some(val);
                        }
                    }
                    b"outlineLvl" => {
                        if let Some(val) = get_attribute_value(e, b"val") {
                            if let Ok(lvl) = val.parse::<usize>() {
                                if lvl <= 8 {
                                    current_outline = Some(lvl);
                                }
                            }
                        }
                    }
                    b"basedOn" => {
                        if let Some(val) = get_attribute_value(e, b"val") {
                            current_based_on = Some(val);
                        }
                    }
                    _ => {}
                }
            }
            Ok(Event::End(ref e)) => {
                let tag = local_name(e.name().into_inner());
                if tag == b"style" {
                    if let Some(id) = current_style_id.take() {
                        styles.insert(
                            id,
                            StyleInfo {
                                name: current_name.take().unwrap_or_default(),
                                outline_level: current_outline.take(),
                                based_on: current_based_on.take(),
                            },
                        );
                    }
                }
            }
            Ok(Event::Eof) => break,
            Err(_) => break,
            _ => {}
        }
        buf.clear();
    }

    styles
}

/// Parse `dc:title` from `docProps/core.xml`.
pub fn parse_core_title(xml: &str) -> Option<String> {
    let mut reader = Reader::from_str(xml);
    let mut buf = Vec::new();
    let mut in_title = false;
    let mut title = String::new();

    loop {
        match reader.read_event_into(&mut buf) {
            Ok(Event::Start(ref e)) => {
                if local_name(e.name().into_inner()) == b"title" {
                    in_title = true;
                }
            }
            Ok(Event::End(ref e)) => {
                if local_name(e.name().into_inner()) == b"title" {
                    in_title = false;
                }
            }
            Ok(Event::Text(ref e)) => {
                if in_title {
                    if let Ok(txt) = e.unescape() {
                        title.push_str(&txt);
                    }
                }
            }
            Ok(Event::Eof) => break,
            Err(_) => break,
            _ => {}
        }
        buf.clear();
    }

    let trimmed = title.trim();
    if !trimmed.is_empty() {
        Some(trimmed.to_string())
    } else {
        None
    }
}

/// Resolve heading level (1-6) for a given styleId using the `styles` map.
pub fn style_heading_level(
    style_id: Option<&str>,
    styles: &HashMap<String, StyleInfo>,
) -> Option<usize> {
    let mut seen = std::collections::HashSet::new();
    let mut sid = style_id;

    while let Some(current_id) = sid {
        if seen.contains(current_id) {
            break;
        }
        seen.insert(current_id);

        if let Some(info) = styles.get(current_id) {
            if let Some(outline) = info.outline_level {
                if outline <= 8 {
                    return Some((outline + 1).min(6));
                }
            }
            if let Some(caps) = HEADING_NAME_RE.captures(&info.name) {
                if let Ok(lvl) = caps[1].parse::<usize>() {
                    return Some(lvl.clamp(1, 6));
                }
            }
            if info.name.eq_ignore_ascii_case("title") {
                return Some(1);
            }
            sid = info.based_on.as_deref();
        } else {
            // Raw styleId fallback: e.g. "Heading1" -> 1
            if let Some(caps) = HEADING_NAME_RE.captures(current_id) {
                if let Ok(lvl) = caps[1].parse::<usize>() {
                    return Some(lvl.clamp(1, 6));
                }
            }
            break;
        }
    }

    None
}

/// Convert exact 2D rows into Markdown pipe-table syntax.
pub fn rows_to_markdown(rows: &[Vec<String>]) -> String {
    if rows.is_empty() {
        return String::new();
    }
    let width = rows.iter().map(|r| r.len()).max().unwrap_or(0);
    if width == 0 {
        return String::new();
    }

    let format_line = |cells: &[String]| -> String {
        let mut parts = Vec::with_capacity(width);
        for i in 0..width {
            let cell = cells.get(i).map(|s| s.as_str()).unwrap_or("");
            let escaped = cell.replace('|', "\\|").trim().to_string();
            parts.push(escaped);
        }
        format!("| {} |", parts.join(" | "))
    };

    let header_sep = format!("|{}|", vec![" --- "; width].join("|"));
    let mut lines = Vec::with_capacity(rows.len() + 1);
    lines.push(format_line(&rows[0]));
    lines.push(header_sep);
    for row in &rows[1..] {
        lines.push(format_line(row));
    }
    lines.join("\n")
}

#[derive(Debug, Clone)]
struct CellDraft {
    text: String,
    gridspan: usize,
    vmerge: Option<String>,
}

/// Process a DOCX archive from in-memory bytes into an IR 0.6 document.
pub fn process_docx(bytes: &[u8], source_path: Option<&str>) -> Result<Ir, RouterError> {
    let mut archive = zip::ZipArchive::new(Cursor::new(bytes))
        .map_err(|e| RouterError::InvalidDocument(format!("malformed docx (bad zip): {e}")))?;

    // 1. Load styles if available
    let styles = if let Ok(mut entry) = archive.by_name("word/styles.xml") {
        let mut xml = String::new();
        entry.read_to_string(&mut xml).unwrap_or_default();
        parse_styles_xml(&xml)
    } else {
        HashMap::new()
    };

    // 2. Load core title if available
    let title = if let Ok(mut entry) = archive.by_name("docProps/core.xml") {
        let mut xml = String::new();
        entry.read_to_string(&mut xml).unwrap_or_default();
        parse_core_title(&xml)
    } else {
        None
    };

    // 3. Read word/document.xml
    let doc_xml = {
        let mut entry = archive.by_name("word/document.xml").map_err(|e| {
            RouterError::InvalidDocument(format!("malformed docx (no word/document.xml): {e}"))
        })?;
        let mut xml = String::new();
        entry.read_to_string(&mut xml).map_err(|e| {
            RouterError::InvalidDocument(format!("unreadable word/document.xml: {e}"))
        })?;
        xml
    };

    let mut reader = Reader::from_str(&doc_xml);
    reader.config_mut().trim_text(false);
    let mut buf = Vec::new();

    let mut md_parts: Vec<String> = Vec::new();
    let mut blocks: Vec<Block> = Vec::new();
    let mut table_count = 0;

    // State machine flags
    let mut in_tc = false;
    let mut in_p = false;
    let mut in_t = false;

    // Paragraph state
    let mut p_text = String::new();
    let mut p_style: Option<String> = None;
    let mut p_outline: Option<usize> = None;

    // Cell state
    let mut cell_paragraphs: Vec<String> = Vec::new();
    let mut current_gridspan = 1;
    let mut current_vmerge: Option<String> = None;

    // Table state
    let mut current_row: Vec<CellDraft> = Vec::new();
    let mut table_rows: Vec<Vec<String>> = Vec::new();
    let mut open_merge: HashMap<usize, String> = HashMap::new();

    loop {
        match reader.read_event_into(&mut buf) {
            Ok(Event::Start(ref e)) => {
                let tag = local_name(e.name().into_inner());
                match tag {
                    b"tbl" => {
                        open_merge.clear();
                        table_rows.clear();
                    }
                    b"tr" => {
                        current_row.clear();
                    }
                    b"tc" => {
                        in_tc = true;
                        cell_paragraphs.clear();
                        current_gridspan = 1;
                        current_vmerge = None;
                    }
                    b"p" => {
                        in_p = true;
                        p_text.clear();
                        p_style = None;
                        p_outline = None;
                    }
                    b"pStyle" => {
                        if let Some(val) = get_attribute_value(e, b"val") {
                            p_style = Some(val);
                        }
                    }
                    b"outlineLvl" => {
                        if let Some(val) = get_attribute_value(e, b"val") {
                            if let Ok(lvl) = val.parse::<usize>() {
                                if lvl <= 8 {
                                    p_outline = Some(lvl);
                                }
                            }
                        }
                    }
                    b"gridSpan" => {
                        if let Some(val) = get_attribute_value(e, b"val") {
                            if let Ok(span) = val.parse::<usize>() {
                                current_gridspan = span.max(1);
                            }
                        }
                    }
                    b"vMerge" => {
                        let val = get_attribute_value(e, b"val")
                            .unwrap_or_else(|| "continue".to_string());
                        current_vmerge = Some(val);
                    }
                    b"t" => {
                        in_t = true;
                    }
                    _ => {}
                }
            }
            Ok(Event::Empty(ref e)) => {
                let tag = local_name(e.name().into_inner());
                match tag {
                    b"pStyle" => {
                        if let Some(val) = get_attribute_value(e, b"val") {
                            p_style = Some(val);
                        }
                    }
                    b"outlineLvl" => {
                        if let Some(val) = get_attribute_value(e, b"val") {
                            if let Ok(lvl) = val.parse::<usize>() {
                                if lvl <= 8 {
                                    p_outline = Some(lvl);
                                }
                            }
                        }
                    }
                    b"gridSpan" => {
                        if let Some(val) = get_attribute_value(e, b"val") {
                            if let Ok(span) = val.parse::<usize>() {
                                current_gridspan = span.max(1);
                            }
                        }
                    }
                    b"vMerge" => {
                        let val = get_attribute_value(e, b"val")
                            .unwrap_or_else(|| "continue".to_string());
                        current_vmerge = Some(val);
                    }
                    b"tab" => {
                        if in_p {
                            p_text.push('\t');
                        }
                    }
                    b"br" | b"cr" => {
                        if in_p {
                            p_text.push('\n');
                        }
                    }
                    _ => {}
                }
            }
            Ok(Event::Text(ref e)) => {
                if in_t && in_p {
                    if let Ok(txt) = e.unescape() {
                        p_text.push_str(&txt);
                    }
                }
            }
            Ok(Event::End(ref e)) => {
                let tag = local_name(e.name().into_inner());
                match tag {
                    b"t" => {
                        in_t = false;
                    }
                    b"p" => {
                        in_p = false;
                        let trimmed = p_text.trim();
                        if in_tc {
                            if !trimmed.is_empty() {
                                cell_paragraphs.push(trimmed.to_string());
                            }
                        } else if !trimmed.is_empty() {
                            let heading_level = p_outline
                                .map(|o| (o + 1).min(6))
                                .or_else(|| style_heading_level(p_style.as_deref(), &styles));

                            if let Some(lvl) = heading_level {
                                let md_line = format!("{} {}", "#".repeat(lvl), trimmed);
                                md_parts.push(md_line);
                                blocks.push(Block::Heading(HeadingBlock {
                                    level: lvl,
                                    text: trimmed.to_string(),
                                    bbox: None,
                                    source: "docx_style".to_string(),
                                }));
                            } else {
                                md_parts.push(trimmed.to_string());
                            }
                        }
                    }
                    b"tc" => {
                        in_tc = false;
                        let cell_text = cell_paragraphs
                            .join(" ")
                            .replace('\t', " ")
                            .replace('\n', " ")
                            .trim()
                            .to_string();
                        current_row.push(CellDraft {
                            text: cell_text,
                            gridspan: current_gridspan,
                            vmerge: current_vmerge.take(),
                        });
                    }
                    b"tr" => {
                        let mut row_cells = Vec::new();
                        let mut col = 0;
                        for cell in current_row.drain(..) {
                            let text = if cell.vmerge.as_deref() == Some("continue") {
                                open_merge.get(&col).cloned().unwrap_or(cell.text)
                            } else {
                                open_merge.insert(col, cell.text.clone());
                                cell.text
                            };
                            for _ in 0..cell.gridspan {
                                row_cells.push(text.clone());
                            }
                            col += cell.gridspan;
                        }
                        table_rows.push(row_cells);
                    }
                    b"tbl" => {
                        if !table_rows.is_empty() {
                            table_count += 1;
                            let width = table_rows.iter().map(|r| r.len()).max().unwrap_or(0);
                            for row in &mut table_rows {
                                while row.len() < width {
                                    row.push(String::new());
                                }
                            }
                            let table_md = rows_to_markdown(&table_rows);
                            let header_rows = if table_rows.len() > 1 { 1 } else { 0 };
                            blocks.push(Block::Table(TableBlock {
                                markdown: table_md.clone(),
                                rows: table_rows.clone(),
                                header_rows,
                                bbox: None,
                                source: "markdown_pipe".to_string(),
                            }));
                            md_parts.push(table_md);
                        }
                    }
                    _ => {}
                }
            }
            Ok(Event::Eof) => break,
            Err(e) => {
                return Err(RouterError::InvalidDocument(format!(
                    "XML parse error in word/document.xml: {e}"
                )));
            }
            _ => {}
        }
        buf.clear();
    }

    let markdown = md_parts.join("\n\n").trim().to_string();

    let metadata = Metadata {
        source_format: "docx".to_string(),
        source_path: source_path.map(|s| s.to_string()),
        pdf_type: "text_based".to_string(),
        page_count: 1,
        confidence: 1.0,
        title,
        pages_with_tables: if table_count > 0 { vec![1] } else { vec![] },
        ..Default::default()
    };

    let page = PageResult {
        page: 0,
        markdown,
        src_tool: SrcTool::DocxAdapter,
        blocks,
        images: vec![],
        error: None,
        needs_ocr: false,
    };

    Ok(Ir::new(metadata, vec![page]))
}
