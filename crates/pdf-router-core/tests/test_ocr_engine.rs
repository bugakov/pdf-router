use std::path::PathBuf;
use std::sync::Arc;

use pdf_router_core::error::RouterError;
use pdf_router_core::ir::Block;
use pdf_router_core::ocr::{MockOcrEngine, OcrEngine, OcrError, PageRaster, TesseractEngine};
use pdf_router_core::{process_document_with_options, ProcessOptions};

fn fixture_path(rel: &str) -> PathBuf {
    let manifest_dir = PathBuf::from(env!("CARGO_MANIFEST_DIR"));
    manifest_dir.parent().unwrap().parent().unwrap().join(rel)
}

#[test]
fn test_page_raster_ppm_conversion() {
    // 1. Raw RGB conversion
    let raster = PageRaster::new(
        0,
        2,
        2,
        150,
        vec![255, 0, 0, 0, 255, 0, 0, 0, 255, 128, 128, 128],
    );
    let ppm = raster.as_ppm_bytes();
    assert!(ppm.starts_with(b"P6\n2 2\n255\n"));
    assert_eq!(ppm.len(), b"P6\n2 2\n255\n".len() + 12);

    // 2. Already PPM passthrough
    let already_ppm = b"P6\n1 1\n255\n".to_vec();
    let raster_ppm = PageRaster::new(0, 1, 1, 150, already_ppm.clone());
    assert_eq!(raster_ppm.as_ppm_bytes(), already_ppm);
}

#[test]
fn test_mock_ocr_engine_success() {
    let mock = MockOcrEngine::new("Page header\n\n| Col A | Col B |\n|---|---|\n| 1 | 2 |");
    let raster = PageRaster::new(0, 10, 10, 150, vec![255; 300]);
    let res = mock.ocr_page(raster).expect("mock ocr should succeed");

    assert_eq!(mock.name(), "mock");
    assert!(res.text.contains("Page header"));
    assert_eq!(res.blocks.len(), 1);
    match &res.blocks[0] {
        Block::Table(tbl) => {
            assert_eq!(tbl.rows.len(), 2);
            assert_eq!(tbl.rows[0], vec!["Col A", "Col B"]);
        }
        _ => panic!("Expected table block"),
    }
}

#[test]
fn test_mock_ocr_engine_failing_page() {
    let mock = MockOcrEngine::new("Text").with_failing_page(1);

    let raster0 = PageRaster::new(0, 10, 10, 150, vec![255; 300]);
    assert!(mock.ocr_page(raster0).is_ok());

    let raster1 = PageRaster::new(1, 10, 10, 150, vec![255; 300]);
    let err = mock.ocr_page(raster1).expect_err("page 1 must fail");
    match err {
        OcrError::Engine(msg) => assert!(msg.contains("Mock OCR failure for page 1")),
        other => panic!("Unexpected error: {:?}", other),
    }
}

#[test]
fn test_tesseract_engine_config_and_missing_binary() {
    let engine = TesseractEngine::new()
        .with_bin("non_existent_tesseract_binary_xyz")
        .with_language("rus")
        .with_timeout(10.0);

    assert_eq!(engine.name(), "tesseract");
    assert_eq!(engine.bin, "non_existent_tesseract_binary_xyz");
    assert_eq!(engine.languages, vec!["rus"]);
    assert_eq!(engine.timeout_s, 10.0);

    let raster = PageRaster::new(0, 10, 10, 150, vec![255; 300]);
    let err = engine
        .ocr_page(raster)
        .expect_err("must fail on missing binary");
    match err {
        OcrError::Engine(msg) => assert!(msg.contains("not found")),
        other => panic!("Unexpected error: {:?}", other),
    }
}

#[test]
fn test_per_page_fault_tolerance_and_strict_mode() {
    let check = std::process::Command::new("djvused").arg("-v").output();
    if check.is_err() {
        eprintln!("djvused not found, skipping djvu fault tolerance test");
        return;
    }

    let plain_path = fixture_path("tests/fixtures/djvu/plain.djvu");

    // 1. Run with mock OCR failing page 0 in strict mode -> fails
    let failing_mock = Arc::new(MockOcrEngine::new("Success").with_failing_page(0));
    let strict_opts = ProcessOptions {
        strict: true,
        ocr_engine: Some(failing_mock.clone()),
        ocr_dpi: Some(150),
    };

    let err = process_document_with_options(&plain_path, &strict_opts)
        .expect_err("strict mode must fail when a page fails");
    match err {
        RouterError::Processing(msg) => {
            assert!(msg.contains("strict mode: 1 of 2 pages failed"));
        }
        other => panic!("Unexpected error: {:?}", other),
    }

    // 2. Run with mock OCR failing page 0 in non-strict mode -> succeeds with page.error populated
    let non_strict_opts = ProcessOptions {
        strict: false,
        ocr_engine: Some(failing_mock),
        ocr_dpi: Some(150),
    };

    let (ir, report) = process_document_with_options(&plain_path, &non_strict_opts)
        .expect("non-strict must succeed");
    assert_eq!(ir.pages.len(), 2);
    assert_eq!(report.pages_total, 2);
    assert_eq!(report.pages_failed, 1);
    assert_eq!(report.pages_ocr, 1);
    assert!(ir.pages[0].error.is_some());
    assert!(ir.pages[1].error.is_none());
}

#[test]
fn test_live_tesseract_if_installed() {
    // Check if tesseract binary is runnable
    let check = std::process::Command::new("tesseract")
        .arg("--version")
        .output();
    if check.is_err() {
        eprintln!("Tesseract binary not installed, skipping live test");
        return;
    }

    let engine = TesseractEngine::new();
    // 100x100 white PPM
    let raster = PageRaster::new(0, 100, 100, 150, vec![255; 100 * 100 * 3]);
    let result = engine.ocr_page(raster);
    // On an empty white square, tesseract will return empty text (OcrError::Empty) or whitespace
    match result {
        Ok(_res) => {
            assert_eq!(engine.name(), "tesseract");
        }
        Err(OcrError::Empty(idx)) => {
            assert_eq!(idx, 0);
        }
        Err(OcrError::Engine(msg)) => {
            eprintln!("Tesseract warning: {}", msg);
        }
        Err(e) => panic!("Unexpected error: {:?}", e),
    }
}
