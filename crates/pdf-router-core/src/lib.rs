//! Core library for pdf-router.
//!
//! Ultra-minimal, deterministic, zero-overhead document router.

pub mod error;
pub mod formats;
pub mod ir;
pub mod renderers;

pub use error::RouterError;
pub use formats::{detect_format, Format};
pub use ir::{
    BBox, Block, HeadingBlock, Ir, Metadata, PageResult, SrcTool, TableBlock, IR_VERSION,
};
pub use renderers::{render_json, render_markdown};

use std::fs;
use std::path::Path;

/// Process a document from disk into an IR.
///
/// In Ticket 01, this provides baseline magic byte detection and stub
/// handling for initial end-to-end tracer-bullet verification.
pub fn process_document<P: AsRef<Path>>(path: P) -> Result<Ir, RouterError> {
    let p = path.as_ref();
    let bytes = fs::read(p)?;
    let format = detect_format(&bytes);

    let content = String::from_utf8_lossy(&bytes).into_owned();
    let metadata = Metadata {
        source_format: format.as_str().to_string(),
        source_path: Some(p.to_string_lossy().to_string()),
        pdf_type: "text_based".to_string(),
        page_count: 1,
        pages_with_tables: vec![],
        pages_needing_ocr: vec![],
        confidence: 1.0,
        title: None,
    };

    let page = PageResult {
        page: 0,
        markdown: content,
        src_tool: SrcTool::Stub,
        blocks: vec![],
        images: vec![],
        error: None,
        needs_ocr: false,
    };

    Ok(Ir::new(metadata, vec![page]))
}
