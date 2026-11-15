//! Core library for pdf-router.
//!
//! Ultra-minimal, deterministic, zero-overhead document router.

pub mod adapters;
pub mod error;
pub mod formats;
pub mod ir;
pub mod renderers;

pub use error::RouterError;
pub use formats::{detect_format, detect_format_path, Format};
pub use ir::{
    BBox, Block, FigureBlock, FormulaBlock, HeadingBlock, Ir, Metadata, OcrReasonEntry, PageResult,
    SrcTool, TableBlock, IR_VERSION,
};
pub use renderers::{
    iter_chunks, iter_zvec_docs, render_chunks, render_json, render_markdown, render_zvec,
    ChunkEnvelope, ChunkMetadata, ChunksConfig, TableChunkMeta, ZvecConfig, ZvecDocRecord,
    ZvecFields, ZvecMetadata,
};

use std::fs;
use std::path::Path;

/// Process a document from disk into an IR.
pub fn process_document<P: AsRef<Path>>(path: P) -> Result<Ir, RouterError> {
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

    let bytes = fs::read(p)?;
    if bytes.is_empty() {
        return Err(RouterError::UnsupportedFormat(format!(
            "empty file: {}",
            p.display()
        )));
    }

    // 2. Sniff format
    let format = detect_format(&bytes);

    match format {
        Format::Pdf => adapters::pdf::process_pdf(&bytes, Some(&p.to_string_lossy())),
        Format::Docx => adapters::docx::process_docx(&bytes, Some(&p.to_string_lossy())),
        Format::Xlsx => adapters::xlsx::process_xlsx(&bytes, Some(&p.to_string_lossy())),
        Format::Doc => adapters::doc::process_doc(&bytes, Some(&p.to_string_lossy())),
        Format::Html => adapters::html::process_html(&bytes, Some(&p.to_string_lossy())),
        _ => {
            let content = String::from_utf8_lossy(&bytes).into_owned();
            let metadata = Metadata {
                source_format: format.as_str().to_string(),
                source_path: Some(p.to_string_lossy().to_string()),
                pdf_type: "text_based".to_string(),
                page_count: 1,
                confidence: 1.0,
                ..Default::default()
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
    }
}
