//! PDF inspector native adapter.
//!
//! Direct integration of `pdf-inspector` crate in pure Rust.
//! Extracts native digital text, structure trees for tagged PDFs (emitting `HeadingBlock`
//! with font-size fallback for non-tagged PDFs), and parses markdown pipe tables into `TableBlock`.

use std::collections::{BTreeMap, HashMap};
use std::sync::LazyLock;

use regex::Regex;

use pdf_inspector::detector::PdfType;
use pdf_inspector::extractor::extract_text_with_positions_mem;
use pdf_inspector::types::TextItem;
use pdf_inspector::{
    extract_pages_markdown_mem, extract_structure_elements_mem, process_pdf_mem, StructureElement,
};

use crate::error::RouterError;
use crate::ir::{
    BBox, Block, HeadingBlock, Ir, Metadata, OcrReasonEntry, PageResult, SrcTool, TableBlock,
};

static TABLE_ROW_RE: LazyLock<Regex> =
    LazyLock::new(|| Regex::new(r"^\s*\|.*\|\s*$").expect("valid regex"));

static TABLE_SEP_RE: LazyLock<Regex> = LazyLock::new(|| {
    Regex::new(r"^\s*\|?\s*:?-+:?\s*(\|\s*:?-+:?\s*)+\|?\s*$").expect("valid regex")
});

static H_ROLE_RE: LazyLock<Regex> =
    LazyLock::new(|| Regex::new(r"^H([1-6])$").expect("valid regex"));

/// Splits a markdown table row line into trimmed cell contents.
fn split_cells(line: &str) -> Vec<String> {
    let trimmed = line.trim();
    let trimmed_pipes = trimmed.trim_matches('|');
    trimmed_pipes
        .split('|')
        .map(|c| c.trim().to_string())
        .collect()
}

/// Parse a markdown pipe-table block into (rows, header_rows).
///
/// Returns `(vec![], 0)` if `md` is not a valid markdown table.
/// `header_rows` is 1 by default, or 2 if the second line is a wrapped continuation.
pub fn parse_markdown_table(md: &str) -> (Vec<Vec<String>>, usize) {
    let lines: Vec<&str> = md.lines().filter(|ln| !ln.trim().is_empty()).collect();
    if lines.len() < 2 || !TABLE_ROW_RE.is_match(lines[0]) || !TABLE_SEP_RE.is_match(lines[1]) {
        return (vec![], 0);
    }

    let mut row_lines: Vec<&str> = vec![lines[0]];
    for &ln in &lines[2..] {
        if TABLE_ROW_RE.is_match(ln) && !TABLE_SEP_RE.is_match(ln) {
            row_lines.push(ln);
        }
    }

    let mut header_rows = 1;
    if row_lines.len() >= 3 {
        let second_cells = split_cells(row_lines[1]);
        if !second_cells.is_empty()
            && second_cells
                .iter()
                .all(|c| !c.is_empty() && !c.chars().any(|ch| ch.is_numeric()) && c.len() < 40)
        {
            header_rows = 2;
        }
    }

    let rows: Vec<Vec<String>> = row_lines.into_iter().map(split_cells).collect();
    (rows, header_rows)
}

/// Find consecutive markdown-pipe-table blocks in a page's markdown.
pub fn extract_tables_from_markdown(page_md: &str) -> Vec<TableBlock> {
    let mut blocks = Vec::new();
    let lines: Vec<&str> = page_md.lines().collect();
    let mut i = 0;
    let n = lines.len();
    while i < n {
        if TABLE_ROW_RE.is_match(lines[i]) && i + 1 < n && TABLE_SEP_RE.is_match(lines[i + 1]) {
            let mut j = i + 2;
            while j < n && TABLE_ROW_RE.is_match(lines[j]) {
                j += 1;
            }
            let md = lines[i..j].join("\n");
            let (rows, hdr) = parse_markdown_table(&md);
            blocks.push(TableBlock {
                markdown: md,
                rows,
                header_rows: hdr,
                bbox: None,
                source: "markdown_pipe".to_string(),
            });
            i = j;
        } else {
            i += 1;
        }
    }
    blocks
}

/// Calculate the mode of font sizes, binning to 0.5pt to reduce float noise.
fn mode_font_size(values: &[f32]) -> f64 {
    if values.is_empty() {
        return 0.0;
    }
    let mut counts: HashMap<i64, usize> = HashMap::new();
    for &v in values {
        let bin = (v as f64 * 2.0).round() as i64;
        *counts.entry(bin).or_insert(0) += 1;
    }
    let mut best_bin = 0;
    let mut max_count = 0;
    for (&bin, &cnt) in &counts {
        if cnt > max_count {
            max_count = cnt;
            best_bin = bin;
        }
    }
    best_bin as f64 / 2.0
}

