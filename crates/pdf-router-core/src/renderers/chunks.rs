//! RAG chunks hybrid envelope v2 renderer (ADR-0006).

use std::collections::{BTreeMap, HashSet, VecDeque};
use std::path::{Path, PathBuf};

use regex::Regex;
use serde::{Deserialize, Serialize};

use crate::ir::{Block, Ir, PageResult, TableBlock};

const MIN_TEXT_CHARS: usize = 64;
const CAPTION_PREFIXES: &[&str] = &["таблица", "табл.", "табл", "table"];

/// Compute 12-char hex SHA-256 ID of document path.
pub fn doc_id_from_path(path: &str) -> String {
    use sha2::{Digest, Sha256};
    let p = Path::new(path);
    let resolved = if let Ok(canon) = std::fs::canonicalize(p) {
        canon.to_string_lossy().to_string()
    } else {
        let abs = if p.is_absolute() {
            p.to_path_buf()
        } else if let Ok(cwd) = std::env::current_dir() {
            cwd.join(p)
        } else {
            p.to_path_buf()
        };
        let mut components = Vec::new();
        for comp in abs.components() {
            match comp {
                std::path::Component::CurDir => {}
                std::path::Component::ParentDir => {
                    components.pop();
                }
                c => components.push(c),
            }
        }
        let mut normalized = PathBuf::new();
        for c in components {
            normalized.push(c.as_os_str());
        }
        normalized.to_string_lossy().to_string()
    };

    let mut hasher = Sha256::new();
    hasher.update(resolved.as_bytes());
    let hash = hasher.finalize();
    let hex = format!("{:x}", hash);
    hex[..12].to_string()
}

/// Extract (doc_id, doc_title) from document metadata.
pub fn doc_identity(ir: &Ir) -> (Option<String>, Option<String>) {
    let src = ir.metadata.source_path.as_deref();
    let doc_id = src.map(doc_id_from_path);
    let doc_title = ir.metadata.title.clone().or_else(|| {
        src.and_then(|p| {
            Path::new(p)
                .file_name()
                .and_then(|s| s.to_str())
                .map(ToString::to_string)
        })
    });
    (doc_id, doc_title)
}

/// Metadata specific to table chunks.
#[derive(Debug, Clone, Serialize, Deserialize, PartialEq, Eq)]
pub struct TableChunkMeta {
    pub table_id: String,
    pub part: usize,
    pub parts: usize,
    pub n_rows: usize,
    pub n_cols: usize,
    pub header: Vec<String>,
    pub source: String,
}

/// Metadata envelope for each chunk.
#[derive(Debug, Clone, Serialize, Deserialize, PartialEq, Eq)]
pub struct ChunkMetadata {
    pub doc_id: Option<String>,
    pub doc_title: Option<String>,
    pub source_format: String,
    pub page: usize,
    pub section: Option<String>,
    pub src_tool: String,
    pub error: Option<String>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub table: Option<TableChunkMeta>,
}

/// Top-level hybrid chunk envelope matching version 2 specification.
#[derive(Debug, Clone, Serialize, Deserialize, PartialEq, Eq)]
pub struct ChunkEnvelope {
    pub chunk_id: String,
    pub chunk_version: u32,
    #[serde(rename = "type")]
    pub chunk_type: String,
    pub embed: String,
    pub markdown: String,
    pub metadata: ChunkMetadata,
    #[serde(default)]
    pub images: Vec<String>,
}

/// Configuration options for chunk generation.
#[derive(Debug, Clone, Serialize, Deserialize, PartialEq, Eq)]
pub struct ChunksConfig {
    pub max_tokens: usize,
    pub overlap_tokens: usize,
    pub summary: String,
    pub embed_context: bool,
}

impl Default for ChunksConfig {
    fn default() -> Self {
        Self {
            max_tokens: 512,
            overlap_tokens: 64,
            summary: "facts".to_string(),
            embed_context: true,
        }
    }
}

/// Tracks open headings across page boundaries.
#[derive(Debug, Clone, Default)]
pub struct HeadingStack {
    levels: Vec<(usize, String)>,
}

impl HeadingStack {
    pub fn new() -> Self {
        Self::default()
    }

