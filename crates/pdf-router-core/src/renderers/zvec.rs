//! Native zvec target renderer (ADR-0010).

use std::collections::VecDeque;
use std::path::Path;

use regex::Regex;
use serde::{Deserialize, Serialize};

use crate::ir::{Block, Ir};
use crate::renderers::chunks::{
    column_keys, doc_identity, facts_summary, format_bbox_helper, kv_lines, looks_like_caption_row,
    parse_num, reading_order, rows_to_pipe, split_blocks, split_long, strip_tables, HeadingStack,
};

const MIN_TEXT_CHARS: usize = 64;
const DRAWING_KEYWORDS: &[&str] = &[
    "чертеж",
    "чертёж",
    "схема",
    "план",
    "разрез",
    "узел",
    "фасад",
    "генплан",
    "профиль",
    "аксонометрия",
    "трасса",
];

/// Format bounding box as JSON array string: `"[x0, y0, x1, y1]"` or `"[]"`.
pub fn format_bbox(bbox: Option<crate::ir::BBox>) -> String {
    format_bbox_helper(bbox)
}

/// Compute structured numeric statistics per column as JSON string.
pub fn table_facts_json(keys: &[String], rows: &[Vec<String>], n_cols: usize) -> String {
    if rows.is_empty() {
        return serde_json::json!({
            "n_cols": n_cols,
            "n_rows": 0,
            "cols": {}
        })
        .to_string();
    }

    let mut stats = serde_json::Map::new();
    for ci in 0..n_cols {
        let mut parsed = Vec::new();
        for row in rows {
            let c = if ci < row.len() { row[ci].trim() } else { "" };
            if !c.is_empty() {
                if let Some(v) = parse_num(c) {
                    parsed.push(v);
                }
            }
        }
        if !parsed.is_empty() && parsed.len() * 2 >= rows.len() {
            let label = if ci < keys.len() { &keys[ci] } else { "" };
            let label_str = if label.is_empty() {
                format!("Колонка {}", ci + 1)
            } else {
                label.to_string()
            };
            let min_v = parsed.iter().copied().fold(f64::INFINITY, f64::min);
            let max_v = parsed.iter().copied().fold(f64::NEG_INFINITY, f64::max);
            let sum_v: f64 = parsed.iter().sum();
            let round_sum = (sum_v * 10000.0).round() / 10000.0;

            stats.insert(
                label_str,
                serde_json::json!({
                    "min": min_v,
                    "max": max_v,
                    "sum": round_sum,
                }),
            );
        }
    }

    serde_json::json!({
        "n_cols": n_cols,
        "n_rows": rows.len(),
        "cols": stats,
    })
    .to_string()
}

/// Fields stored in zvec collection schema.
#[derive(Debug, Clone, Serialize, Deserialize, PartialEq)]
pub struct ZvecFields {
    pub project_uid: String,
    pub doc_uid: String,
    pub doc_name: String,
    pub chunk_type: String,
    pub chunk_index: usize,
    pub page_num: usize,
    pub bbox: String,
    pub section_path: String,
    pub section_title: String,
    pub content: String,
    pub table_facts: String,
    pub file_path: String,
    pub image_url: String,
    pub caption: String,
    pub image_meta: String,
}

/// Metadata attached to each zvec Doc record.
#[derive(Debug, Clone, Serialize, Deserialize, PartialEq, Eq)]
pub struct ZvecMetadata {
    pub source_format: String,
    pub src_tool: String,
    pub error: Option<String>,
    #[serde(default)]
    pub images: Vec<String>,
}

/// Strongly-typed zvec Doc record matching collection schema.
#[derive(Debug, Clone, Serialize, Deserialize, PartialEq)]
pub struct ZvecDocRecord {
    pub id: String,
    pub embed_text: String,
    pub fields: ZvecFields,
    pub metadata: ZvecMetadata,
}

/// Configuration options for zvec doc rendering.
#[derive(Debug, Clone, Serialize, Deserialize, PartialEq, Eq)]
pub struct ZvecConfig {
    pub max_tokens: usize,
    pub overlap_tokens: usize,
    pub summary: String,
    pub embed_context: bool,
    pub doc_uid: Option<String>,
    pub project_uid: Option<String>,
}

