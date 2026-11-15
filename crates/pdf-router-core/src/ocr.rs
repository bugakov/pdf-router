//! Pluggable OCR Engine trait and CLI adapters.

use std::process::Command;
use std::sync::mpsc;
use std::thread;
use std::time::Duration;

use serde::{Deserialize, Serialize};
use thiserror::Error;

use crate::ir::Block;

/// In-memory page raster passed to OCR engines.
#[derive(Debug, Clone, PartialEq)]
pub struct PageRaster {
    pub page_index: usize,
    pub width: u32,
    pub height: u32,
    pub dpi: u32,
    pub data: Vec<u8>,
}

impl PageRaster {
    pub fn new(page_index: usize, width: u32, height: u32, dpi: u32, data: Vec<u8>) -> Self {
        Self {
            page_index,
            width,
            height,
            dpi,
            data,
        }
    }

    /// Returns binary PPM (P6) bytes.
    /// If `data` is already a valid PPM stream (starts with b"P6"), returns `data`.
    /// Otherwise wraps raw RGB bytes in a P6 PPM header.
    pub fn as_ppm_bytes(&self) -> Vec<u8> {
        if self.data.starts_with(b"P6") {
            self.data.clone()
        } else {
            let mut header = format!("P6\n{} {}\n255\n", self.width, self.height).into_bytes();
            header.extend_from_slice(&self.data);
            header
        }
    }
}

/// Extracted OCR result from an OCR engine.
#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
pub struct OcrResult {
    pub text: String,
    pub blocks: Vec<Block>,
    pub confidence: f64,
}

impl OcrResult {
    pub fn new(text: impl Into<String>) -> Self {
        Self {
            text: text.into(),
            blocks: vec![],
            confidence: 1.0,
        }
    }

    pub fn with_blocks(mut self, blocks: Vec<Block>) -> Self {
        self.blocks = blocks;
        self
    }

    pub fn with_confidence(mut self, confidence: f64) -> Self {
        self.confidence = confidence;
        self
    }
}

/// OCR error variants.
#[derive(Error, Debug)]
pub enum OcrError {
    #[error("OCR engine error: {0}")]
    Engine(String),

    #[error("OCR timed out after {0}s")]
    Timeout(f64),

    #[error("IO error: {0}")]
    Io(#[from] std::io::Error),

    #[error("Empty OCR result for page {0}")]
    Empty(usize),
}

/// Pluggable OCR engine trait.
pub trait OcrEngine: Send + Sync {
    /// Identifier of the engine, e.g. "tesseract", "rapidocr", "mock".
    fn name(&self) -> &'static str;

    /// Perform OCR on a single page raster.
    fn ocr_page(&self, raster: PageRaster) -> Result<OcrResult, OcrError>;
}

/// Local Tesseract CLI adapter.
#[derive(Debug, Clone)]
pub struct TesseractEngine {
    pub bin: String,
    pub languages: Vec<String>,
    pub timeout_s: f64,
}

impl Default for TesseractEngine {
    fn default() -> Self {
        Self {
            bin: "tesseract".to_string(),
            languages: vec!["eng".to_string()],
            timeout_s: 300.0,
        }
    }
}

impl TesseractEngine {
    pub fn new() -> Self {
        Self::default()
    }

    pub fn with_bin(mut self, bin: impl Into<String>) -> Self {
        self.bin = bin.into();
        self
    }

    pub fn with_language(mut self, lang: impl Into<String>) -> Self {
        self.languages = vec![lang.into()];
        self
    }

    pub fn with_languages(mut self, langs: Vec<String>) -> Self {
        self.languages = langs;
        self
    }

    pub fn with_timeout(mut self, timeout_s: f64) -> Self {
        self.timeout_s = timeout_s;
        self
    }
}

impl OcrEngine for TesseractEngine {
    fn name(&self) -> &'static str {
        "tesseract"
    }