    pub fn push(&mut self, level: usize, text: String) {
        while let Some(&(last_lvl, _)) = self.levels.last() {
            if last_lvl >= level {
                self.levels.pop();
            } else {
                break;
            }
        }
        self.levels.push((level, text));
    }

    pub fn deepest(&self) -> Option<&str> {
        self.levels.last().map(|(_, t)| t.as_str())
    }

    pub fn path(&self) -> String {
        self.levels
            .iter()
            .map(|(_, t)| t.as_str())
            .collect::<Vec<_>>()
            .join(" > ")
    }
}

pub(crate) fn reading_order(page: &PageResult) -> Vec<&Block> {
    let mut blocks: Vec<&Block> = page.blocks.iter().collect();
    let is_ocr = blocks.iter().any(|b| match b {
        Block::Heading(h) => h.source.starts_with("ocr_"),
        Block::Table(t) => t.source.starts_with("ocr_"),
        Block::Formula(f) => f.source.starts_with("ocr_"),
        Block::Figure(_) => false,
    });
    if is_ocr {
        blocks.sort_by(|a, b| {
            let bbox_a = match a {
                Block::Heading(h) => h.bbox,
                Block::Table(t) => t.bbox,
                Block::Formula(f) => f.bbox,
                Block::Figure(fig) => fig.bbox,
            };
            let bbox_b = match b {
                Block::Heading(h) => h.bbox,
                Block::Table(t) => t.bbox,
                Block::Formula(f) => f.bbox,
                Block::Figure(fig) => fig.bbox,
            };
            let key_a = bbox_a
                .map(|bb| (bb.y0, bb.x0))
                .unwrap_or((f64::INFINITY, f64::INFINITY));
            let key_b = bbox_b
                .map(|bb| (bb.y0, bb.x0))
                .unwrap_or((f64::INFINITY, f64::INFINITY));
            key_a
                .partial_cmp(&key_b)
                .unwrap_or(std::cmp::Ordering::Equal)
        });
    }
    blocks
}

pub(crate) fn format_bbox_helper(bbox: Option<crate::ir::BBox>) -> String {
    match bbox {
        Some(b) => {
            let fmt_coord = |v: f64| -> String {
                let r = (v * 100.0).round() / 100.0;
                if (r - r.round()).abs() < 1e-9 {
                    format!("{:.1}", r)
                } else {
                    let mut s = format!("{:.2}", r);
                    if s.ends_with('0') {
                        s.pop();
                    }
                    s
                }
            };
            format!(
                "[{}, {}, {}, {}]",
                fmt_coord(b.x0),
                fmt_coord(b.y0),
                fmt_coord(b.x1),
                fmt_coord(b.y1)
            )
        }
        None => "[]".to_string(),
    }
}

pub(crate) fn looks_like_caption_row(row: &[String]) -> bool {
    let content: Vec<&str> = row
        .iter()
        .map(|c| c.trim())
        .filter(|c| !c.is_empty())
        .collect();
    if content.is_empty() {
        return false;
    }
    if content.len() == 1 {
        return true;
    }
    content.iter().any(|c| {
        let lower = c.to_lowercase();
        CAPTION_PREFIXES
            .iter()
            .any(|prefix| lower.starts_with(prefix))
    })
}

pub(crate) fn column_keys(band: &[Vec<String>], n_cols: usize) -> Vec<String> {
    let mut keys = Vec::with_capacity(n_cols);
    for ci in 0..n_cols {
        let mut parts = Vec::new();
        for r in band {
            if ci < r.len() {
                let cell = r[ci].trim();
                if !cell.is_empty() {
                    parts.push(cell);
                }
            }
        }
        let k = parts.join(" / ");
        if k.is_empty() {
            keys.push(format!("Колонка {}", ci + 1));
        } else {
            keys.push(k);
        }
    }
    keys
}

pub(crate) fn kv_lines(keys: &[String], rows: &[Vec<String>]) -> Vec<String> {
    let mut out = Vec::new();
    for row in rows {
        let mut pairs = Vec::new();
        for (ci, cell) in row.iter().enumerate() {
            let key = if ci < keys.len() { &keys[ci] } else { "" };
            let val = cell.trim();
            if !key.is_empty() && !val.is_empty() {
                pairs.push(format!("{}: {}", key, val));
            }
        }
        if !pairs.is_empty() {
            out.push(pairs.join(", "));
        }
    }
    out
}