impl Default for ZvecConfig {
    fn default() -> Self {
        Self {
            max_tokens: 512,
            overlap_tokens: 64,
            summary: "facts".to_string(),
            embed_context: true,
            doc_uid: None,
            project_uid: None,
        }
    }
}

/// Streaming iterator yielding zvec Doc records one by one with O(1) memory overhead.
pub struct ZvecDocsIterator<'a> {
    ir: &'a Ir,
    config: ZvecConfig,
    page_idx: usize,
    chunk_index: usize,
    stack: HeadingStack,
    buffer: VecDeque<ZvecDocRecord>,
    doc_uid: String,
    project_uid: String,
    file_path: String,
    doc_name: String,
}

impl<'a> ZvecDocsIterator<'a> {
    pub fn new(ir: &'a Ir, config: ZvecConfig) -> Self {
        let (doc_id, doc_title) = doc_identity(ir);
        let doc_uid = config
            .doc_uid
            .clone()
            .or(doc_id)
            .unwrap_or_else(|| "doc".to_string())
            .trim()
            .to_string();
        let project_uid = config
            .project_uid
            .clone()
            .unwrap_or_default()
            .trim()
            .to_string();

        let file_path = ir.metadata.source_path.clone().unwrap_or_default();
        let doc_name = ir
            .metadata
            .title
            .clone()
            .or_else(|| {
                if !file_path.is_empty() {
                    Path::new(&file_path)
                        .file_name()
                        .and_then(|s| s.to_str())
                        .map(ToString::to_string)
                } else {
                    None
                }
            })
            .or(doc_title)
            .unwrap_or_default();

        Self {
            ir,
            config,
            page_idx: 0,
            chunk_index: 0,
            stack: HeadingStack::new(),
            buffer: VecDeque::new(),
            doc_uid,
            project_uid,
            file_path,
            doc_name,
        }
    }

