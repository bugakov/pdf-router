//! Legacy Word 97–2003 (.doc) inspector via `office-oxide` (IR 0.6).
//!
//! Conforms to ADR-0008:
//! - Parses OLE2/CFB containers in-process via `office_oxide::Document::from_reader`.
//! - Maps `to_ir()` representation to the router's IR 0.6:
//!   - Single `PageResult(page = 0)` for the whole document (born-digital prose).
//!   - Tables -> `TableBlock` with pipe markdown generated via `rows_to_markdown`.
//!   - Headings -> `HeadingBlock` with `source = "oxide_style"` (levels clamped 1..=6).
//!   - Images -> `FigureBlock` with `![](images/page0001_image_XX.ext)` placeholders.
//!   - Lists -> prefixed with `1. ` (ordered) or `- ` (unordered).
//!   - Document `<title>` from metadata block.
//! - Corrupt CFB/OLE2 triggers `RouterError::InvalidDocument`.
//! - Non-OLE2 files trigger `RouterError::UnsupportedFormat`.

use std::io::Cursor;
use std::time::Instant;

use office_oxide::{Document, DocumentFormat};

use crate::adapters::docx::rows_to_markdown;
use crate::error::RouterError;
use crate::ir::{Block, FigureBlock, HeadingBlock, Ir, Metadata, PageResult, SrcTool, TableBlock};

const OLE2_MAGIC: &[u8; 8] = &[0xD0, 0xCF, 0x11, 0xE0, 0xA1, 0xB1, 0x1A, 0xE1];

/// Known raster and vector image extensions.
const IMAGE_FORMATS: &[&str] = &[
    "jpeg", "jpg", "png", "gif", "bmp", "tiff", "tif", "emf", "wmf",
];

/// Join inline runs (`text` / `line_break`) into plain prose without markup noise.
fn run_text(content: Option<&Vec<serde_json::Value>>) -> String {
    let mut parts = Vec::new();
    if let Some(nodes) = content {
        for node in nodes {
            if let Some(kind) = node.get("type").and_then(|t| t.as_str()) {
                if kind == "text" {
                    if let Some(text) = node.get("text").and_then(|t| t.as_str()) {
                        parts.push(text);
                    }
                } else if kind == "line_break" {
                    parts.push(" ");
                }
            }
        }
    }
    parts.join("").replace(['\n', '\t'], " ").trim().to_string()
}

/// Extract pipe-safe table cell text (paragraphs joined with a single space).
fn cell_text(cell: &serde_json::Value) -> String {
    let mut paras = Vec::new();
    if let Some(content) = cell.get("content").and_then(|c| c.as_array()) {
        for p in content {
            if p.get("type").and_then(|t| t.as_str()) == Some("paragraph") {
                let t = run_text(p.get("content").and_then(|c| c.as_array()));
                if !t.is_empty() {
                    paras.push(t);
                }
            }
        }
    }
    paras
        .join(" ")
        .replace(['\n', '\t'], " ")
        .trim()
        .to_string()
}

/// Decode standard base64 payload into raw bytes.
fn decode_base64(input: &str) -> Option<Vec<u8>> {
    let mut out = Vec::with_capacity(input.len() * 3 / 4);
    let mut buf = 0u32;
    let mut bits = 0;
    for b in input.bytes() {
        if b.is_ascii_whitespace() || b == b'=' {
            continue;
        }
        let val = match b {
            b'A'..=b'Z' => b - b'A',
            b'a'..=b'z' => b - b'a' + 26,
            b'0'..=b'9' => b - b'0' + 52,
            b'+' => 62,
            b'/' => 63,
            _ => return None,
        } as u32;
        buf = (buf << 6) | val;
        bits += 6;
        if bits >= 8 {
            bits -= 8;
            out.push((buf >> bits) as u8);
        }
    }
    if out.is_empty() {
        None
    } else {
        Some(out)
    }
}

