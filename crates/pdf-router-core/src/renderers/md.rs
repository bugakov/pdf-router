//! Markdown renderer.

use crate::ir::Ir;

/// Render an IR to concatenated Markdown.
///
/// Pages are separated by double newlines (`\n\n`).
pub fn render_markdown(ir: &Ir) -> String {
    let mut parts = Vec::with_capacity(ir.pages.len());
    for page in &ir.pages {
        let trimmed = page.markdown.trim();
        if !trimmed.is_empty() {
            parts.push(trimmed);
        }
    }
    parts.join("\n\n")
}
