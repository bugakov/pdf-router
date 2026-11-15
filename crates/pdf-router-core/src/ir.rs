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
    DocxAdapter,
    DjvuAdapter,
    DjvuTextLayer,
    XlsxAdapter,
    DocAdapter,
    HtmlAdapter,
    Stub,
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

/// Document-level metadata.
#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
pub struct Metadata {
    pub source_format: String,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub source_path: Option<String>,
    pub pdf_type: String,
    pub page_count: usize,
    #[serde(default)]
    pub pages_with_tables: Vec<usize>,
    #[serde(default)]
    pub pages_needing_ocr: Vec<usize>,
    pub confidence: f64,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub title: Option<String>,
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
}
