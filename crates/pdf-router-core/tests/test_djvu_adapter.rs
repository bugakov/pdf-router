use std::path::PathBuf;
use std::sync::Arc;

fn fixture_djvu_path(rel: &str) -> PathBuf {
    let p = PathBuf::from(rel);
    if p.exists() {
        return p;
    }
    let manifest = PathBuf::from(env!("CARGO_MANIFEST_DIR"));
    let from_manifest = manifest.join("../../").join(rel);
    if from_manifest.exists() {
        return from_manifest;
    }
    p
}

use pdf_router_core::adapters::djvu::{
    clean_djvu_text, djvu_page_count, djvu_page_texts, parse_ppm_header, process_djvu,
};
use pdf_router_core::error::RouterError;
use pdf_router_core::formats::{detect_format, detect_format_path, Format};
use pdf_router_core::ir::SrcTool;
use pdf_router_core::ocr::MockOcrEngine;
use pdf_router_core::{process_document_with_options, ProcessOptions};

#[test]
fn test_ppm_header_parsing_valid() {
    let header = b"P6\n10 20\n255\n";
    let mut data = header.to_vec();
    data.resize(header.len() + 10 * 20 * 3, 128);

    let (w, h, maxval, offset) = parse_ppm_header(&data).expect("valid PPM header");
    assert_eq!(w, 10);
    assert_eq!(h, 20);
    assert_eq!(maxval, 255);
    assert_eq!(offset, header.len());
}

#[test]
fn test_ppm_header_parsing_with_comments() {
    let header = b"P6\n# Creator: ddjvu\n10 # width\n20 # height\n255\n";
    let mut data = header.to_vec();
    data.resize(header.len() + 10 * 20 * 3, 200);

    let (w, h, maxval, offset) = parse_ppm_header(&data).expect("valid PPM header with comments");
    assert_eq!(w, 10);
    assert_eq!(h, 20);
    assert_eq!(maxval, 255);
    assert_eq!(offset, header.len());
}

#[test]
fn test_ppm_header_parsing_errors() {
    // 1. Wrong magic
    let bad_magic = b"P5\n10 20\n255\n";
    assert!(parse_ppm_header(bad_magic).is_err());

    // 2. Unsupported maxval
    let bad_maxval = b"P6\n10 20\n65535\n";
    assert!(parse_ppm_header(bad_maxval).is_err());

    // 3. Truncated body
    let truncated = b"P6\n10 20\n255\nraw";
    assert!(parse_ppm_header(truncated).is_err());
}

#[test]
fn test_clean_djvu_control_characters() {
    let raw = "Hello\x0CWorld\x0BColumn\x1DRegion\x1FParagraph";
    let cleaned = clean_djvu_text(raw);
    assert_eq!(cleaned, "Hello\nWorld\nColumn\nRegion\nParagraph");
}

#[test]
fn test_djvu_format_detection() {
    let plain_path = fixture_djvu_path("tests/fixtures/djvu/plain.djvu");
    let text_layer_path = fixture_djvu_path("tests/fixtures/djvu/text_layer.djvu");

    assert!(plain_path.exists(), "plain.djvu fixture must exist");
    assert!(
        text_layer_path.exists(),
        "text_layer.djvu fixture must exist"
    );

    let plain_bytes = std::fs::read(&plain_path).unwrap();
    assert_eq!(detect_format(&plain_bytes), Format::Djvu);
    assert_eq!(detect_format_path(&plain_path).unwrap(), Format::Djvu);

    let text_layer_bytes = std::fs::read(&text_layer_path).unwrap();
    assert_eq!(detect_format(&text_layer_bytes), Format::Djvu);
    assert_eq!(detect_format_path(&text_layer_path).unwrap(), Format::Djvu);
}

#[test]
fn test_djvu_integration_with_tools() {
    let check = std::process::Command::new("djvused").arg("-v").output();
    if check.is_err() {
        eprintln!("djvused not found, skipping live djvulibre integration test");
        return;
    }

    let text_layer_path = fixture_djvu_path("tests/fixtures/djvu/text_layer.djvu");
    let count = djvu_page_count(&text_layer_path).expect("djvu_page_count failed");
    assert_eq!(count, 1);

    let texts = djvu_page_texts(&text_layer_path, count).expect("djvu_page_texts failed");
    assert_eq!(texts.len(), 1);
    assert!(!texts[0].trim().is_empty());

    // Test text_layer.djvu full processing
    let text_layer_bytes = std::fs::read(&text_layer_path).unwrap();
    let ir = process_djvu(&text_layer_bytes, Some(text_layer_path.to_str().unwrap()))
        .expect("process_djvu text layer failed");
    assert_eq!(ir.metadata.source_format, "djvu");
    assert_eq!(ir.pages.len(), 1);
    assert_eq!(ir.pages[0].src_tool, SrcTool::DjvuTextLayer);
    assert!(!ir.pages[0].needs_ocr);
    assert!(!ir.pages[0].markdown.trim().is_empty());

    // Test plain.djvu (scanned layerless)
    let plain_path = fixture_djvu_path("tests/fixtures/djvu/plain.djvu");
    let plain_bytes = std::fs::read(&plain_path).unwrap();
    let plain_ir = process_djvu(&plain_bytes, Some(plain_path.to_str().unwrap()))
        .expect("process_djvu plain failed");
    assert_eq!(plain_ir.pages.len(), 1);
    assert_eq!(plain_ir.pages[0].src_tool, SrcTool::DjvuAdapter);
    assert!(plain_ir.pages[0].needs_ocr);

    // Test OCR pass on plain.djvu with MockOcrEngine
    let mock = Arc::new(MockOcrEngine::new("Extracted OCR content"));
    let opts = ProcessOptions {
        strict: true,
        ocr_engine: Some(mock.clone()),
        ocr_dpi: Some(150),
        ..ProcessOptions::default()
    };

    let (ocr_ir, report) = process_document_with_options(&plain_path, &opts)
        .expect("OCR pass with mock engine must succeed");
    assert_eq!(ocr_ir.pages[0].src_tool, SrcTool::MockOcr);
    assert_eq!(ocr_ir.pages[0].markdown, "Extracted OCR content");
    assert!(!ocr_ir.pages[0].needs_ocr);
    assert_eq!(report.pages_ocr, 1);
    assert_eq!(report.pages_failed, 0);

    // Test failing OCR on plain.djvu in strict mode
    let failing_mock = Arc::new(MockOcrEngine::new("Extracted").with_failing_page(0));
    let strict_failing_opts = ProcessOptions {
        strict: true,
        ocr_engine: Some(failing_mock.clone()),
        ocr_dpi: Some(150),
        ..ProcessOptions::default()
    };

    let err = process_document_with_options(&plain_path, &strict_failing_opts)
        .expect_err("strict mode must fail on failed page OCR");
    match err {
        RouterError::Processing(msg) => {
            assert!(msg.contains("strict mode: 1 of 1 pages failed"));
        }
        other => panic!("Unexpected error: {:?}", other),
    }

    // Test failing OCR on plain.djvu in non-strict mode
    let non_strict_failing_opts = ProcessOptions {
        strict: false,
        ocr_engine: Some(failing_mock),
        ocr_dpi: Some(150),
        ..ProcessOptions::default()
    };

    let (res_ir, res_report) = process_document_with_options(plain_path, &non_strict_failing_opts)
        .expect("non-strict must succeed with page error");
    assert!(res_ir.pages[0].error.is_some());
    assert_eq!(res_report.pages_failed, 1);
    assert_eq!(res_report.pages_ocr, 0);
}