    fn refill_buffer(&mut self) -> bool {
        static HEADING_RE: std::sync::OnceLock<Regex> = std::sync::OnceLock::new();
        let heading_re = HEADING_RE.get_or_init(|| Regex::new(r"(?m)^(#{2,4})\s+(.+)$").unwrap());

        while self.buffer.is_empty() && self.page_idx < self.ir.pages.len() {
            let page = &self.ir.pages[self.page_idx];
            self.page_idx += 1;
            let page_num = page.page + 1;

            let ordered = reading_order(page);
            let mut md_headings = Vec::new();
            for cap in heading_re.captures_iter(&page.markdown) {
                md_headings.push(cap[2].trim().to_string());
            }
            let fallback_section = md_headings.first().cloned();

            let mut table_jobs = Vec::new();
            let mut figure_jobs = Vec::new();
            let mut table_no = 0;
            let mut fig_no = 0;

            for block in ordered {
                match block {
                    Block::Heading(h) => {
                        self.stack.push(h.level, h.text.clone());
                    }
                    Block::Table(t) => {
                        table_no += 1;
                        let snap_title = self.stack.deepest().map(ToString::to_string);
                        let snap_path = self.stack.path();
                        let sec_path = if !snap_path.is_empty() {
                            snap_path
                        } else {
                            fallback_section.clone().unwrap_or_default()
                        };
                        let sec_title = snap_title.or_else(|| fallback_section.clone());
                        table_jobs.push((t, sec_title, sec_path, table_no));
                    }
                    Block::Figure(fig) => {
                        fig_no += 1;
                        let snap_title = self.stack.deepest().map(ToString::to_string);
                        let snap_path = self.stack.path();
                        let sec_path = if !snap_path.is_empty() {
                            snap_path
                        } else {
                            fallback_section.clone().unwrap_or_default()
                        };
                        let sec_title = snap_title.or_else(|| fallback_section.clone());
                        figure_jobs.push((fig, sec_title, sec_path, fig_no));
                    }
                    _ => {}
                }
            }

            // --- 1. Process Table Blocks ---
            for (block, heading_opt, section_path, no) in table_jobs {
                let rows = &block.rows;
                let n_cols = rows.iter().map(|r| r.len()).max().unwrap_or(0);
                if rows.is_empty() || n_cols == 0 {
                    continue;
                }

                let (caption, band, data): (Option<String>, &[Vec<String>], &[Vec<String>]) =
                    if rows.len() >= 2 && looks_like_caption_row(&rows[0]) {
                        let cap = rows[0]
                            .iter()
                            .map(|c| c.trim())
                            .filter(|c| !c.is_empty())
                            .collect::<Vec<_>>()
                            .join(" ");
                        (Some(cap), &rows[1..2], &rows[2..])
                    } else {
                        let band_end = block.header_rows.min(rows.len());
                        (None, &rows[..band_end], &rows[band_end..])
                    };

                let keys = column_keys(band, n_cols);

                let eff_sec = if !section_path.is_empty() {
                    Some(section_path.as_str())
                } else {
                    heading_opt.as_deref()
                };

                let crumbs = [Some(self.doc_name.as_str()), eff_sec]
                    .into_iter()
                    .flatten()
                    .filter(|p| !p.is_empty())
                    .collect::<Vec<_>>()
                    .join(" › ");

                let mut head = Vec::new();
                if self.config.embed_context && !crumbs.is_empty() {
                    head.push(crumbs);
                }
                if let Some(cap) = &caption {
                    head.push(cap.clone());
                }

                let embed_of = |part_rows: &[Vec<String>]| -> String {
                    let mut pieces = head.clone();
                    pieces.extend(kv_lines(&keys, part_rows));
                    if self.config.summary == "facts" {
                        pieces.push(facts_summary(&keys, part_rows, n_cols));
                    }
                    pieces.join("\n")
                };

                let md_of = |part_rows: &[Vec<String>]| -> String {
                    let title_line = heading_opt.as_deref().or(caption.as_deref());
                    let mut out = Vec::new();
                    if let Some(tl) = title_line {
                        out.push(format!("# {}", tl));
                    }
                    if let Some(cap) = &caption {
                        if title_line != Some(cap.as_str()) {
                            out.push(cap.clone());
                        }
                        out.push(rows_to_pipe(band, part_rows));
                    } else {
                        out.push(block.markdown.clone());
                    }
                    if self.config.summary == "facts" {
                        out.push(facts_summary(&keys, part_rows, n_cols));
                    }
                    out.into_iter()
                        .filter(|ln| !ln.is_empty())
                        .collect::<Vec<_>>()
                        .join("\n")
                };

                let whole_embed = embed_of(data);
                let whole_tokens = whole_embed.chars().count() / 4;

                let groups: Vec<Vec<Vec<String>>> = if data.is_empty()
                    || whole_tokens <= self.config.max_tokens
                {
                    vec![data.to_vec()]
                } else {
                    let mut groups = Vec::new();
                    let mut current: Vec<Vec<String>> = Vec::new();
                    for row in data {
                        let mut test_group = current.clone();
                        test_group.push(row.clone());
                        if !current.is_empty()
                            && (embed_of(&test_group).chars().count() / 4) > self.config.max_tokens
                        {
                            groups.push(current);
                            current = vec![row.clone()];
                        } else {
                            current.push(row.clone());
                        }
                    }
                    if !current.is_empty() {
                        groups.push(current);
                    }
                    groups
                };

                let single = groups.len() == 1;
                let bbox_str = format_bbox(block.bbox);

                for (k_idx, part_rows) in groups.iter().enumerate() {
                    let k = k_idx + 1;
                    let part_type = if single { "table" } else { "table_part" };
                    let part_id = if single {
                        format!("{}_p{:02}_t{:02}", self.doc_uid, page_num, no)
                    } else {
                        format!("{}_p{:02}_t{:02}_pt{:02}", self.doc_uid, page_num, no, k)
                    };
                    let table_facts = if self.config.summary == "facts" {
                        table_facts_json(&keys, part_rows, n_cols)
                    } else {
                        String::new()
                    };

                    self.buffer.push_back(ZvecDocRecord {
                        id: part_id,
                        embed_text: embed_of(part_rows),
                        fields: ZvecFields {
                            project_uid: self.project_uid.clone(),
                            doc_uid: self.doc_uid.clone(),
                            doc_name: self.doc_name.clone(),
                            chunk_type: part_type.to_string(),
                            chunk_index: self.chunk_index,
                            page_num,
                            bbox: bbox_str.clone(),
                            section_path: section_path.clone(),
                            section_title: heading_opt.clone().unwrap_or_default(),
                            content: md_of(part_rows),
                            table_facts,
                            file_path: self.file_path.clone(),
                            image_url: String::new(),
                            caption: caption.clone().unwrap_or_default(),
                            image_meta: String::new(),
                        },
                        metadata: ZvecMetadata {
                            source_format: self.ir.metadata.source_format.clone(),
                            src_tool: page.src_tool.as_str().to_string(),
                            error: page.error.clone(),
                            images: page.images.clone(),
                        },
                    });
                    self.chunk_index += 1;
                }
            }

            // --- 2. Process Figure / Drawing Blocks ---
            for (fig_block, heading_opt, section_path, no) in figure_jobs {
                let cap = fig_block
                    .caption
                    .clone()
                    .or_else(|| heading_opt.clone())
                    .unwrap_or_default();
                let is_draw_cap = {
                    let lower = cap.to_lowercase();
                    DRAWING_KEYWORDS.iter().any(|kw| lower.contains(kw))
                };
                let is_draw_img = fig_block
                    .image_index
                    .and_then(|idx| page.images.get(idx))
                    .map(|img_p| img_p.to_lowercase().contains("vector"))
                    .unwrap_or(false);
                let is_draw = is_draw_cap || is_draw_img;

                let fig_type = if is_draw { "drawing" } else { "image" };
                let prefix = if is_draw { "draw" } else { "img" };
                let fig_id = format!("{}_p{:02}_{}{:02}", self.doc_uid, page_num, prefix, no);

                let img_path = fig_block
                    .image_index
                    .and_then(|idx| page.images.get(idx).cloned())
                    .or_else(|| page.images.first().cloned())
                    .unwrap_or_default();

                let eff_sec = if !section_path.is_empty() {
                    Some(section_path.as_str())
                } else {
                    heading_opt.as_deref()
                };

                let crumbs = [Some(self.doc_name.as_str()), eff_sec]
                    .into_iter()
                    .flatten()
                    .filter(|p| !p.is_empty())
                    .collect::<Vec<_>>()
                    .join(" › ");

                let tag = if is_draw {
                    "Чертёж / Схема"
                } else {
                    "Иллюстрация"
                };

                let mut embed_lines = Vec::new();
                if self.config.embed_context && !crumbs.is_empty() {
                    embed_lines.push(crumbs);
                }
                if !cap.is_empty() {
                    embed_lines.push(format!("[{}]: {}", tag, cap));
                } else {
                    embed_lines.push(format!("[{}] (Стр. {})", tag, page_num));
                }

                let mut content_lines = Vec::new();
                if !img_path.is_empty() {
                    let cap_label = if !cap.is_empty() { &cap } else { tag };
                    content_lines.push(format!("![{}]({})", cap_label, img_path));
                }
                if !cap.is_empty() {
                    content_lines.push(format!("**{}**", cap));
                }
                let content = if !content_lines.is_empty() {
                    content_lines.join("\n\n")
                } else {
                    format!("[{}]", tag)
                };

                let img_meta = serde_json::json!({
                    "kind": fig_type,
                    "page": page_num,
                    "has_bbox": fig_block.bbox.is_some(),
                })
                .to_string();

                self.buffer.push_back(ZvecDocRecord {
                    id: fig_id,
                    embed_text: embed_lines.join("\n"),
                    fields: ZvecFields {
                        project_uid: self.project_uid.clone(),
                        doc_uid: self.doc_uid.clone(),
                        doc_name: self.doc_name.clone(),
                        chunk_type: fig_type.to_string(),
                        chunk_index: self.chunk_index,
                        page_num,
                        bbox: format_bbox(fig_block.bbox),
                        section_path,
                        section_title: heading_opt.unwrap_or_default(),
                        content,
                        table_facts: String::new(),
                        file_path: self.file_path.clone(),
                        image_url: img_path,
                        caption: cap,
                        image_meta: img_meta,
                    },
                    metadata: ZvecMetadata {
                        source_format: self.ir.metadata.source_format.clone(),
                        src_tool: page.src_tool.as_str().to_string(),
                        error: page.error.clone(),
                        images: page.images.clone(),
                    },
                });
                self.chunk_index += 1;
            }

            // --- 3. Fallback for Images when no FigureBlocks were found ---
            let has_fig_blocks = page.blocks.iter().any(|b| matches!(b, Block::Figure(_)));
            if !has_fig_blocks && !page.images.is_empty() {
                for (img_idx, img_p) in page.images.iter().enumerate() {
                    let idx_1 = img_idx + 1;
                    let is_draw = img_p.to_lowercase().contains("vector");
                    let fig_type = if is_draw { "drawing" } else { "image" };
                    let tag = if is_draw {
                        "Чертёж / Схема"
                    } else {
                        "Иллюстрация"
                    };

                    let cap = self
                        .stack
                        .deepest()
                        .map(ToString::to_string)
                        .or_else(|| fallback_section.clone())
                        .unwrap_or_default();

                    let sec_path = self.stack.path();
                    let eff_sec = if !sec_path.is_empty() {
                        sec_path.as_str()
                    } else {
                        cap.as_str()
                    };

                    let crumbs = [Some(self.doc_name.as_str()), Some(eff_sec)]
                        .into_iter()
                        .flatten()
                        .filter(|p| !p.is_empty())
                        .collect::<Vec<_>>()
                        .join(" › ");

                    let mut embed_lines = Vec::new();
                    if self.config.embed_context && !crumbs.is_empty() {
                        embed_lines.push(crumbs);
                    }
                    if !cap.is_empty() {
                        embed_lines.push(format!("[{}]: {} (Стр. {})", tag, cap, page_num));
                    } else {
                        embed_lines.push(format!("[{}] (Стр. {})", tag, page_num));
                    }

                    let prefix = if is_draw { "draw" } else { "img" };
                    let fig_id =
                        format!("{}_p{:02}_{}{:02}", self.doc_uid, page_num, prefix, idx_1);
                    let img_meta = serde_json::json!({
                        "kind": fig_type,
                        "page": page_num,
                        "has_bbox": false,
                    })
                    .to_string();

                    let content = if !cap.is_empty() {
                        format!("![{}]({})\n\n**{}**", cap, img_p, cap)
                    } else {
                        format!("![{}]({})", tag, img_p)
                    };

                    let eff_sec_path = if !sec_path.is_empty() {
                        sec_path
                    } else {
                        fallback_section.clone().unwrap_or_default()
                    };

                    self.buffer.push_back(ZvecDocRecord {
                        id: fig_id,
                        embed_text: embed_lines.join("\n"),
                        fields: ZvecFields {
                            project_uid: self.project_uid.clone(),
                            doc_uid: self.doc_uid.clone(),
                            doc_name: self.doc_name.clone(),
                            chunk_type: fig_type.to_string(),
                            chunk_index: self.chunk_index,
                            page_num,
                            bbox: "[]".to_string(),
                            section_path: eff_sec_path,
                            section_title: cap.clone(),
                            content,
                            table_facts: String::new(),
                            file_path: self.file_path.clone(),
                            image_url: img_p.clone(),
                            caption: cap,
                            image_meta: img_meta,
                        },
                        metadata: ZvecMetadata {
                            source_format: self.ir.metadata.source_format.clone(),
                            src_tool: page.src_tool.as_str().to_string(),
                            error: page.error.clone(),
                            images: page.images.clone(),
                        },
                    });
                    self.chunk_index += 1;
                }
            }

            // --- 4. Process Text Half (Markdown minus subtracted tables) ---
            let remaining = strip_tables(&page.markdown, page);
            let max_chars_full = self.config.max_tokens * 4;
            let overlap_chars = self.config.overlap_tokens * 4;

            let mut seq = 0;
            for (section, text) in split_blocks(&remaining) {
                let stack_p = self.stack.path();
                let effective_section_path = if !stack_p.is_empty() {
                    stack_p
                } else if let Some(s) = &section {
                    s.clone()
                } else if let Some(fb) = &fallback_section {
                    fb.clone()
                } else {
                    String::new()
                };

                let effective_section_title = if let Some(s) = &section {
                    s.clone()
                } else if let Some(d) = self.stack.deepest() {
                    d.to_string()
                } else if let Some(fb) = &fallback_section {
                    fb.clone()
                } else {
                    String::new()
                };

                let preamble = if self.config.embed_context {
                    let sec_choice = if !effective_section_path.is_empty() {
                        effective_section_path.as_str()
                    } else {
                        effective_section_title.as_str()
                    };
                    [Some(self.doc_name.as_str()), Some(sec_choice)]
                        .into_iter()
                        .flatten()
                        .filter(|p| !p.is_empty())
                        .collect::<Vec<_>>()
                        .join(" › ")
                } else {
                    String::new()
                };

                let mut max_chars = max_chars_full;
                if !preamble.is_empty() {
                    let preamble_chars = preamble.chars().count();
                    max_chars = if max_chars_full > preamble_chars + 1 {
                        (max_chars_full - preamble_chars - 1).max(MIN_TEXT_CHARS)
                    } else {
                        MIN_TEXT_CHARS
                    };
                }

                for piece in split_long(&text, max_chars, overlap_chars) {
                    seq += 1;
                    let embed_text = if !preamble.is_empty() {
                        format!("{}\n{}", preamble, piece)
                    } else {
                        piece.clone()
                    };
                    let chunk_id = format!("{}_p{:02}_text{:02}", self.doc_uid, page_num, seq);

                    self.buffer.push_back(ZvecDocRecord {
                        id: chunk_id,
                        embed_text,
                        fields: ZvecFields {
                            project_uid: self.project_uid.clone(),
                            doc_uid: self.doc_uid.clone(),
                            doc_name: self.doc_name.clone(),
                            chunk_type: "text".to_string(),
                            chunk_index: self.chunk_index,
                            page_num,
                            bbox: "[]".to_string(),
                            section_path: effective_section_path.clone(),
                            section_title: effective_section_title.clone(),
                            content: piece,
                            table_facts: String::new(),
                            file_path: self.file_path.clone(),
                            image_url: String::new(),
                            caption: String::new(),
                            image_meta: String::new(),
                        },
                        metadata: ZvecMetadata {
                            source_format: self.ir.metadata.source_format.clone(),
                            src_tool: page.src_tool.as_str().to_string(),
                            error: page.error.clone(),
                            images: page.images.clone(),
                        },
                    });
                    self.chunk_index += 1;
                }
            }
        }

        !self.buffer.is_empty()
    }
}

impl<'a> Iterator for ZvecDocsIterator<'a> {
    type Item = ZvecDocRecord;

    fn next(&mut self) -> Option<Self::Item> {
        if self.buffer.is_empty() && !self.refill_buffer() {
            return None;
        }
        self.buffer.pop_front()
    }
}

/// Create a streaming iterator over zvec Doc records for direct vector store ingestion.
pub fn iter_zvec_docs<'a>(ir: &'a Ir, config: &ZvecConfig) -> ZvecDocsIterator<'a> {
    ZvecDocsIterator::new(ir, config.clone())
}

/// Render an IR to JSONL string containing one zvec Doc record per line.
pub fn render_zvec(ir: &Ir, config: &ZvecConfig) -> String {
    let mut out = String::new();
    for doc in iter_zvec_docs(ir, config) {
        if let Ok(line) = serde_json::to_string(&doc) {
            out.push_str(&line);
            out.push('\n');
        }
    }
    out
}