/// Detect headings by font-size outlier (1.3x body mode), per page.
pub fn headings_from_text_items(text_items: &[TextItem], page_0indexed: u32) -> Vec<HeadingBlock> {
    if text_items.is_empty() {
        return vec![];
    }
    // text_item.page is 1-indexed in pdf-inspector
    let page_items: Vec<&TextItem> = text_items
        .iter()
        .filter(|it| it.page.saturating_sub(1) == page_0indexed)
        .collect();
    if page_items.is_empty() {
        return vec![];
    }
    let font_sizes: Vec<f32> = page_items.iter().map(|it| it.font_size).collect();
    let body = mode_font_size(&font_sizes);
    if body <= 0.0 {
        return vec![];
    }
    let threshold = body * 1.3;

    // Group by (font_size, y) so we don't merge items on different visual lines
    let mut by_line: BTreeMap<(i64, i64), Vec<&TextItem>> = BTreeMap::new();
    for &it in &page_items {
        if it.font_size as f64 >= threshold && !it.text.trim().is_empty() {
            let sz_key = (it.font_size as f64 * 100.0).round() as i64;
            let y_key = (it.y as f64 * 10.0).round() as i64;
            by_line.entry((sz_key, y_key)).or_default().push(it);
        }
    }

    let mut line_entries: Vec<((i64, i64), Vec<&TextItem>)> = by_line.into_iter().collect();
    // Sort by y descending (PDF y goes up)
    line_entries.sort_by_key(|&((_, y_key), _)| -y_key);

    let mut out = Vec::new();
    for ((sz_key, _), mut items) in line_entries {
        items.sort_by(|a, b| a.x.partial_cmp(&b.x).unwrap_or(std::cmp::Ordering::Equal));
        let text_parts: Vec<&str> = items
            .iter()
            .map(|it| it.text.trim())
            .filter(|s| !s.is_empty())
            .collect();
        let text = text_parts.join(" ");
        if text.is_empty() {
            continue;
        }

        let sz = sz_key as f64 / 100.0;
        let level = if sz >= body * 1.8 {
            1
        } else if sz >= body * 1.5 {
            2
        } else {
            3
        };

        let x0 = items
            .iter()
            .map(|it| it.x as f64)
            .fold(f64::INFINITY, f64::min);
        let x1 = items
            .iter()
            .map(|it| (it.x + it.width) as f64)
            .fold(f64::NEG_INFINITY, f64::max);
        let y0 = items
            .iter()
            .map(|it| it.y as f64)
            .fold(f64::INFINITY, f64::min);
        let y1 = items
            .iter()
            .map(|it| (it.y + it.height) as f64)
            .fold(f64::NEG_INFINITY, f64::max);

        out.push(HeadingBlock {
            level,
            text,
            bbox: Some(BBox { x0, y0, x1, y1 }),
            source: "font_size_fallback".to_string(),
        });
    }

    out
}

/// Recover H1..H6 blocks from structure-tree + text items joined by (page, mcid).
pub fn headings_from_structure_tree(
    structure_elements: &[StructureElement],
    text_items: &[TextItem],
) -> HashMap<usize, Vec<HeadingBlock>> {
    let mut text_items_by_mcid: HashMap<(u32, i64), Vec<&TextItem>> = HashMap::new();
    for it in text_items {
        if let Some(mcid) = it.mcid {
            text_items_by_mcid
                .entry((it.page, mcid))
                .or_default()
                .push(it);
        }
    }

    let mut by_page: HashMap<usize, Vec<HeadingBlock>> = HashMap::new();
    for el in structure_elements {
        if let Some(caps) = H_ROLE_RE.captures(&el.role) {
            let level: usize = caps[1].parse().unwrap_or(1);
            if let Some(mut items) = text_items_by_mcid.get(&(el.page, el.mcid)).cloned() {
                if items.is_empty() {
                    continue;
                }
                items.sort_by(|a, b| a.x.partial_cmp(&b.x).unwrap_or(std::cmp::Ordering::Equal));
                let text_parts: Vec<&str> = items
                    .iter()
                    .map(|it| it.text.trim())
                    .filter(|s| !s.is_empty())
                    .collect();
                let text = text_parts.join(" ");
                if text.is_empty() {
                    continue;
                }

                let x0 = items
                    .iter()
                    .map(|it| it.x as f64)
                    .fold(f64::INFINITY, f64::min);
                let x1 = items
                    .iter()
                    .map(|it| (it.x + it.width) as f64)
                    .fold(f64::NEG_INFINITY, f64::max);
                let y0 = items
                    .iter()
                    .map(|it| it.y as f64)
                    .fold(f64::INFINITY, f64::min);
                let y1 = items
                    .iter()
                    .map(|it| (it.y + it.height) as f64)
                    .fold(f64::NEG_INFINITY, f64::max);

                let page_0indexed = el.page.saturating_sub(1) as usize;
                by_page
                    .entry(page_0indexed)
                    .or_default()
                    .push(HeadingBlock {
                        level,
                        text,
                        bbox: Some(BBox { x0, y0, x1, y1 }),
                        source: "structure_tree".to_string(),
                    });
            }
        }
    }

    by_page
}

