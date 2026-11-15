// pdf-router-py stub for Ticket 01
pub use pdf_router_core::IR_VERSION;

pub fn version() -> &'static str {
    env!("CARGO_PKG_VERSION")
}
