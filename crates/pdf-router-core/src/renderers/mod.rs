//! Output renderers (Markdown, JSON, RAG Chunks, zvec).

pub mod chunks;
pub mod json;
pub mod md;
pub mod zvec;

pub use chunks::{
    iter_chunks, render_chunks, ChunkEnvelope, ChunkMetadata, ChunksConfig, TableChunkMeta,
};
pub use json::render_json;
pub use md::render_markdown;
pub use zvec::{iter_zvec_docs, render_zvec, ZvecConfig, ZvecDocRecord, ZvecFields, ZvecMetadata};