/// Determine sidecar image extension from metadata format string or magic bytes.
fn image_ext(img: &serde_json::Value, raw: &[u8]) -> &'static str {
    if let Some(fmt_val) = img.get("format").and_then(|f| f.as_str()) {
        let fmt_lower = fmt_val.to_ascii_lowercase();
        let cleaned: String = fmt_lower
            .chars()
            .filter(|c| c.is_ascii_alphanumeric())
            .collect();
        if IMAGE_FORMATS.contains(&cleaned.as_str()) {
            return match cleaned.as_str() {
                "jpg" => "jpeg",
                "tif" => "tiff",
                "jpeg" => "jpeg",
                "png" => "png",
                "gif" => "gif",
                "bmp" => "bmp",
                "tiff" => "tiff",
                "emf" => "emf",
                "wmf" => "wmf",
                _ => "bin",
            };
        }
    }

    if raw.starts_with(b"\xff\xd8") {
        "jpeg"
    } else if raw.starts_with(b"\x89PNG") {
        "png"
    } else if raw.starts_with(b"GIF87a") || raw.starts_with(b"GIF89a") {
        "gif"
    } else if raw.starts_with(b"BM") {
        "bmp"
    } else if raw.starts_with(b"II*\x00") || raw.starts_with(b"MM\x00*") {
        "tiff"
    } else if raw.starts_with(b"\x01\x00\x00\x00") {
        "emf"
    } else if raw.starts_with(b"\xd7\xcd\xc6\x9a") {
        "wmf"
    } else {
        "bin"
    }
}

/// Emit FigureBlock and markdown placeholder for an image element.
fn handle_figure(
    img: &serde_json::Value,
    blocks: &mut Vec<Block>,
    md_parts: &mut Vec<String>,
    image_count: &mut usize,
) {
    let data_str = match img.get("data").and_then(|d| d.as_str()) {
        Some(s) if !s.is_empty() => s,
        _ => return,
    };
    let raw = match decode_base64(data_str) {
        Some(b) => b,
        None => return,
    };
    let idx = *image_count;
    *image_count += 1;
    let ext = image_ext(img, &raw);
    blocks.push(Block::Figure(FigureBlock {
        bbox: None,
        caption: None,
        image_index: Some(idx),
    }));
    md_parts.push(format!("![](images/page0001_image_{idx:02}.{ext})"));
}

/// Document-order walk over oxide elements tree.
fn walk(
    node: &serde_json::Value,
    md_parts: &mut Vec<String>,
    blocks: &mut Vec<Block>,
    image_count: &mut usize,
) {
    if let Some(arr) = node.as_array() {
        for item in arr {
            walk(item, md_parts, blocks, image_count);
        }
        return;
    }
    if let Some(obj) = node.as_object() {
        let kind = obj.get("type").and_then(|t| t.as_str()).unwrap_or("");
        match kind {
            "heading" => {
                let text = run_text(obj.get("content").and_then(|c| c.as_array()));
                if !text.is_empty() {
                    let level_raw = obj.get("level").and_then(|l| l.as_u64()).unwrap_or(1) as usize;
                    let level = level_raw.clamp(1, 6);
                    md_parts.push(format!("{} {text}", "#".repeat(level)));
                    blocks.push(Block::Heading(HeadingBlock {
                        level,
                        text,
                        bbox: None,
                        source: "oxide_style".to_string(),
                    }));
                }
            }
            "paragraph" => {
                let mut buf = Vec::new();
                if let Some(content) = obj.get("content").and_then(|c| c.as_array()) {
                    for sub in content {
                        if sub.get("type").and_then(|t| t.as_str()) == Some("image") {
                            if !buf.is_empty() {
                                md_parts.push(buf.join(" ").trim().to_string());
                                buf.clear();
                            }
                            handle_figure(sub, blocks, md_parts, image_count);
                        } else {
                            let text = run_text(Some(&vec![sub.clone()]));
                            if !text.is_empty() {
                                buf.push(text);
                            }
                        }
                    }
                }
                if !buf.is_empty() {
                    md_parts.push(buf.join(" ").trim().to_string());
                }
            }
            "table" => {
                let mut rows: Vec<Vec<String>> = Vec::new();
                if let Some(table_rows) = obj.get("rows").and_then(|r| r.as_array()) {
                    for row_val in table_rows {
                        if let Some(cells) = row_val.get("cells").and_then(|c| c.as_array()) {
                            let r: Vec<String> = cells.iter().map(cell_text).collect();
                            rows.push(r);
                        }
                    }
                }
                if !rows.is_empty() {
                    let width = rows.iter().map(|r| r.len()).max().unwrap_or(0);
                    if width > 0 {
                        for r in &mut rows {
                            r.resize(width, String::new());
                        }
                        let md = rows_to_markdown(&rows);
                        blocks.push(Block::Table(TableBlock {
                            markdown: md.clone(),
                            rows: rows.clone(),
                            header_rows: if rows.len() > 1 { 1 } else { 0 },
                            bbox: None,
                            source: "native".to_string(),
                        }));
                        md_parts.push(md);
                    }
                }
            }
            "list" => {
                let ordered = obj
                    .get("ordered")
                    .and_then(|o| o.as_bool())
                    .unwrap_or(false);
                if let Some(items) = obj.get("items").and_then(|i| i.as_array()) {
                    for (i, item) in items.iter().enumerate() {
                        let pieces = run_text(item.get("content").and_then(|c| c.as_array()));
                        if !pieces.is_empty() {
                            let prefix = if ordered {
                                format!("{}. ", i + 1)
                            } else {
                                "- ".to_string()
                            };
                            md_parts.push(format!("{prefix}{pieces}"));
                        }
                    }
                }
            }
            "text" => {
                if let Some(text) = obj.get("text").and_then(|t| t.as_str()) {
                    let cleaned = text.replace('\n', " ").trim().to_string();
                    if !cleaned.is_empty() {
                        md_parts.push(cleaned);
                    }
                }
            }
            "image" => {
                handle_figure(node, blocks, md_parts, image_count);
            }
            _ => {
                // Unknown element kinds: recursively walk nested collections
                for val in obj.values() {
                    if val.is_object() || val.is_array() {
                        walk(val, md_parts, blocks, image_count);
                    }
                }
            }
        }
    }
}

