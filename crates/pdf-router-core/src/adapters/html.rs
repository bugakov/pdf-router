//! Minimal HTML ingest — tag-strip to clean prose paragraphs and headings (IR 0.6).
//!
//! Conforms to ADR-0008:
//! - Tags like `<script>`, `<style>`, `<noscript>`, `<template>`, `<iframe>`, `<svg>`
//!   are completely omitted along with their payloads.
//! - Document `<title>` is captured into `Metadata.title`.
//! - Block-level tags split paragraphs with collapsed whitespace runs.
//! - `<h1>`–`<h6>` become `HeadingBlock`s with `source = "html_tag"`.
//! - Single-page IR 0.6 with `page = 0`, `source_format = "html"`, `SrcTool::HtmlAdapter`.

use std::time::Instant;

use crate::error::RouterError;
use crate::ir::{Block, HeadingBlock, Ir, Metadata, PageResult, SrcTool};

/// Tags whose contents are not document text.
const SKIP_TAGS: &[&str] = &["script", "style", "noscript", "template", "iframe", "svg"];

/// Block-level tags that end the current paragraph.
const BLOCK_TAGS: &[&str] = &[
    "p",
    "div",
    "li",
    "ul",
    "ol",
    "dl",
    "dt",
    "dd",
    "tr",
    "table",
    "section",
    "article",
    "aside",
    "header",
    "footer",
    "main",
    "nav",
    "blockquote",
    "pre",
    "hr",
    "address",
    "figure",
    "figcaption",
    "form",
    "fieldset",
    "br",
];

/// Collapse every whitespace run (including U+00A0 from `&nbsp;`).
pub fn collapse_whitespace(text: &str) -> String {
    text.split_whitespace().collect::<Vec<_>>().join(" ")
}

/// Decode standard and numeric HTML entities.
pub fn decode_html_entities(s: &str) -> String {
    let mut out = String::with_capacity(s.len());
    let mut chars = s.chars().peekable();
    while let Some(c) = chars.next() {
        if c == '&' {
            let mut entity = String::new();
            let mut found_semi = false;
            while let Some(&next_c) = chars.peek() {
                if next_c == ';' {
                    chars.next();
                    found_semi = true;
                    break;
                }
                if next_c.is_alphanumeric() || next_c == '#' {
                    entity.push(chars.next().unwrap());
                    if entity.len() > 10 {
                        break;
                    }
                } else {
                    break;
                }
            }
            if found_semi {
                if let Some(stripped) = entity
                    .strip_prefix("#x")
                    .or_else(|| entity.strip_prefix("#X"))
                {
                    if let Ok(code) = u32::from_str_radix(stripped, 16) {
                        if let Some(ch) = char::from_u32(code) {
                            out.push(ch);
                            continue;
                        }
                    }
                } else if let Some(stripped) = entity.strip_prefix('#') {
                    if let Ok(code) = stripped.parse::<u32>() {
                        if let Some(ch) = char::from_u32(code) {
                            out.push(ch);
                            continue;
                        }
                    }
                } else {
                    match entity.as_str() {
                        "amp" => {
                            out.push('&');
                            continue;
                        }
                        "lt" => {
                            out.push('<');
                            continue;
                        }
                        "gt" => {
                            out.push('>');
                            continue;
                        }
                        "quot" => {
                            out.push('"');
                            continue;
                        }
                        "apos" => {
                            out.push('\'');
                            continue;
                        }
                        "nbsp" => {
                            out.push('\u{00A0}');
                            continue;
                        }
                        "copy" => {
                            out.push('©');
                            continue;
                        }
                        "reg" => {
                            out.push('®');
                            continue;
                        }
                        "trade" => {
                            out.push('™');
                            continue;
                        }
                        "mdash" => {
                            out.push('—');
                            continue;
                        }
                        "ndash" => {
                            out.push('–');
                            continue;
                        }
                        "laquo" => {
                            out.push('«');
                            continue;
                        }
                        "raquo" => {
                            out.push('»');
                            continue;
                        }
                        _ => {}
                    }
                }
                out.push('&');
                out.push_str(&entity);
                out.push(';');
            } else {
                out.push('&');
                out.push_str(&entity);
            }
        } else {
            out.push(c);
        }
    }
    out
}

struct ProseExtractor {
    md_parts: Vec<String>,
    blocks: Vec<Block>,
    title: Option<String>,
    title_buf: String,
    buf: String,
    heading_level: Option<usize>,
    in_head: bool,
    in_title: bool,
    skip_tag: Option<String>,
}

impl ProseExtractor {
    fn new() -> Self {
        Self {
            md_parts: Vec::new(),
            blocks: Vec::new(),
            title: None,
            title_buf: String::new(),
            buf: String::new(),
            heading_level: None,
            in_head: false,
            in_title: false,
            skip_tag: None,
        }
    }

    fn flush_paragraph(&mut self) {
        if self.heading_level.is_some() {
            self.flush_heading();
            return;
        }
        let text = collapse_whitespace(&self.buf);
        self.buf.clear();
        if !text.is_empty() {
            self.md_parts.push(text);
        }
    }

    fn flush_heading(&mut self) {
        let text = collapse_whitespace(&self.buf);
        self.buf.clear();
        if let Some(level) = self.heading_level.take() {
            if !text.is_empty() {
                self.md_parts.push(format!("{} {text}", "#".repeat(level)));
                self.blocks.push(Block::Heading(HeadingBlock {
                    level,
                    text,
                    bbox: None,
                    source: "html_tag".to_string(),
                }));
            }
        }
    }

