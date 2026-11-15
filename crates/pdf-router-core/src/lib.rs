//! Core library for pdf-router.
//!
//! Ultra-minimal, deterministic, zero-overhead document router.

pub mod adapters;
pub mod error;
pub mod formats;
pub mod images;
pub mod ir;
pub mod ocr;
pub mod renderers;

pub use error::RouterError;
pub use formats::{detect_format, detect_format_path, Format};
pub use images::{doc_id_from_path, keep_raster_photo, page_images_dir};
pub use ir::{
    BBox, Block, FigureBlock, FormulaBlock, HeadingBlock, Ir, Metadata, OcrReasonEntry, PageResult,
    SrcTool, TableBlock, IR_VERSION,
};
pub use ocr::{MockOcrEngine, OcrEngine, OcrError, OcrResult, PageRaster, TesseractEngine};
pub use renderers::{
    iter_chunks, iter_zvec_docs, render_chunks, render_json, render_markdown, render_zvec,
    ChunkEnvelope, ChunkMetadata, ChunksConfig, TableChunkMeta, ZvecConfig, ZvecDocRecord,
    ZvecFields, ZvecMetadata,
};

use serde::{Deserialize, Serialize};
use std::fs;
use std::path::{Path, PathBuf};
use std::sync::Arc;

/// Processing execution options.
#[derive(Clone)]
pub struct ProcessOptions {
    pub strict: bool,
    pub ocr_engine: Option<Arc<dyn OcrEngine>>,
    pub ocr_dpi: Option<u32>,
    pub output_dir: Option<PathBuf>,
    pub images_min_size: usize,
    pub images_min_thin: usize,
    pub images_max_aspect_ratio: f64,
    pub extract_raster_photos: bool,
    pub extract_vector_diagrams: bool,
}

impl Default for ProcessOptions {
    fn default() -> Self {
        Self {
            strict: false,
            ocr_engine: None,
            ocr_dpi: Some(200),
            output_dir: None,
            images_min_size: 150,
            images_min_thin: 8,
            images_max_aspect_ratio: 4.0,
            extract_raster_photos: true,
            extract_vector_diagrams: false,
        }
    }
}

impl ProcessOptions {
    pub fn new() -> Self {
        Self::default()
    }

    pub fn with_strict(mut self, strict: bool) -> Self {
        self.strict = strict;
        self
    }

    pub fn with_ocr_engine(mut self, engine: Arc<dyn OcrEngine>) -> Self {
        self.ocr_engine = Some(engine);
        self
    }

    pub fn with_ocr_dpi(mut self, dpi: u32) -> Self {
        self.ocr_dpi = Some(dpi);
        self
    }

    pub fn with_output_dir(mut self, dir: impl Into<PathBuf>) -> Self {
        self.output_dir = Some(dir.into());
        self
    }

    pub fn with_images_size_gate(mut self, min_size: usize, min_thin: usize, max_ar: f64) -> Self {
        self.images_min_size = min_size;
        self.images_min_thin = min_thin;
        self.images_max_aspect_ratio = max_ar;
        self
    }
}

impl std::fmt::Debug for ProcessOptions {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        f.debug_struct("ProcessOptions")
            .field("strict", &self.strict)
            .field("ocr_engine", &self.ocr_engine.as_ref().map(|e| e.name()))
            .field("ocr_dpi", &self.ocr_dpi)
            .field("output_dir", &self.output_dir)
            .field("images_min_size", &self.images_min_size)
            .field("images_min_thin", &self.images_min_thin)
            .field("images_max_aspect_ratio", &self.images_max_aspect_ratio)
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
    #[serde(default)]
    pub images_dropped: usize,
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

    // 4. Extract sidecar images if output_dir is configured
    if let Some(ref out_dir) = options.output_dir {
        let doc_id = doc_id_from_path(&p.to_string_lossy());
        let (images_by_page, dropped) = match format {
            Format::Pdf => images::extract_pdf_images(&bytes, &doc_id, out_dir, options)?,
            Format::Docx => images::extract_docx_images(&bytes, &doc_id, out_dir, options)?,
            _ => (std::collections::HashMap::new(), 0),
        };
        report.images_dropped += dropped;
        for page in &mut ir.pages {
            if let Some(imgs) = images_by_page.get(&page.page) {
                page.images = imgs.clone();
            }
        }
    }

    Ok((ir, report))
}