    fn ocr_page(&self, raster: PageRaster) -> Result<OcrResult, OcrError> {
        let lang_str = if self.languages.is_empty() {
            "eng".to_string()
        } else {
            self.languages.join("+")
        };
        let dpi_str = raster.dpi.max(70).to_string();

        let mut cmd = Command::new(&self.bin);
        cmd.args(["stdin", "stdout", "-l", &lang_str, "--dpi", &dpi_str]);
        cmd.stdin(std::process::Stdio::piped());
        cmd.stdout(std::process::Stdio::piped());
        cmd.stderr(std::process::Stdio::piped());

        let mut child = cmd.spawn().map_err(|e| {
            if e.kind() == std::io::ErrorKind::NotFound {
                OcrError::Engine(format!("tesseract binary not found: {}", self.bin))
            } else {
                OcrError::Io(e)
            }
        })?;

        let ppm = raster.as_ppm_bytes();
        let page_idx = raster.page_index;
        let timeout_dur = Duration::from_secs_f64(self.timeout_s);
        let child_pid = child.id();

        let (tx, rx) = mpsc::channel();
        thread::spawn(move || {
            if let Some(mut stdin) = child.stdin.take() {
                use std::io::Write;
                let _ = stdin.write_all(&ppm);
                drop(stdin);
            }
            let out = child.wait_with_output();
            let _ = tx.send(out);
        });

        let output = match rx.recv_timeout(timeout_dur) {
            Ok(res) => res?,
            Err(mpsc::RecvTimeoutError::Timeout) => {
                let _ = Command::new("kill")
                    .args(["-9", &child_pid.to_string()])
                    .output();
                return Err(OcrError::Timeout(self.timeout_s));
            }
            Err(mpsc::RecvTimeoutError::Disconnected) => {
                return Err(OcrError::Engine(
                    "OCR process thread terminated unexpectedly".to_string(),
                ));
            }
        };

        if !output.status.success() {
            let stderr = String::from_utf8_lossy(&output.stderr);
            let short_err = if stderr.len() > 300 {
                &stderr[..300]
            } else {
                &stderr
            };
            return Err(OcrError::Engine(format!(
                "tesseract exited with code {:?}: {}",
                output.status.code(),
                short_err
            )));
        }

        let text = String::from_utf8_lossy(&output.stdout).trim().to_string();
        if text.is_empty() {
            return Err(OcrError::Empty(page_idx));
        }

        let tables = crate::adapters::pdf::extract_tables_from_markdown(&text);
        let blocks = tables.into_iter().map(Block::Table).collect();

        Ok(OcrResult {
            text,
            blocks,
            confidence: 0.95,
        })
    }
}

/// Mock OCR engine for testing deterministic and failure scenarios.
#[derive(Debug, Clone)]
pub struct MockOcrEngine {
    pub default_text: String,
    pub failing_pages: std::collections::HashSet<usize>,
}

impl MockOcrEngine {
    pub fn new(default_text: impl Into<String>) -> Self {
        Self {
            default_text: default_text.into(),
            failing_pages: std::collections::HashSet::new(),
        }
    }

    pub fn with_failing_page(mut self, page: usize) -> Self {
        self.failing_pages.insert(page);
        self
    }
}

impl Default for MockOcrEngine {
    fn default() -> Self {
        Self {
            default_text: "Mock OCR text".to_string(),
            failing_pages: std::collections::HashSet::new(),
        }
    }
}

impl OcrEngine for MockOcrEngine {
    fn name(&self) -> &'static str {
        "mock"
    }

    fn ocr_page(&self, raster: PageRaster) -> Result<OcrResult, OcrError> {
        if self.failing_pages.contains(&raster.page_index) {
            return Err(OcrError::Engine(format!(
                "Mock OCR failure for page {}",
                raster.page_index
            )));
        }

        let tables = crate::adapters::pdf::extract_tables_from_markdown(&self.default_text);
        let blocks = tables.into_iter().map(Block::Table).collect();

        Ok(OcrResult {
            text: self.default_text.clone(),
            blocks,
            confidence: 1.0,
        })
    }
}