    fn parse(&mut self, html: &str) {
        let bytes = html.as_bytes();
        let len = bytes.len();
        let mut i = 0;

        while i < len {
            if bytes[i] == b'<' {
                // Check comment: <!-- ... -->
                if html[i..].starts_with("<!--") {
                    if let Some(pos) = html[i + 4..].find("-->") {
                        i += 4 + pos + 3;
                    } else {
                        break;
                    }
                    continue;
                }

                // Check <! (e.g. <!DOCTYPE ...>)
                if html[i..].starts_with("<!") {
                    if let Some(pos) = html[i..].find('>') {
                        i += pos + 1;
                    } else {
                        break;
                    }
                    continue;
                }

                // Check closing tag </tag>
                if html[i..].starts_with("</") {
                    let end_pos = match html[i..].find('>') {
                        Some(p) => i + p,
                        None => break,
                    };
                    let raw_name = &html[i + 2..end_pos];
                    let tag_name = raw_name
                        .split(|c: char| c.is_whitespace() || c == '>')
                        .next()
                        .unwrap_or("")
                        .to_ascii_lowercase();

                    if let Some(ref cur_skip) = self.skip_tag {
                        if cur_skip == &tag_name {
                            self.skip_tag = None;
                        }
                        i = end_pos + 1;
                        continue;
                    }

                    if self.skip_tag.is_none() {
                        if tag_name == "head" {
                            self.in_head = false;
                        } else if tag_name == "title" {
                            if self.in_title {
                                let t = collapse_whitespace(&self.title_buf);
                                self.title = if t.is_empty() { None } else { Some(t) };
                            }
                            self.in_title = false;
                            self.title_buf.clear();
                        } else if !self.in_head || self.in_title {
                            if tag_name.starts_with('h')
                                && tag_name.len() == 2
                                && tag_name[1..].chars().all(|c| ('1'..='6').contains(&c))
                            {
                                self.flush_heading();
                            } else if BLOCK_TAGS.contains(&tag_name.as_str()) {
                                self.flush_paragraph();
                            }
                        }
                    }

                    i = end_pos + 1;
                    continue;
                }

                // Opening tag <tag ...>
                let end_pos = match html[i..].find('>') {
                    Some(p) => i + p,
                    None => break,
                };
                let tag_slice = &html[i + 1..end_pos];
                let tag_name = tag_slice
                    .split(|c: char| c.is_whitespace() || c == '/' || c == '>')
                    .next()
                    .unwrap_or("")
                    .to_ascii_lowercase();

                if self.skip_tag.is_some() {
                    i = end_pos + 1;
                    continue;
                }

                if SKIP_TAGS.contains(&tag_name.as_str()) {
                    self.skip_tag = Some(tag_name);
                } else if tag_name == "head" {
                    self.in_head = true;
                } else if tag_name == "title" {
                    self.flush_paragraph();
                    self.in_title = true;
                } else if !self.in_head || self.in_title {
                    if tag_name.starts_with('h')
                        && tag_name.len() == 2
                        && tag_name[1..].chars().all(|c| ('1'..='6').contains(&c))
                    {
                        self.flush_paragraph();
                        let level = tag_name[1..].parse::<usize>().unwrap_or(1);
                        self.heading_level = Some(level);
                    } else if tag_name == "br" {
                        self.flush_paragraph();
                    } else if BLOCK_TAGS.contains(&tag_name.as_str()) {
                        self.flush_paragraph();
                    }
                }

                i = end_pos + 1;
                continue;
            }

            // Normal text character run until '<'
            let next_tag = match html[i..].find('<') {
                Some(p) => i + p,
                None => len,
            };
            let text_chunk = &html[i..next_tag];
            i = next_tag;

            if self.skip_tag.is_some() || (self.in_head && !self.in_title) {
                continue;
            }

            let decoded = decode_html_entities(text_chunk);
            if self.in_title {
                self.title_buf.push_str(&decoded);
            } else {
                self.buf.push_str(&decoded);
            }
        }

        self.flush_paragraph();
    }
}

/// Process raw HTML bytes into IR 0.6.
pub fn process_html(bytes: &[u8], source_path: Option<&str>) -> Result<Ir, RouterError> {
    let t0 = Instant::now();

    // Strip UTF-8 BOM if present
    let raw = if bytes.starts_with(b"\xef\xbb\xbf") {
        &bytes[3..]
    } else {
        bytes
    };

    let text = String::from_utf8_lossy(raw);
    let has_encoding_issues = text.contains('\u{FFFD}');

    let mut extractor = ProseExtractor::new();
    extractor.parse(&text);

    let markdown = extractor
        .md_parts
        .into_iter()
        .filter(|p| !p.is_empty())
        .collect::<Vec<_>>()
        .join("\n\n")
        .trim()
        .to_string();

    let elapsed_ms = t0.elapsed().as_secs_f64() * 1000.0;

    let metadata = Metadata {
        source_format: "html".to_string(),
        source_path: source_path.map(|s| s.to_string()),
        pdf_type: "text_based".to_string(),
        page_count: 1,
        confidence: 1.0,
        processing_time_ms: (elapsed_ms * 10.0).round() / 10.0,
        title: extractor.title,
        is_complex_layout: false,
        has_encoding_issues,
        pages_with_tables: vec![],
        ..Default::default()
    };

    let page = PageResult {
        page: 0,
        markdown,
        src_tool: SrcTool::HtmlAdapter,
        blocks: extractor.blocks,
        images: vec![],
        error: None,
        needs_ocr: false,
    };

    Ok(Ir::new(metadata, vec![page]))
}
