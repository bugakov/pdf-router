//! DJVU adapter invoking djvulibre CLI tools (`djvused`, `djvudump`, `ddjvu`).

use std::io::Write;
use std::path::{Path, PathBuf};
use std::process::{Command, Output};
use std::sync::mpsc;
use std::thread;
use std::time::Duration;

use crate::error::RouterError;
use crate::ir::{Ir, Metadata, OcrReasonEntry, PageResult, SrcTool};
use crate::ocr::PageRaster;

const TOOL_TIMEOUT_S: f64 = 300.0;

/// Run an external command with timeout and error wrapping.
fn run_cmd(mut cmd: Command, timeout_s: f64) -> Result<Output, RouterError> {
    let program = cmd.get_program().to_string_lossy().into_owned();
    cmd.stdin(std::process::Stdio::null());
    cmd.stdout(std::process::Stdio::piped());
    cmd.stderr(std::process::Stdio::piped());

    let child = cmd.spawn().map_err(|e| {
        if e.kind() == std::io::ErrorKind::NotFound {
            RouterError::Processing(format!("tool not found: {program} (install djvulibre-bin)"))
        } else {
            RouterError::Io(e)
        }
    })?;

    let child_pid = child.id();
    let (tx, rx) = mpsc::channel();
    thread::spawn(move || {
        let out = child.wait_with_output();
        let _ = tx.send(out);
    });

    let output = match rx.recv_timeout(Duration::from_secs_f64(timeout_s)) {
        Ok(res) => res.map_err(RouterError::Io)?,
        Err(mpsc::RecvTimeoutError::Timeout) => {
            let _ = Command::new("kill")
                .args(["-9", &child_pid.to_string()])
                .output();
            return Err(RouterError::Processing(format!(
                "{program} timed out after {timeout_s}s (corrupt document?)"
            )));
        }
        Err(mpsc::RecvTimeoutError::Disconnected) => {
            return Err(RouterError::Processing(format!(
                "{program} process thread terminated unexpectedly"
            )));
        }
    };

    if !output.status.success() {
        let stderr = String::from_utf8_lossy(&output.stderr);
        let short_err = if stderr.len() > 300 {
            &stderr[..300]
        } else {
            &stderr
        };
        return Err(RouterError::Processing(format!(
            "{program} exited with code {:?}: {}",
            output.status.code(),
            short_err
        )));
    }

    Ok(output)
}

/// Page count via `djvused -u <path> -e n`.
pub fn djvu_page_count(path: &Path) -> Result<usize, RouterError> {
    let mut cmd = Command::new("djvused");
    cmd.arg("-u").arg(path).arg("-e").arg("n");
    let output = run_cmd(cmd, TOOL_TIMEOUT_S)?;
    let text = String::from_utf8_lossy(&output.stdout).trim().to_string();
    text.parse::<usize>().map_err(|e| {
        RouterError::Processing(format!(
            "djvused -e n returned invalid page count {:?} for {}: {}",
            text,
            path.display(),
            e
        ))
    })
}

/// Per-page stored DPI parsed from `djvudump <path>`.
pub fn djvu_stored_dpis(path: &Path) -> Vec<u32> {
    let mut cmd = Command::new("djvudump");
    cmd.arg(path);
    let Ok(output) = run_cmd(cmd, 30.0) else {
        return vec![];
    };
    let text = String::from_utf8_lossy(&output.stdout);
    let mut dpis = Vec::new();
    for line in text.lines() {
        if line.contains("FORM:DJVU") && !line.contains("FORM:DJVI") {
            dpis.push(300); // default placeholder
        } else if line.contains("INFO") {
            if let Some(pos) = line.find("dpi") {
                let prefix = line[..pos].trim_end();
                if let Some(start) = prefix.rfind(|c: char| !c.is_ascii_digit()) {
                    if let Ok(dpi) = prefix[start + 1..].parse::<u32>() {
                        if let Some(last) = dpis.last_mut() {
                            *last = dpi;
                        }
                    }
                }
            }
        }
    }
    dpis
}

/// Clean DJVU control characters (\014 \013 \035 \037 -> \n).
pub fn clean_djvu_text(raw: &str) -> String {
    let mut cleaned = String::with_capacity(raw.len());
    for ch in raw.chars() {
        match ch {
            '\x0C' | '\x0B' | '\x1D' | '\x1F' => cleaned.push('\n'),
            _ => cleaned.push(ch),
        }
    }
    cleaned.trim().to_string()
}

/// Extract hidden text layer per page (0-indexed list).
pub fn djvu_page_texts(path: &Path, count: usize) -> Result<Vec<String>, RouterError> {
    if count == 0 {
        return Ok(vec![]);
    }

    // Fast path: probe whole document
    let mut cmd = Command::new("djvused");
    cmd.arg("-u").arg(path).arg("-e").arg("print-pure-txt");
    let whole = match run_cmd(cmd, TOOL_TIMEOUT_S) {
        Ok(output) => clean_djvu_text(&String::from_utf8_lossy(&output.stdout)),
        Err(_) => String::new(),
    };

    if whole.is_empty() {
        return Ok(vec![String::new(); count]);
    }
    if count == 1 {
        return Ok(vec![whole]);
    }

    // Per-page extraction
    let mut texts = Vec::with_capacity(count);
    for i in 1..=count {
        let mut cmd = Command::new("djvused");
        let expr = format!("select {i}; print-pure-txt");
        cmd.arg("-u").arg(path).arg("-e").arg(&expr);
        match run_cmd(cmd, 30.0) {
            Ok(output) => {
                let cleaned = clean_djvu_text(&String::from_utf8_lossy(&output.stdout));
                texts.push(cleaned);
            }
            Err(_) => {
                texts.push(String::new());
            }
        }
    }
    Ok(texts)
}

