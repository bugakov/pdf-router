//! JSON renderer.

use crate::ir::Ir;

/// Render an IR to formatted JSON string.
pub fn render_json(ir: &Ir) -> Result<String, serde_json::Error> {
    serde_json::to_string_pretty(ir)
}