fn pdf_type_to_str(pt: &PdfType) -> &'static str {
    match pt {
        PdfType::TextBased => "text_based",
        PdfType::Scanned => "scanned",
        PdfType::ImageBased => "image_based",
        PdfType::Mixed => "mixed",
    }
}

/// Process a PDF document in memory using `pdf-inspector`.
pub fn process_pdf(bytes: &[u8], source_path: Option<&str>) -> Result<Ir, RouterError> {
    let result = process_pdf_mem(bytes).map_err(|e| RouterError::InvalidDocument(e.to_string()))?;
    let pages_result = extract_pages_markdown_mem(bytes, None)
        .map_err(|e| RouterError::InvalidDocument(e.to_string()))?;

    let structure_elements = extract_structure_elements_mem(bytes, None).unwrap_or_default();
    let text_items = extract_text_with_positions_mem(bytes).unwrap_or_default();
    let headings_by_page_struct = headings_from_structure_tree(&structure_elements, &text_items);

    let pages_needing_ocr: Vec<usize> = result
        .pages_needing_ocr
        .iter()
        .map(|&p| p as usize)
        .collect();

    let pages_with_tables: Vec<usize> = if !result.layout.pages_with_tables.is_empty() {
        result
            .layout
            .pages_with_tables
            .into_iter()
            .map(|p| p as usize)
            .collect()
    } else {
        pages_result
            .pages_with_tables
            .into_iter()
            .map(|p| p as usize)
            .collect()
    };

    let pages_with_columns: Vec<usize> = if !result.layout.pages_with_columns.is_empty() {
        result
            .layout
            .pages_with_columns
            .into_iter()
            .map(|p| p as usize)
            .collect()
    } else {
        pages_result
            .pages_with_columns
            .into_iter()
            .map(|p| p as usize)
            .collect()
    };

    let ocr_reasons_by_page: Vec<OcrReasonEntry> = result
        .ocr_reasons_by_page
        .into_iter()
        .map(|r| OcrReasonEntry {
            page: r.page as usize,
            reasons: r.reasons,
        })
        .collect();

    let is_complex_layout = result.layout.is_complex || pages_result.is_complex;

    let metadata = Metadata {
        source_format: "pdf".to_string(),
        source_path: source_path.map(|s| s.to_string()),
        pdf_type: pdf_type_to_str(&result.pdf_type).to_string(),
        page_count: result.page_count as usize,
        confidence: result.confidence as f64,
        processing_time_ms: result.processing_time_ms as f64,
        title: result.title,
        is_complex_layout,
        has_encoding_issues: result.has_encoding_issues,
        pages_with_tables,
        pages_with_columns,
        pages_needing_ocr: pages_needing_ocr.clone(),
        ocr_reasons_by_page,
        unusable_text_pages: vec![],
    };

    let mut pages = Vec::with_capacity(pages_result.pages.len());
    for pm in pages_result.pages {
        let one_indexed = (pm.page + 1) as usize;
        let needs_ocr = pm.needs_ocr || pages_needing_ocr.contains(&one_indexed);

        let mut blocks: Vec<Block> = Vec::new();
        // Extract tables from markdown
        for tbl in extract_tables_from_markdown(&pm.markdown) {
            blocks.push(Block::Table(tbl));
        }

        // Headings: prefer structure tree, fallback to font-size detection
        let page_0indexed = pm.page as usize;
        if let Some(headings) = headings_by_page_struct.get(&page_0indexed) {
            for h in headings {
                blocks.push(Block::Heading(h.clone()));
            }
        } else {
            let font_headings = headings_from_text_items(&text_items, pm.page);
            for h in font_headings {
                blocks.push(Block::Heading(h));
            }
        }

        pages.push(PageResult {
            page: page_0indexed,
            markdown: pm.markdown,
            src_tool: SrcTool::PdfInspector,
            blocks,
            images: vec![],
            error: None,
            needs_ocr,
        });
    }

    Ok(Ir::new(metadata, pages))
}