/// Parse PPM (P6) header from binary buffer.
/// Returns `(width, height, maxval, body_offset)`.
pub fn parse_ppm_header(data: &[u8]) -> Result<(u32, u32, u32, usize), RouterError> {
    if data.len() < 3 || &data[..2] != b"P6" {
        return Err(RouterError::Processing(
            "ddjvu: invalid PPM magic (expected P6)".to_string(),
        ));
    }

    let mut pos = 2;
    let mut fields = Vec::new();

    while fields.len() < 3 {
        // Skip whitespace and comments
        while pos < data.len() && data[pos].is_ascii_whitespace() {
            pos += 1;
        }
        if pos < data.len() && data[pos] == b'#' {
            while pos < data.len() && data[pos] != b'\n' && data[pos] != b'\r' {
                pos += 1;
            }
            continue;
        }
        let start = pos;
        while pos < data.len() && data[pos].is_ascii_digit() {
            pos += 1;
        }
        if start == pos {
            return Err(RouterError::Processing("malformed PPM header".to_string()));
        }
        let num_str = std::str::from_utf8(&data[start..pos])
            .map_err(|e| RouterError::Processing(format!("invalid UTF-8 in PPM header: {e}")))?;
        let val: u32 = num_str
            .parse()
            .map_err(|e| RouterError::Processing(format!("invalid integer in PPM header: {e}")))?;
        fields.push(val);
    }

    // Exactly one whitespace byte separates header from binary samples
    if pos < data.len() && data[pos].is_ascii_whitespace() {
        pos += 1;
    } else {
        return Err(RouterError::Processing(
            "missing PPM header separator byte".to_string(),
        ));
    }

    let (w, h, maxval) = (fields[0], fields[1], fields[2]);
    if maxval != 255 {
        return Err(RouterError::Processing(format!(
            "unsupported PPM maxval {maxval} (expected 255)"
        )));
    }

    let body_len = (w as usize)
        .checked_mul(h as usize)
        .and_then(|wh| wh.checked_mul(3))
        .ok_or_else(|| RouterError::Processing("PPM dimensions overflow".to_string()))?;

    if data.len() < pos + body_len {
        return Err(RouterError::Processing(format!(
            "truncated PPM body: expected {} bytes, got {}",
            body_len,
            data.len() - pos
        )));
    }

    Ok((w, h, maxval, pos))
}

/// Render a DJVU page to PPM `PageRaster` using `ddjvu`.
pub fn render_page_ppm_djvu(
    path: &Path,
    page_idx: usize,
    dpi: u32,
) -> Result<PageRaster, RouterError> {
    let n = djvu_page_count(path)?;
    if page_idx >= n {
        return Err(RouterError::Processing(format!(
            "page index {page_idx} out of range (0..{n})"
        )));
    }

    let dpis = djvu_stored_dpis(path);
    let scale = if page_idx < dpis.len() {
        dpi.min(dpis[page_idx])
    } else {
        dpi
    };

    let mut cmd = Command::new("ddjvu");
    cmd.args([
        "-format=ppm",
        &format!("-page={}", page_idx + 1),
        &format!("-scale={scale}"),
    ]);
    cmd.arg(path).arg("-");

    let output = run_cmd(cmd, TOOL_TIMEOUT_S)?;
    let (w, h, _maxval, offset) = parse_ppm_header(&output.stdout)?;
    let body = output.stdout[offset..offset + (w as usize) * (h as usize) * 3].to_vec();

    Ok(PageRaster::new(page_idx, w, h, scale, body))
}

/// Process a DJVU document into an IR.
pub fn process_djvu(bytes: &[u8], path: Option<&str>) -> Result<Ir, RouterError> {
    let mut _tmp = None;
    let file_path: PathBuf = match path {
        Some(p) if Path::new(p).is_file() => PathBuf::from(p),
        _ => {
            let mut tmp_file = tempfile::Builder::new().suffix(".djvu").tempfile()?;
            tmp_file.write_all(bytes)?;
            let p = tmp_file.path().to_path_buf();
            _tmp = Some(tmp_file);
            p
        }
    };

    let n = djvu_page_count(&file_path)?;
    if n == 0 {
        return Err(RouterError::InvalidDocument(
            "DJVU document has no pages".to_string(),
        ));
    }

    let texts = djvu_page_texts(&file_path, n)?;
    let mut pages = Vec::with_capacity(n);
    let mut pages_needing_ocr = Vec::new();

    for (i, text) in texts.into_iter().enumerate() {
        let has_layer = !text.trim().is_empty();
        let one_indexed = i + 1;
        if !has_layer {
            pages_needing_ocr.push(one_indexed);
        }

        pages.push(PageResult {
            page: i,
            markdown: text,
            src_tool: if has_layer {
                SrcTool::DjvuTextLayer
            } else {
                SrcTool::DjvuAdapter
            },
            blocks: vec![],
            images: vec![],
            error: None,
            needs_ocr: !has_layer,
        });
    }

    let ocr_reasons_by_page = pages_needing_ocr
        .iter()
        .map(|&p| OcrReasonEntry {
            page: p,
            reasons: vec!["inspector_needs_ocr".to_string()],
        })
        .collect();

    let metadata = Metadata {
        source_format: "djvu".to_string(),
        source_path: path.map(|s| s.to_string()),
        pdf_type: "scanned".to_string(),
        page_count: n,
        confidence: 1.0,
        processing_time_ms: 0.0,
        title: None,
        is_complex_layout: false,
        has_encoding_issues: false,
        pages_with_tables: vec![],
        pages_with_columns: vec![],
        pages_needing_ocr,
        ocr_reasons_by_page,
        unusable_text_pages: vec![],
    };

    Ok(Ir::new(metadata, pages))
}
