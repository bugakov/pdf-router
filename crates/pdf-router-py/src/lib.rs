use std::collections::HashMap;
use std::path::{Path, PathBuf};

use pyo3::prelude::*;
use pythonize::{depythonize, pythonize};

use pdf_router_core::ir::Ir;
use pdf_router_core::renderers::{
    iter_zvec_docs as core_iter_zvec_docs, render_chunks as core_render_chunks,
    render_json as core_render_json, render_markdown as core_render_markdown,
    render_zvec as core_render_zvec, ChunksConfig, ZvecConfig,
};
use pdf_router_core::{detect_format, process_document_with_options, ProcessOptions, RouterError};

pub use pdf_router_core::IR_VERSION;

#[pyfunction]
pub fn version() -> &'static str {
    env!("CARGO_PKG_VERSION")
}

fn router_err_to_pyerr(e: RouterError) -> PyErr {
    match e {
        RouterError::UnsupportedFormat(msg) => {
            PyErr::new::<pyo3::exceptions::PyValueError, _>(format!("unsupported format: {msg}"))
        }
        RouterError::Io(io_err) => PyErr::new::<pyo3::exceptions::PyIOError, _>(io_err.to_string()),
        other => PyErr::new::<pyo3::exceptions::PyRuntimeError, _>(other.to_string()),
    }
}

fn extract_ir(ir_obj: &Bound<'_, PyAny>) -> PyResult<Ir> {
    if let Ok(json_str) = ir_obj.extract::<String>() {
        serde_json::from_str(&json_str)
            .map_err(|e| PyErr::new::<pyo3::exceptions::PyValueError, _>(e.to_string()))
    } else {
        let dict_obj = if let Ok(to_dict_fn) = ir_obj.getattr("to_dict") {
            to_dict_fn.call0()?
        } else if let Ok(d) = ir_obj.getattr("__dict__") {
            d
        } else {
            ir_obj.clone()
        };
        depythonize(&dict_obj)
            .map_err(|e| PyErr::new::<pyo3::exceptions::PyValueError, _>(e.to_string()))
    }
}

#[pyfunction]
#[pyo3(name = "detect_format")]
pub fn py_detect_format(bytes: &[u8]) -> PyResult<String> {
    let fmt = detect_format(bytes);
    Ok(fmt.as_str().to_string())
}

#[pyfunction]
#[pyo3(signature = (pdf_path, *, strict = false, output_dir = None))]
pub fn convert(pdf_path: &str, strict: bool, output_dir: Option<&str>) -> PyResult<String> {
    let opts = ProcessOptions {
        strict,
        output_dir: output_dir.map(PathBuf::from),
        ..ProcessOptions::default()
    };
    let path = Path::new(pdf_path);
    let (ir, _report) = process_document_with_options(path, &opts).map_err(router_err_to_pyerr)?;
    Ok(core_render_markdown(&ir))
}

#[pyfunction]
#[pyo3(signature = (pdf_path, *, output_dir = None, strict = false))]
pub fn process_pdf<'py>(
    py: Python<'py>,
    pdf_path: &str,
    output_dir: Option<&str>,
    strict: bool,
) -> PyResult<(Bound<'py, PyAny>, Bound<'py, PyAny>)> {
    let opts = ProcessOptions {
        strict,
        output_dir: output_dir.map(PathBuf::from),
        ..ProcessOptions::default()
    };
    let path = Path::new(pdf_path);
    let (ir, report) = process_document_with_options(path, &opts).map_err(router_err_to_pyerr)?;

    let py_ir = pythonize(py, &ir)
        .map_err(|e| PyErr::new::<pyo3::exceptions::PyValueError, _>(e.to_string()))?;
    let py_report = pythonize(py, &report)
        .map_err(|e| PyErr::new::<pyo3::exceptions::PyValueError, _>(e.to_string()))?;

    Ok((py_ir, py_report))
}