pub(crate) fn parse_num(cell: &str) -> Option<f64> {
    let s = cell.trim().replace(['\u{a0}', ' '], "");
    let s = if let Some(stripped) = s.strip_suffix('%') {
        stripped.to_string()
    } else {
        s
    };
    let s = s.replace(',', ".");
    if s.is_empty() {
        return None;
    }
    static NUM_RE: std::sync::OnceLock<Regex> = std::sync::OnceLock::new();
    let re = NUM_RE.get_or_init(|| Regex::new(r"^-?\d+(?:\.\d+)?$").unwrap());
    if !re.is_match(&s) {
        return None;
    }
    s.parse::<f64>().ok()
}

pub(crate) fn fmt_num(x: f64) -> String {
    if (x - x.round()).abs() < 1e-9 {
        format!("{}", x.round() as i64)
    } else {
        let mut s = format!("{:.2}", x);
        while s.ends_with('0') {
            s.pop();
        }
        if s.ends_with('.') {
            s.pop();
        }
        s
    }
}

fn get_cell(rows: &[Vec<String>], row_idx: usize, ci: usize) -> &str {
    if row_idx < rows.len() && ci < rows[row_idx].len() {
        rows[row_idx][ci].trim()
    } else {
        ""
    }
}

pub(crate) fn facts_summary(keys: &[String], rows: &[Vec<String>], n_cols: usize) -> String {
    if rows.is_empty() {
        return format!("Сводка: {} кол., 0 строк данных.", n_cols);
    }

    let mut stats: BTreeMap<usize, Vec<f64>> = BTreeMap::new();
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
            stats.insert(ci, parsed);
        }
    }

    let mut parts = vec![format!(
        "Сводка: {} кол., {} строк данных.",
        n_cols,
        rows.len()
    )];
    for (&ci, vals) in &stats {
        let label = if ci < keys.len() { &keys[ci] } else { "" };
        let label_str = if label.is_empty() {
            format!("Колонка {}", ci + 1)
        } else {
            label.to_string()
        };
        let min_v = vals.iter().copied().fold(f64::INFINITY, f64::min);
        let max_v = vals.iter().copied().fold(f64::NEG_INFINITY, f64::max);
        let sum_v: f64 = vals.iter().sum();
        parts.push(format!(
            "{}: мин {}, макс {}, сумма {}.",
            label_str,
            fmt_num(min_v),
            fmt_num(max_v),
            fmt_num(sum_v)
        ));
    }

    let first = (0..n_cols)
        .map(|ci| get_cell(rows, 0, ci))
        .filter(|c| !c.is_empty())
        .collect::<Vec<_>>()
        .join(", ");
    if !first.is_empty() {
        parts.push(format!("Первая строка: {}.", first));
    }
    if rows.len() > 1 {
        let last_idx = rows.len() - 1;
        let last = (0..n_cols)
            .map(|ci| get_cell(rows, last_idx, ci))
            .filter(|c| !c.is_empty())
            .collect::<Vec<_>>()
            .join(", ");
        if !last.is_empty() {
            parts.push(format!("Последняя строка: {}.", last));
        }
    }

    parts.join(" ")
}

pub(crate) fn rows_to_pipe(band: &[Vec<String>], data: &[Vec<String>]) -> String {
    let width = band
        .iter()
        .chain(data.iter())
        .map(|r| r.len())
        .max()
        .unwrap_or(1)
        .max(1);

    let norm_cell = |text: &str| -> String {
        text.replace('|', "\\|")
            .replace('\n', " ")
            .trim()
            .to_string()
    };

    let line = |row: &[String]| -> String {
        let mut norm: Vec<String> = row.iter().map(|c| norm_cell(c)).collect();
        while norm.len() < width {
            norm.push(String::new());
        }
        format!("| {} |", norm.join(" | "))
    };

    let mut lines = Vec::new();
    for r in band {
        lines.push(line(r));
    }
    let sep: Vec<&str> = vec!["---"; width];
    lines.push(format!("| {} |", sep.join(" | ")));
    for r in data {
        lines.push(line(r));
    }
    lines.join("\n")
}