/// Process raw legacy Word .doc bytes into IR 0.6.
pub fn process_doc(bytes: &[u8], source_path: Option<&str>) -> Result<Ir, RouterError> {
    let t0 = Instant::now();

    // 1. Sniff OLE2 magic header
    if !bytes.starts_with(OLE2_MAGIC) {
        return Err(RouterError::UnsupportedFormat(
            "non-OLE2 document: invalid magic header".to_string(),
        ));
    }

    // 2. Parse OLE2/CFB container via office-oxide
    let doc = Document::from_reader(Cursor::new(bytes.to_vec()), DocumentFormat::Doc)
        .map_err(|e| RouterError::InvalidDocument(format!("malformed doc (office-oxide): {e}")))?;

    // 3. Extract Intermediate Representation
    let ir = doc.to_ir();
    let json_str = ir.to_json_string().map_err(|e| {
        RouterError::InvalidDocument(format!("malformed doc (office-oxide IR): {e}"))
    })?;

    let val: serde_json::Value = serde_json::from_str(&json_str)?;

    let sections = val
        .get("sections")
        .and_then(|s| s.as_array())
        .ok_or_else(|| {
            RouterError::InvalidDocument(format!(
                "malformed doc (office-oxide returned no sections): {}",
                source_path.unwrap_or("")
            ))
        })?;

    let mut md_parts = Vec::new();
    let mut blocks = Vec::new();
    let mut image_count = 0;

    for section in sections {
        // Section titles are intentionally dropped (duplicates in-body opening heading)
        if let Some(elements) = section.get("elements").and_then(|e| e.as_array()) {
            for el in elements {
                walk(el, &mut md_parts, &mut blocks, &mut image_count);
            }
        }
    }

    let meta = val.get("metadata");
    let title = meta
        .and_then(|m| m.get("title"))
        .and_then(|t| t.as_str())
        .map(|s| s.trim())
        .filter(|s| !s.is_empty())
        .map(|s| s.to_string());

    let has_tables = blocks.iter().any(|b| matches!(b, Block::Table(_)));
    let markdown = md_parts
        .into_iter()
        .filter(|p| !p.is_empty())
        .collect::<Vec<_>>()
        .join("\n\n")
        .trim()
        .to_string();

    let elapsed_ms = t0.elapsed().as_secs_f64() * 1000.0;

    let metadata = Metadata {
        source_format: "doc".to_string(),
        source_path: source_path.map(|s| s.to_string()),
        pdf_type: "text_based".to_string(),
        page_count: 1,
        confidence: 1.0,
        processing_time_ms: (elapsed_ms * 10.0).round() / 10.0,
        title,
        is_complex_layout: false,
        pages_with_tables: if has_tables { vec![1] } else { vec![] },
        ..Default::default()
    };

    let page = PageResult {
        page: 0,
        markdown,
        src_tool: SrcTool::DocAdapter,
        blocks,
        images: vec![],
        error: None,
        needs_ocr: false,
    };

    Ok(Ir::new(metadata, vec![page]))
}
