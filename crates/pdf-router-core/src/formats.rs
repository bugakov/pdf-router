//! Format detection from magic bytes and file paths.

use std::fs::File;
use std::io::Read;
use std::path::Path;

use serde::{Deserialize, Serialize};

use crate::error::RouterError;

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
/// - `%PDF-` in first 1024 bytes -> PDF
/// - `AT&TFORM` followed by `DJVU` / `DJVM` -> DJVU
/// - `PK\x03\x04` -> ZIP container (OOXML: docx, xlsx)
/// - `\xD0\xCF\x11\xE0\xA1\xB1\x1A\xE1` -> OLE2 (legacy DOC / XLS)
/// - `<!DOCTYPE html` or `<html` -> HTML
pub fn detect_format(bytes: &[u8]) -> Format {
    // 1. PDF: spec header or real-world leading junk up to 1 KiB
    let window = &bytes[..bytes.len().min(1024)];
    if bytes.starts_with(b"%PDF-") || window.windows(5).any(|w| w == b"%PDF-") {
        return Format::Pdf;
    }

    // 2. DjVu file magic: 'AT&TFORM' + 4-byte chunk size + 'DJVU' or 'DJVM'
    if bytes.len() >= 16 && &bytes[0..4] == b"AT&T" && &bytes[4..8] == b"FORM" {
        let form_type = &bytes[12..16];
        if form_type == b"DJVU" || form_type == b"DJVM" {
            return Format::Djvu;
        }
    }

    // 3. ZIP container (DOCX / XLSX)
    if bytes.starts_with(&[0x50, 0x4B, 0x03, 0x04]) {
        if let Ok(mut archive) = zip::ZipArchive::new(std::io::Cursor::new(bytes)) {
            if archive.by_name("word/document.xml").is_ok() {
                return Format::Docx;
            }
            if archive.by_name("xl/workbook.xml").is_ok() {
                return Format::Xlsx;
            }
        }
        return Format::Zip;
    }

    // 4. OLE2 Compound File Binary Format (Word .doc, Excel .xls)
    if bytes.starts_with(&[0xD0, 0xCF, 0x11, 0xE0, 0xA1, 0xB1, 0x1A, 0xE1]) {
        return Format::Doc;
    }

    // 5. HTML heuristics (trimming BOM and leading whitespace)
    let s = String::from_utf8_lossy(&bytes[..bytes.len().min(1024)]);
    let trimmed = s.trim_start_matches(|c: char| c.is_whitespace() || c == '\u{FEFF}');
    let lower = trimmed.to_lowercase();
    if lower.starts_with("<!doctype html") || lower.starts_with("<html") {
        return Format::Html;
    }

    Format::Unknown
}

/// Detect document format from file path with rich diagnostics.
///
/// Distinguishes Word lock files (`~$...`), corrupt archives, PowerPoint (`.pptx`),
/// and unknown formats cleanly.
pub fn detect_format_path<P: AsRef<Path>>(path: P) -> Result<Format, RouterError> {
    let p = path.as_ref();

    // 1. Check for Word lock / owner files (~$...)
    if let Some(name) = p.file_name().and_then(|f| f.to_str()) {
        if name.starts_with("~$") {
            return Err(RouterError::UnsupportedFormat(format!(
                "Word lock/owner file (~$…), not a document: {}",
                p.display()
            )));
        }
    }

    // 2. Read first 1 KiB
    let mut file = File::open(p)?;
    let mut head = vec![0u8; 1024];
    let n = file.read(&mut head)?;
    head.truncate(n);

    if head.is_empty() {
        return Err(RouterError::UnsupportedFormat(format!(
            "empty file: {}",
            p.display()
        )));
    }

    // 3. Inspect ZIP packages
    if head.starts_with(&[0x50, 0x4B, 0x03, 0x04])
        || head.starts_with(&[0x50, 0x4B, 0x05, 0x06])
        || head.starts_with(&[0x50, 0x4B, 0x07, 0x08])
    {
        let file = File::open(p)?;
        match zip::ZipArchive::new(file) {
            Ok(mut archive) => {
                if archive.by_name("word/document.xml").is_ok() {
                    return Ok(Format::Docx);
                }
                if archive.by_name("xl/workbook.xml").is_ok() {
                    return Ok(Format::Xlsx);
                }
                if archive.by_name("ppt/presentation.xml").is_ok() {
                    return Err(RouterError::UnsupportedFormat(format!(
                        "PowerPoint pptx is not routed (out of scope): {}",
                        p.display()
                    )));
                }
                if archive.by_name("[Content_Types].xml").is_ok() {
                    return Err(RouterError::UnsupportedFormat(format!(
                        "OOXML package that is not docx/xlsx (pptx?): {}",
                        p.display()
                    )));
                }
                return Err(RouterError::UnsupportedFormat(format!(
                    "zip archive, not a routed document format: {}",
                    p.display()
                )));
            }
            Err(e) => {
                return Err(RouterError::UnsupportedFormat(format!(
                    "corrupt zip archive: {} ({e})",
                    p.display()
                )));
            }
        }
    }

    // 4. Sniff format from head bytes
    let fmt = detect_format(&head);
    if fmt == Format::Unknown {
        let hex: Vec<String> = head[..head.len().min(8)]
            .iter()
            .map(|b| format!("{b:02x}"))
            .collect();
        return Err(RouterError::UnsupportedFormat(format!(
            "unrecognized file format: {} (starts with {})",
            p.display(),
            hex.join(" ")
        )));
    }

    Ok(fmt)
}