pub(crate) fn pipe_regions(markdown: &str) -> Vec<(usize, usize, String)> {
    static ROW_RE: std::sync::OnceLock<Regex> = std::sync::OnceLock::new();
    static SEP_RE: std::sync::OnceLock<Regex> = std::sync::OnceLock::new();
    let row_re = ROW_RE.get_or_init(|| Regex::new(r"^\s*\|.*\|\s*$").unwrap());
    let sep_re =
        SEP_RE.get_or_init(|| Regex::new(r"^\s*\|?\s*:?-+:?\s*(\|\s*:?-+:?\s*)+\|?\s*$").unwrap());

    let lines: Vec<&str> = markdown.lines().collect();
    let mut regions = Vec::new();
    let mut i = 0;
    let n = lines.len();
    while i < n {
        if row_re.is_match(lines[i]) && i + 1 < n && sep_re.is_match(lines[i + 1]) {
            let mut j = i + 2;
            while j < n && row_re.is_match(lines[j]) {
                j += 1;
            }
            regions.push((i, j, lines[i..j].join("\n")));
            i = j;
        } else {
            i += 1;
        }
    }
    regions
}

pub(crate) fn norm_block(text: &str) -> String {
    text.trim()
        .lines()
        .map(|ln| ln.trim())
        .collect::<Vec<_>>()
        .join("\n")
}

pub(crate) fn strip_tables(markdown: &str, page: &PageResult) -> String {
    if markdown.trim().is_empty() {
        return markdown.to_string();
    }
    let table_blocks: Vec<&TableBlock> = page
        .blocks
        .iter()
        .filter_map(|b| match b {
            Block::Table(t) => Some(t),
            _ => None,
        })
        .collect();
    if table_blocks.is_empty() {
        return markdown.to_string();
    }
    let ocr_page = table_blocks.iter().any(|b| b.source.starts_with("ocr_"));
    let known: HashSet<String> = table_blocks
        .iter()
        .map(|b| norm_block(&b.markdown))
        .collect();

    let lines: Vec<&str> = markdown.lines().collect();
    let mut drop = HashSet::new();
    for (start, end, text) in pipe_regions(markdown) {
        if ocr_page || known.contains(&norm_block(&text)) {
            for idx in start..end {
                drop.insert(idx);
            }
        }
    }
    if drop.is_empty() {
        return markdown.to_string();
    }
    let kept: Vec<&str> = lines
        .iter()
        .enumerate()
        .filter(|(idx, _)| !drop.contains(idx))
        .map(|(_, ln)| *ln)
        .collect();
    kept.join("\n")
}

pub(crate) fn split_blocks(markdown: &str) -> Vec<(Option<String>, String)> {
    if markdown.trim().is_empty() {
        return Vec::new();
    }
    static HEADING_RE: std::sync::OnceLock<Regex> = std::sync::OnceLock::new();
    let heading_re = HEADING_RE.get_or_init(|| Regex::new(r"^(#{2,4})\s+(.+)$").unwrap());

    let lines: Vec<&str> = markdown.lines().collect();
    let mut blocks = Vec::new();
    let mut current_section: Option<String> = None;
    let mut current_lines: Vec<&str> = Vec::new();

    let mut flush = |curr_sec: &Option<String>, curr_lns: &mut Vec<&str>| {
        let text = curr_lns.join("\n").trim().to_string();
        if !text.is_empty() {
            blocks.push((curr_sec.clone(), text));
        }
        curr_lns.clear();
    };

    for line in lines {
        if let Some(caps) = heading_re.captures(line) {
            flush(&current_section, &mut current_lines);
            current_section = Some(caps[2].trim().to_string());
            current_lines.push(line);
        } else if line.trim().is_empty() {
            flush(&current_section, &mut current_lines);
        } else {
            current_lines.push(line);
        }
    }
    flush(&current_section, &mut current_lines);
    blocks
}

pub(crate) fn split_long(text: &str, max_chars: usize, overlap_chars: usize) -> Vec<String> {
    let chars: Vec<char> = text.chars().collect();
    if chars.len() <= max_chars {
        return vec![text.to_string()];
    }
    let overlap_chars = overlap_chars.min(max_chars.saturating_sub(1));
    let mut pieces = Vec::new();
    let mut start = 0;
    while start < chars.len() {
        let end = (start + max_chars).min(chars.len());
        let piece: String = chars[start..end].iter().collect();
        let piece = piece.trim().to_string();
        if !piece.is_empty() {
            pieces.push(piece);
        }
        if start + max_chars >= chars.len() {
            break;
        }
        start += max_chars - overlap_chars;
    }
    if pieces.is_empty() {
        vec![text.to_string()]
    } else {
        pieces
    }
}

