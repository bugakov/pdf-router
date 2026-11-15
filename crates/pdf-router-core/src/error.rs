//! Error types for pdf-router-core.

use thiserror::Error;

#[derive(Error, Debug)]
pub enum RouterError {
    #[error("IO error: {0}")]
    Io(#[from] std::io::Error),

    #[error("JSON serialization error: {0}")]
    Json(#[from] serde_json::Error),

    #[error("Unsupported format: {0}")]
    UnsupportedFormat(String),

    #[error("Processing error: {0}")]
    Processing(String),
}
