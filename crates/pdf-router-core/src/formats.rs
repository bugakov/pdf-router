//! Format detection from magic bytes.

use serde::{Deserialize, Serialize};

/// Recognized document formats.
#[derive(Debug, Clone, Copy, PartialEq, Eq, Hash, Serialize, Deserialize)]
#[serde(rename_all = "lowercase")]
pub enum Format {
    Pdf,
    Docx,
    Xlsx,
    Doc,
    Djvu,
    Html,
    Zip,
    Unknown,
}

impl Format {
    pub fn as_str(&self) -> &'static str {
        match self {
            Format::Pdf => "pdf",
            Format::Docx => "docx",
            Format::Xlsx => "xlsx",
            Format::Doc => "doc",
            Format::Djvu => "djvu",
            Format::Html => "html",
            Format::Zip => "zip",
            Format::Unknown => "unknown",
        }
    }
}

/// Detect document format from initial bytes (magic bytes).
///
/// Precedence:
/// - `%PDF-` -> PDF
/// - `\xD0\xCF\x11\xE0\xA1\xB1\x1A\xE1` -> OLE2 (legacy DOC / XLS)
/// - `AT&TFORM` followed by `DJVU` / `DJVM` -> DJVU
/// - `PK\x03\x04` -> ZIP container (OOXML: docx, xlsx)
/// - `<!DOCTYPE html` or `<html` -> HTML
pub fn detect_format(bytes: &[u8]) -> Format {
    if bytes.starts_with(b"%PDF-") {
        return Format::Pdf;
    }

    // OLE2 Compound File Binary Format (Word .doc, Excel .xls)
    if bytes.starts_with(&[0xD0, 0xCF, 0x11, 0xE0, 0xA1, 0xB1, 0x1A, 0xE1]) {
        return Format::Doc;
    }

    // DjVu file magic: 'AT&TFORM' + 4-byte chunk size + 'DJVU' or 'DJVM'
    if bytes.len() >= 16 && &bytes[0..4] == b"AT&T" && &bytes[4..8] == b"FORM" {
        let form_type = &bytes[12..16];
        if form_type == b"DJVU" || form_type == b"DJVM" {
            return Format::Djvu;
        }
    }

    // ZIP container (DOCX / XLSX)
    if bytes.starts_with(&[0x50, 0x4B, 0x03, 0x04]) {
        return Format::Zip;
    }

    // HTML heuristics (trimming leading whitespace/BOM)
    let s = String::from_utf8_lossy(&bytes[..bytes.len().min(512)]);
    let trimmed = s.trim_start();
    let lower = trimmed.to_lowercase();
    if lower.starts_with("<!doctype html") || lower.starts_with("<html") {
        return Format::Html;
    }

    Format::Unknown
}