#[allow(clippy::too_many_arguments)]
pub(crate) fn table_parts(
    page: &PageResult,
    block: &TableBlock,
    table_no: usize,
    doc_id: Option<&str>,
    doc_title: Option<&str>,
    heading: Option<&str>,
    section: Option<&str>,
    source_format: &str,
    cfg: &ChunksConfig,
) -> Vec<ChunkEnvelope> {
    let rows = &block.rows;
    let n_cols = rows.iter().map(|r| r.len()).max().unwrap_or(0);
    if rows.is_empty() || n_cols == 0 {
        return Vec::new();
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

    let crumbs = [doc_title, heading]
        .into_iter()
        .flatten()
        .filter(|p| !p.is_empty())
        .collect::<Vec<_>>()
        .join(" › ");

    let mut head = Vec::new();
    if cfg.embed_context && !crumbs.is_empty() {
        head.push(crumbs);
    }
    if let Some(cap) = &caption {
        head.push(cap.clone());
    }

    let embed_of = |part_rows: &[Vec<String>]| -> String {
        let mut pieces = head.clone();
        pieces.extend(kv_lines(&keys, part_rows));
        if cfg.summary == "facts" {
            pieces.push(facts_summary(&keys, part_rows, n_cols));
        }
        pieces.join("\n")
    };

    let md_of = |part_rows: &[Vec<String>]| -> String {
        let title_line = heading.or(caption.as_deref());
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
        if cfg.summary == "facts" {
            out.push(facts_summary(&keys, part_rows, n_cols));
        }
        out.into_iter()
            .filter(|ln| !ln.is_empty())
            .collect::<Vec<_>>()
            .join("\n")
    };

    let whole_embed = embed_of(data);
    let whole_tokens = whole_embed.chars().count() / 4;

    let groups: Vec<Vec<Vec<String>>> = if data.is_empty() || whole_tokens <= cfg.max_tokens {
        vec![data.to_vec()]
    } else {
        let mut groups = Vec::new();
        let mut current: Vec<Vec<String>> = Vec::new();
        for row in data {
            let mut test_group = current.clone();
            test_group.push(row.clone());
            if !current.is_empty() && (embed_of(&test_group).chars().count() / 4) > cfg.max_tokens {
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

    let table_id = format!("{}-t{}", page.page, table_no);
    let single = groups.len() == 1;
    let mut out = Vec::new();

    for (k_idx, part_rows) in groups.iter().enumerate() {
        let k = k_idx + 1;
        let chunk_id = if single {
            table_id.clone()
        } else {
            format!("{}-{:02}", table_id, k)
        };
        let chunk_type = if single { "table" } else { "table_part" };

        let meta = ChunkMetadata {
            doc_id: doc_id.map(ToString::to_string),
            doc_title: doc_title.map(ToString::to_string),
            source_format: source_format.to_string(),
            page: page.page,
            section: section.map(ToString::to_string),
            src_tool: page.src_tool.as_str().to_string(),
            error: page.error.clone(),
            table: Some(TableChunkMeta {
                table_id: table_id.clone(),
                part: if single { 1 } else { k },
                parts: groups.len(),
                n_rows: rows.len(),
                n_cols,
                header: keys.clone(),
                source: block.source.clone(),
            }),
        };

        out.push(ChunkEnvelope {
            chunk_id,
            chunk_version: 2,
            chunk_type: chunk_type.to_string(),
            embed: embed_of(part_rows),
            markdown: md_of(part_rows),
            metadata: meta,
            images: page.images.clone(),
        });
    }

    out
}

/// Streaming iterator yielding hybrid RAG chunks one by one with O(1) memory overhead.
pub struct ChunksIterator<'a> {
    ir: &'a Ir,
    config: ChunksConfig,
    page_idx: usize,
    stack: HeadingStack,
    buffer: VecDeque<ChunkEnvelope>,
    doc_id: Option<String>,
    doc_title: Option<String>,
}

impl<'a> ChunksIterator<'a> {
    pub fn new(ir: &'a Ir, config: ChunksConfig) -> Self {
        let (doc_id, doc_title) = doc_identity(ir);
        Self {
            ir,
            config,
            page_idx: 0,
            stack: HeadingStack::new(),
            buffer: VecDeque::new(),
            doc_id,
            doc_title,
        }
    }

    fn refill_buffer(&mut self) -> bool {
        static HEADING_RE: std::sync::OnceLock<Regex> = std::sync::OnceLock::new();
        let heading_re = HEADING_RE.get_or_init(|| Regex::new(r"(?m)^(#{2,4})\s+(.+)$").unwrap());

        while self.buffer.is_empty() && self.page_idx < self.ir.pages.len() {
            let page = &self.ir.pages[self.page_idx];
            self.page_idx += 1;

            let ordered = reading_order(page);
            let mut md_headings = Vec::new();
            for cap in heading_re.captures_iter(&page.markdown) {
                md_headings.push(cap[2].trim().to_string());
            }
            let fallback_section = md_headings.first().cloned();

            let mut table_jobs = Vec::new();
            let mut table_no = 0;
            for block in ordered {
                match block {
                    Block::Heading(h) => {
                        self.stack.push(h.level, h.text.clone());
                    }
                    Block::Table(t) => {
                        table_no += 1;
                        let snap = self.stack.deepest().map(ToString::to_string);
                        let eff_section = snap.clone().or_else(|| fallback_section.clone());
                        table_jobs.push((t, snap, eff_section, table_no));
                    }
                    _ => {}
                }
            }

            for (block, heading, section, no) in table_jobs {
                let parts = table_parts(
                    page,
                    block,
                    no,
                    self.doc_id.as_deref(),
                    self.doc_title.as_deref(),
                    heading.as_deref(),
                    section.as_deref(),
                    &self.ir.metadata.source_format,
                    &self.config,
                );
                for p in parts {
                    self.buffer.push_back(p);
                }
            }

            let remaining = strip_tables(&page.markdown, page);
            let max_chars_full = self.config.max_tokens * 4;
            let overlap_chars = self.config.overlap_tokens * 4;

            let mut seq = 0;
            for (section, text) in split_blocks(&remaining) {
                let preamble = if self.config.embed_context {
                    let eff_sec = section.as_deref().or_else(|| self.stack.deepest());
                    [self.doc_title.as_deref(), eff_sec]
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
                    let embed = if !preamble.is_empty() {
                        format!("{}\n{}", preamble, piece)
                    } else {
                        piece.clone()
                    };

                    self.buffer.push_back(ChunkEnvelope {
                        chunk_id: format!("{}-{:03}", page.page, seq),
                        chunk_version: 2,
                        chunk_type: "text".to_string(),
                        embed,
                        markdown: piece,
                        metadata: ChunkMetadata {
                            doc_id: self.doc_id.clone(),
                            doc_title: self.doc_title.clone(),
                            source_format: self.ir.metadata.source_format.clone(),
                            page: page.page,
                            section: section.clone(),
                            src_tool: page.src_tool.as_str().to_string(),
                            error: page.error.clone(),
                            table: None,
                        },
                        images: page.images.clone(),
                    });
                }
            }
        }

        !self.buffer.is_empty()
    }
}

impl<'a> Iterator for ChunksIterator<'a> {
    type Item = ChunkEnvelope;

    fn next(&mut self) -> Option<Self::Item> {
        if self.buffer.is_empty() && !self.refill_buffer() {
            return None;
        }
        self.buffer.pop_front()
    }
}

/// Create a streaming iterator over RAG chunks for an IR.
pub fn iter_chunks<'a>(ir: &'a Ir, config: &ChunksConfig) -> ChunksIterator<'a> {
    ChunksIterator::new(ir, config.clone())
}

/// Render an IR to JSONL string containing one hybrid RAG envelope per line.
pub fn render_chunks(ir: &Ir, config: &ChunksConfig) -> String {
    let mut out = String::new();
    for chunk in iter_chunks(ir, config) {
        if let Ok(line) = serde_json::to_string(&chunk) {
            out.push_str(&line);
            out.push('\n');
        }
    }
    out
}