#[pyfunction]
#[pyo3(signature = (ir_obj, out_dir, stem))]
pub fn write_outputs(
    _py: Python<'_>,
    ir_obj: Bound<'_, PyAny>,
    out_dir: &str,
    stem: &str,
) -> PyResult<HashMap<String, String>> {
    let ir = extract_ir(&ir_obj)?;
    let out_path = Path::new(out_dir);
    std::fs::create_dir_all(out_path)?;

    let mut written = HashMap::new();

    let md_path = out_path.join(format!("{stem}.md"));
    let md_content = core_render_markdown(&ir);
    std::fs::write(&md_path, md_content)?;
    written.insert("md".to_string(), md_path.to_string_lossy().to_string());

    let chunks_path = out_path.join(format!("{stem}.chunks.jsonl"));
    let chunks_content = core_render_chunks(&ir, &ChunksConfig::default());
    std::fs::write(&chunks_path, chunks_content)?;
    written.insert(
        "chunks".to_string(),
        chunks_path.to_string_lossy().to_string(),
    );

    let json_path = out_path.join(format!("{stem}.ir.json"));
    let json_content = core_render_json(&ir)
        .map_err(|e| PyErr::new::<pyo3::exceptions::PyValueError, _>(e.to_string()))?;
    std::fs::write(&json_path, json_content)?;
    written.insert("json".to_string(), json_path.to_string_lossy().to_string());

    Ok(written)
}

#[pyfunction]
#[pyo3(signature = (path = None))]
pub fn load_config<'py>(py: Python<'py>, path: Option<&str>) -> PyResult<Bound<'py, PyAny>> {
    let toml_val: toml::Value = if let Some(p) = path {
        let text = std::fs::read_to_string(p)
            .map_err(|e| PyErr::new::<pyo3::exceptions::PyFileNotFoundError, _>(e.to_string()))?;
        toml::from_str(&text)
            .map_err(|e| PyErr::new::<pyo3::exceptions::PyValueError, _>(e.to_string()))?
    } else {
        toml::Value::Table(toml::map::Map::new())
    };
    pythonize(py, &toml_val)
        .map_err(|e| PyErr::new::<pyo3::exceptions::PyValueError, _>(e.to_string()))
}

#[pyfunction]
#[pyo3(signature = (target, *, doc_uid = None))]
pub fn iter_zvec_docs<'py>(
    py: Python<'py>,
    target: Bound<'py, PyAny>,
    doc_uid: Option<&str>,
) -> PyResult<Vec<Bound<'py, PyAny>>> {
    let cfg = doc_uid.map(|uid| ZvecConfig {
        doc_uid: Some(uid.to_string()),
        ..ZvecConfig::default()
    });
    let config = cfg.unwrap_or_default();

    let ir = if let Ok(path_str) = target.extract::<String>() {
        let path = Path::new(&path_str);
        let opts = ProcessOptions::default();
        let (ir, _) = process_document_with_options(path, &opts).map_err(router_err_to_pyerr)?;
        ir
    } else {
        extract_ir(&target)?
    };

    let records: Vec<_> = core_iter_zvec_docs(&ir, &config).collect();
    let mut py_records = Vec::with_capacity(records.len());
    for rec in records {
        py_records.push(
            pythonize(py, &rec)
                .map_err(|e| PyErr::new::<pyo3::exceptions::PyValueError, _>(e.to_string()))?,
        );
    }
    Ok(py_records)
}

#[pyfunction]
#[pyo3(signature = (target, *, doc_uid = None))]
pub fn iter_zvec_records<'py>(
    py: Python<'py>,
    target: Bound<'py, PyAny>,
    doc_uid: Option<&str>,
) -> PyResult<Vec<Bound<'py, PyAny>>> {
    iter_zvec_docs(py, target, doc_uid)
}

