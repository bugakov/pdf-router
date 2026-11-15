//! Intermediate Representation (IR 0.6) data structures.

use serde::{Deserialize, Serialize};

pub const IR_VERSION: &str = "0.6";

/// A 2D axis-aligned bounding box in points or raster pixels.
#[derive(Debug, Clone, Copy, PartialEq, Serialize, Deserialize)]
pub struct BBox {
    pub x0: f64,
    pub y0: f64,
    pub x1: f64,
    pub y1: f64,
}

/// Tool/adapter responsible for page content extraction.
#[derive(Debug, Clone, Copy, PartialEq, Eq, Hash, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum SrcTool {
    PdfInspector,
    TextLayerUnusable,
    Rapidocr,
    PdfInspectorOcr,
    Tesseract,
    GlmOcr,
    DeepseekOcr,
    DocxAdapter,
    DjvuAdapter,
    DjvuTextLayer,
    XlsxAdapter,
    DocAdapter,
    HtmlAdapter,
    MockOcr,
    Stub,
}

impl SrcTool {
    pub fn as_str(&self) -> &'static str {
        match self {
            SrcTool::PdfInspector => "pdf_inspector",
            SrcTool::TextLayerUnusable => "text_layer_unusable",
            SrcTool::Rapidocr => "rapidocr",
            SrcTool::PdfInspectorOcr => "pdf_inspector_ocr",
            SrcTool::Tesseract => "tesseract",
            SrcTool::GlmOcr => "glm_ocr",
            SrcTool::DeepseekOcr => "deepseek_ocr",
            SrcTool::DocxAdapter => "docx_adapter",
            SrcTool::DjvuAdapter => "djvu_adapter",
            SrcTool::DjvuTextLayer => "djvu_text_layer",
            SrcTool::XlsxAdapter => "xlsx_adapter",
            SrcTool::DocAdapter => "doc_adapter",
            SrcTool::HtmlAdapter => "html_adapter",
            SrcTool::MockOcr => "mock_ocr",
            SrcTool::Stub => "stub",
        }
    }

    pub fn from_engine(name: &str) -> Self {
        match name {
            "tesseract" => SrcTool::Tesseract,
            "rapidocr" => SrcTool::Rapidocr,
            "pdf_inspector_ocr" => SrcTool::PdfInspectorOcr,
            "glm_ocr" => SrcTool::GlmOcr,
            "deepseek_ocr" => SrcTool::DeepseekOcr,
            "mock" | "mock_ocr" => SrcTool::MockOcr,
            _ => SrcTool::Stub,
        }
    }
}

impl std::fmt::Display for SrcTool {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        write!(f, "{}", self.as_str())
    }
}
#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
pub struct HeadingBlock {
    pub level: usize,
    pub text: String,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub bbox: Option<BBox>,
    pub source: String,
}

#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
pub struct TableBlock {
    pub markdown: String,
    pub rows: Vec<Vec<String>>,
    pub header_rows: usize,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub bbox: Option<BBox>,
    pub source: String,
}

#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
pub struct FigureBlock {
    #[serde(skip_serializing_if = "Option::is_none")]
    pub bbox: Option<BBox>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub caption: Option<String>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub image_index: Option<usize>,
}

#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
pub struct FormulaBlock {
    pub latex: String,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub bbox: Option<BBox>,
    pub source: String,
}

/// Structural block on a page.
#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
#[serde(tag = "kind")]
pub enum Block {
    #[serde(rename = "HeadingBlock")]
    Heading(HeadingBlock),
    #[serde(rename = "TableBlock")]
    Table(TableBlock),
    #[serde(rename = "FigureBlock")]
    Figure(FigureBlock),
    #[serde(rename = "FormulaBlock")]
    Formula(FormulaBlock),
}

/// Extracted page result.
#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
pub struct PageResult {
    pub page: usize,
    pub markdown: String,
    pub src_tool: SrcTool,
    #[serde(default)]
    pub blocks: Vec<Block>,
    #[serde(default)]
    pub images: Vec<String>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub error: Option<String>,
    #[serde(default)]
    pub needs_ocr: bool,
}

/// One entry of `ocr_reasons_by_page` (1-indexed page).
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize, Default)]
pub struct OcrReasonEntry {
    pub page: usize,
    #[serde(default)]
    pub reasons: Vec<String>,
}

/// Document-level metadata.
#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
pub struct Metadata {
    pub source_format: String,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub source_path: Option<String>,
    pub pdf_type: String,
    pub page_count: usize,
    #[serde(default)]
    pub confidence: f64,
    #[serde(default)]
    pub processing_time_ms: f64,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub title: Option<String>,
    #[serde(default)]
    pub is_complex_layout: bool,
    #[serde(default)]
    pub has_encoding_issues: bool,
    #[serde(default)]
    pub pages_with_tables: Vec<usize>,
    #[serde(default)]
    pub pages_with_columns: Vec<usize>,
    #[serde(default)]
    pub pages_needing_ocr: Vec<usize>,
    #[serde(default)]
    pub ocr_reasons_by_page: Vec<OcrReasonEntry>,
    #[serde(default)]
    pub unusable_text_pages: Vec<usize>,
}

impl Default for Metadata {
    fn default() -> Self {
        Self {
            source_format: "unknown".to_string(),
            source_path: None,
            pdf_type: "unknown".to_string(),
            page_count: 0,
            confidence: 0.0,
            processing_time_ms: 0.0,
            title: None,
            is_complex_layout: false,
            has_encoding_issues: false,
            pages_with_tables: vec![],
            pages_with_columns: vec![],
            pages_needing_ocr: vec![],
            ocr_reasons_by_page: vec![],
            unusable_text_pages: vec![],
        }
    }
}

/// Complete document Intermediate Representation.
#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
pub struct Ir {
    pub ir_version: String,
    pub metadata: Metadata,
    pub pages: Vec<PageResult>,
}

impl Ir {
    pub fn new(metadata: Metadata, pages: Vec<PageResult>) -> Self {
        Self {
            ir_version: IR_VERSION.to_string(),
            metadata,
            pages,
        }
    }

    /// Streaming iterator over RAG hybrid chunks.
    pub fn iter_chunks<'a>(
        &'a self,
        config: &crate::renderers::ChunksConfig,
    ) -> crate::renderers::chunks::ChunksIterator<'a> {
        crate::renderers::iter_chunks(self, config)
    }

    /// Streaming iterator over zvec Doc records.
    pub fn iter_zvec_docs<'a>(
        &'a self,
        config: &crate::renderers::ZvecConfig,
    ) -> crate::renderers::zvec::ZvecDocsIterator<'a> {
        crate::renderers::iter_zvec_docs(self, config)
    }
}
