//! CLI binary for pdf-router.

use anyhow::{Context, Result};
use clap::{Parser, ValueEnum};
use pdf_router_core::{
    process_document, render_chunks, render_json, render_markdown, render_zvec, ChunksConfig,
    ZvecConfig,
};
use std::path::PathBuf;

#[derive(Debug, Clone, Copy, ValueEnum)]
enum RenderTarget {
    Md,
    Json,
    Chunks,
    Zvec,
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
}

fn main() -> Result<()> {
    let args = Cli::parse();

    let ir = process_document(&args.path)
        .with_context(|| format!("Failed to process document: {}", args.path.display()))?;

    if args.info {
        println!("format:     {}", ir.metadata.source_format);
        println!("pdf_type:   {}", ir.metadata.pdf_type);
        println!("page_count: {}", ir.metadata.page_count);
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
        RenderTarget::Chunks => {
            let chunks = render_chunks(&ir, &ChunksConfig::default());
            print!("{}", chunks);
        }
        RenderTarget::Zvec => {
            let zvec = render_zvec(&ir, &ZvecConfig::default());
            print!("{}", zvec);
        }
    }

    Ok(())
}
