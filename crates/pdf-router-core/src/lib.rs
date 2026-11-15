//! Core library for pdf-router.
//!
//! Ultra-minimal, deterministic, zero-overhead document router.

pub mod adapters;
pub mod error;
pub mod formats;
pub mod ir;
pub mod ocr;
pub mod renderers;

pub use error::RouterError;
pub use formats::{detect_format, detect_format_path, Format};
pub use ir::{
    BBox, Block, FigureBlock, FormulaBlock, HeadingBlock, Ir, Metadata, OcrReasonEntry, PageResult,
    SrcTool, TableBlock, IR_VERSION,
};
pub use ocr::{MockOcrEngine, OcrEngine, OcrError, OcrResult, PageRaster, TesseractEngine};
pub use renderers::{render_json, render_markdown};

use serde::{Deserialize, Serialize};
use std::fs;
use std::path::Path;
use std::sync::Arc;

/// Processing execution options.
#[derive(Clone, Default)]
pub struct ProcessOptions {
    pub strict: bool,
    pub ocr_engine: Option<Arc<dyn OcrEngine>>,
    pub ocr_dpi: Option<u32>,
}

impl std::fmt::Debug for ProcessOptions {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        f.debug_struct("ProcessOptions")
            .field("strict", &self.strict)
            .field("ocr_engine", &self.ocr_engine.as_ref().map(|e| e.name()))
            .field("ocr_dpi", &self.ocr_dpi)
            .finish()
    }
}

/// Aggregated report of document processing.
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize, Default)]
pub struct ProcessingReport {
    pub pages_total: usize,
    pub pages_native: usize,
    pub pages_ocr: usize,
    pub pages_failed: usize,
}

/// Process a document from disk into an IR.
pub fn process_document<P: AsRef<Path>>(path: P) -> Result<Ir, RouterError> {
    let (ir, _report) = process_document_with_options(path, &ProcessOptions::default())?;
    Ok(ir)
}

/// Process a document from disk into an IR with explicit options and report tracking.
pub fn process_document_with_options<P: AsRef<Path>>(
    path: P,
    options: &ProcessOptions,
) -> Result<(Ir, ProcessingReport), RouterError> {
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

    let mut ir = match format {
        Format::Pdf => adapters::pdf::process_pdf(&bytes, Some(&p.to_string_lossy()))?,
        Format::Docx => adapters::docx::process_docx(&bytes, Some(&p.to_string_lossy()))?,
        Format::Xlsx => adapters::xlsx::process_xlsx(&bytes, Some(&p.to_string_lossy()))?,
        Format::Doc => adapters::doc::process_doc(&bytes, Some(&p.to_string_lossy()))?,
        Format::Djvu => adapters::djvu::process_djvu(&bytes, Some(&p.to_string_lossy()))?,
        Format::Html => adapters::html::process_html(&bytes, Some(&p.to_string_lossy()))?,
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

            Ir::new(metadata, vec![page])
        }
    };

    // 3. Process pages: run OCR on pages needing it, collect processing report
    let mut report = ProcessingReport {
        pages_total: ir.pages.len(),
        ..Default::default()
    };

    let dpi = options.ocr_dpi.unwrap_or(200);

    for page in &mut ir.pages {
        if page.needs_ocr {
            if let Some(engine) = &options.ocr_engine {
                let raster_result = match format {
                    Format::Djvu => adapters::djvu::render_page_ppm_djvu(p, page.page, dpi),
                    _ => {
                        let w = 100u32;
                        let h = 100u32;
                        let mut data = format!("P6\n{w} {h}\n255\n").into_bytes();
                        data.resize(data.len() + (w * h * 3) as usize, 255);
                        Ok(PageRaster::new(page.page, w, h, dpi, data))
                    }
                };

                match raster_result {
                    Ok(raster) => match engine.ocr_page(raster) {
                        Ok(ocr_res) => {
                            page.markdown = ocr_res.text;
                            page.blocks = ocr_res.blocks;
                            page.src_tool = SrcTool::from_engine(engine.name());
                            page.needs_ocr = false;
                            page.error = None;
                            report.pages_ocr += 1;
                        }
                        Err(e) => {
                            page.error = Some(e.to_string());
                            report.pages_failed += 1;
                        }
                    },
                    Err(e) => {
                        page.error = Some(e.to_string());
                        report.pages_failed += 1;
                    }
                }
            } else {
                report.pages_native += 1;
            }
        } else if page.error.is_some() {
            report.pages_failed += 1;
        } else {
            report.pages_native += 1;
        }
    }

    if options.strict && report.pages_failed > 0 {
        return Err(RouterError::Processing(format!(
            "strict mode: {} of {} pages failed processing",
            report.pages_failed, report.pages_total
        )));
    }

    Ok((ir, report))
}
