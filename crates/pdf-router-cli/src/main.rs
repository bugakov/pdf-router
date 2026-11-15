//! CLI binary for pdf-router.

use anyhow::{Context, Result};
use clap::{Parser, ValueEnum};
use pdf_router_core::{
    process_document_with_options, render_json, render_markdown, MockOcrEngine, OcrEngine,
    ProcessOptions, TesseractEngine,
};
use std::path::PathBuf;
use std::sync::Arc;

#[derive(Debug, Clone, Copy, ValueEnum)]
enum RenderTarget {
    Md,
    Json,
}

#[derive(Parser, Debug)]
#[command(
    name = "pdf-router",
    about = "Ultra-minimal, deterministic, deploy-anywhere document router",
    version
)]
struct Cli {
    /// Path to document (PDF, DOCX, XLSX, DOC, DJVU, HTML)
    #[arg(value_name = "FILE")]
    path: PathBuf,

    /// Output rendering target
    #[arg(short, long, value_enum, default_value_t = RenderTarget::Md)]
    render: RenderTarget,

    /// Show metadata summary only without rendering
    #[arg(long)]
    info: bool,

    /// Abort with error if any page fails processing
    #[arg(long)]
    strict: bool,

    /// OCR engine to invoke (e.g. tesseract, mock)
    #[arg(long)]
    ocr: Option<String>,

    /// DPI for page rasterization
    #[arg(long, default_value_t = 200)]
    ocr_dpi: u32,

    /// Languages for OCR engine (comma-separated, e.g. "eng" or "eng,rus")
    #[arg(long, default_value = "eng")]
    ocr_lang: String,
}

fn main() -> Result<()> {
    let args = Cli::parse();

    let ocr_engine: Option<Arc<dyn OcrEngine>> = match args.ocr.as_deref() {
        Some("tesseract") => {
            let langs: Vec<String> = args
                .ocr_lang
                .split(',')
                .map(|s| s.trim().to_string())
                .filter(|s| !s.is_empty())
                .collect();
            Some(Arc::new(TesseractEngine::new().with_languages(langs)))
        }
        Some("mock") => Some(Arc::new(MockOcrEngine::default())),
        Some(other) => anyhow::bail!("Unsupported OCR engine: {}", other),
        None => None,
    };

    let options = ProcessOptions {
        strict: args.strict,
        ocr_engine,
        ocr_dpi: Some(args.ocr_dpi),
    };

    let (ir, report) = process_document_with_options(&args.path, &options)
        .with_context(|| format!("Failed to process document: {}", args.path.display()))?;

    if args.info {
        println!("format:       {}", ir.metadata.source_format);
        println!("pdf_type:     {}", ir.metadata.pdf_type);
        println!("page_count:   {}", ir.metadata.page_count);
        println!("pages_native: {}", report.pages_native);
        println!("pages_ocr:    {}", report.pages_ocr);
        println!("pages_failed: {}", report.pages_failed);
        return Ok(());
    }

    match args.render {
        RenderTarget::Md => {
            let md = render_markdown(&ir);
            print!("{}", md);
            if !md.ends_with('\n') {
                println!();
            }
        }
        RenderTarget::Json => {
            let json = render_json(&ir)?;
            println!("{}", json);
        }
    }

    Ok(())
}