#[pyfunction]
pub fn render_markdown(_py: Python<'_>, ir_obj: Bound<'_, PyAny>) -> PyResult<String> {
    let ir = extract_ir(&ir_obj)?;
    Ok(core_render_markdown(&ir))
}

#[pyfunction]
pub fn render_chunks(_py: Python<'_>, ir_obj: Bound<'_, PyAny>) -> PyResult<String> {
    let ir = extract_ir(&ir_obj)?;
    Ok(core_render_chunks(&ir, &ChunksConfig::default()))
}

#[pyfunction]
#[pyo3(signature = (ir_obj, *, doc_uid = None))]
pub fn render_zvec(
    _py: Python<'_>,
    ir_obj: Bound<'_, PyAny>,
    doc_uid: Option<&str>,
) -> PyResult<String> {
    let ir = extract_ir(&ir_obj)?;
    let cfg = doc_uid.map(|uid| ZvecConfig {
        doc_uid: Some(uid.to_string()),
        ..ZvecConfig::default()
    });
    Ok(core_render_zvec(&ir, &cfg.unwrap_or_default()))
}

#[pyfunction]
pub fn render_json(_py: Python<'_>, ir_obj: Bound<'_, PyAny>) -> PyResult<String> {
    let ir = extract_ir(&ir_obj)?;
    core_render_json(&ir)
        .map_err(|e| PyErr::new::<pyo3::exceptions::PyValueError, _>(e.to_string()))
}

#[pymodule]
pub fn pdf_router_native(m: &Bound<'_, PyModule>) -> PyResult<()> {
    m.add_function(wrap_pyfunction!(version, m)?)?;
    m.add_function(wrap_pyfunction!(py_detect_format, m)?)?;
    m.add_function(wrap_pyfunction!(convert, m)?)?;
    m.add_function(wrap_pyfunction!(process_pdf, m)?)?;
    m.add_function(wrap_pyfunction!(write_outputs, m)?)?;
    m.add_function(wrap_pyfunction!(load_config, m)?)?;
    m.add_function(wrap_pyfunction!(iter_zvec_docs, m)?)?;
    m.add_function(wrap_pyfunction!(iter_zvec_records, m)?)?;
    m.add_function(wrap_pyfunction!(render_markdown, m)?)?;
    m.add_function(wrap_pyfunction!(render_chunks, m)?)?;
    m.add_function(wrap_pyfunction!(render_zvec, m)?)?;
    m.add_function(wrap_pyfunction!(render_json, m)?)?;
    Ok(())
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn test_version_string() {
        assert_eq!(version(), env!("CARGO_PKG_VERSION"));
    }

    #[test]
    fn test_detect_format_native() {
        assert_eq!(py_detect_format(b"%PDF-1.7").unwrap(), "pdf");
        assert_eq!(py_detect_format(b"AT&TFORM\0\0\0\x10DJVU").unwrap(), "djvu");
        assert_eq!(py_detect_format(b"random bytes").unwrap(), "unknown");
    }

    #[test]
    fn test_pyo3_runtime_bindings() {
        Python::with_gil(|py| {
            let m = PyModule::new_bound(py, "pdf_router_native").unwrap();
            pdf_router_native(&m).unwrap();
            assert!(m.hasattr("version").unwrap());
            assert!(m.hasattr("detect_format").unwrap());
            assert!(m.hasattr("convert").unwrap());
            assert!(m.hasattr("process_pdf").unwrap());
            assert!(m.hasattr("write_outputs").unwrap());
            assert!(m.hasattr("load_config").unwrap());
            assert!(m.hasattr("iter_zvec_docs").unwrap());
            assert!(m.hasattr("iter_zvec_records").unwrap());
            assert!(m.hasattr("render_markdown").unwrap());
            assert!(m.hasattr("render_chunks").unwrap());
            assert!(m.hasattr("render_zvec").unwrap());
            assert!(m.hasattr("render_json").unwrap());
        });
    }
}
