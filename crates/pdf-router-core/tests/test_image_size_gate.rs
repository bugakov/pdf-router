//! Tests for image extraction, size gating, and aspect ratio gating.

use std::fs;

use pdf_router_core::{
    doc_id_from_path, keep_raster_photo, page_images_dir, process_document,
    process_document_with_options, ProcessOptions,
};

fn create_test_pdf(specs: &[(u32, u32)]) -> Vec<u8> {
    use lopdf::{Dictionary, Document, Stream};

    let mut doc = Document::with_version("1.5");
    let pages_id = doc.new_object_id();

    let mut xobject_dict = Dictionary::new();
    for (idx, &(w, h)) in specs.iter().enumerate() {
        let mut img_dict = Dictionary::new();
        img_dict.set("Type", "XObject");
        img_dict.set("Subtype", "Image");
        img_dict.set("Width", w as i64);
        img_dict.set("Height", h as i64);
        img_dict.set("ColorSpace", "DeviceRGB");
        img_dict.set("BitsPerComponent", 8);

        let data = vec![200u8; (w * h * 3) as usize];
        let stream = Stream::new(img_dict, data);
        let img_id = doc.add_object(stream);
        xobject_dict.set(format!("Im{}", idx), img_id);
    }

    let mut res_dict = Dictionary::new();
    res_dict.set("XObject", xobject_dict);

    let mut page_dict = Dictionary::new();
    page_dict.set("Type", "Page");
    page_dict.set("Parent", pages_id);
    page_dict.set("MediaBox", vec![0.into(), 0.into(), 600.into(), 800.into()]);
    page_dict.set("Resources", res_dict);

    let page_id = doc.add_object(page_dict);

    let mut pages_dict = Dictionary::new();
    pages_dict.set("Type", "Pages");
    pages_dict.set("Kids", vec![page_id.into()]);
    pages_dict.set("Count", 1);
    doc.set_object(pages_id, pages_dict);

    let mut catalog = Dictionary::new();
    catalog.set("Type", "Catalog");
    catalog.set("Pages", pages_id);
    let catalog_id = doc.add_object(catalog);

    doc.trailer.set("Root", catalog_id);

    let mut buf = Vec::new();
    doc.save_to(&mut buf).unwrap();
    buf
}

#[test]
fn test_keep_raster_photo_grid() {
    let cases = [
        (49, 36, false, "icon/thumbnail: both sides below 150"),
        (5, 5, false, "bullet"),
        (3, 562, false, "vertical rule: thin side below 8"),
        (1136, 32, true, "wide strip kept: doubtful content stays"),
        (335, 149, true, "narrow horizontal map/chart: keep"),
        (500, 400, true, "ordinary photo: keep"),
        (150, 150, true, "boundary is strict: 150 is kept"),
        (150, 149, true, "only one side below: kept"),
        (8, 1000, true, "boundary: thin side of exactly 8 is kept"),
        (7, 1000, false, "thin side of 7 drops"),
        (
            0,
            0,
            false,
            "degenerate: both sides below 150, dropped by the size rule",
        ),
    ];

    for (w, h, expected, why) in cases {
        assert_eq!(
            keep_raster_photo(w, h, 150, 8, 0.0),
            expected,
            "Failed for {}x{}: {}",
            w,
            h,
            why
        );
    }
}

#[test]
fn test_keep_raster_photo_defaults_keep_everything() {
    assert!(keep_raster_photo(5, 5, 0, 0, 0.0));
    assert!(keep_raster_photo(3, 562, 0, 0, 0.0));
    assert!(keep_raster_photo(0, 0, 0, 0, 0.0));
}

#[test]
fn test_aspect_ratio_gate() {
    // 2647x110 horizontal slice has AR ~24.0, should be dropped with max_aspect_ratio=4.0
    assert!(!keep_raster_photo(2647, 110, 0, 0, 4.0));
    // 50x1200 vertical slice has AR 24.0, should be dropped
    assert!(!keep_raster_photo(50, 1200, 0, 0, 4.0));
    // 800x600 photo has AR 1.33, should be kept
    assert!(keep_raster_photo(800, 600, 0, 0, 4.0));
    // Off when 0.0
    assert!(keep_raster_photo(2647, 110, 0, 0, 0.0));
}

#[test]
fn test_size_gate_drops_junk_and_counts_it() {
    let tmp = tempfile::tempdir().unwrap();
    let pdf_path = tmp.path().join("gate.pdf");
    let out_dir = tmp.path().join("out");

    let specs = [(49, 36), (3, 562), (335, 149), (500, 400)];
    let bytes = create_test_pdf(&specs);
    fs::write(&pdf_path, bytes).unwrap();

    let opts = ProcessOptions::new()
        .with_output_dir(&out_dir)
        .with_images_size_gate(150, 8, 4.0);

    let (ir, report) = process_document_with_options(&pdf_path, &opts).unwrap();

    assert_eq!(
        report.images_dropped, 2,
        "expected 49x36 and 3x562 to drop, got {}",
        report.images_dropped
    );
    assert_eq!(
        ir.pages[0].images.len(),
        2,
        "expected chart + photo kept, got {:?}",
        ir.pages[0].images
    );

    // Verify sidecars on disk
    let doc_id = doc_id_from_path(&pdf_path.to_string_lossy());
    let img_dir = page_images_dir(&out_dir, &doc_id);
    assert!(img_dir.exists());

    for img_rel in &ir.pages[0].images {
        let abs_path = out_dir.join(img_rel);
        assert!(
            abs_path.exists(),
            "Sidecar file {} must exist",
            abs_path.display()
        );
        // Verify valid PNG
        let loaded = image::open(&abs_path).expect("Exported sidecar must be valid PNG");
        let (w, h) = (loaded.width(), loaded.height());
        assert!(w == 335 && h == 149 || w == 500 && h == 400);
    }
}

#[test]
fn test_size_gate_off_by_default() {
    let tmp = tempfile::tempdir().unwrap();
    let pdf_path = tmp.path().join("gate.pdf");
    let out_dir = tmp.path().join("out");

    let specs = [(49, 36), (3, 562), (335, 149), (500, 400)];
    let bytes = create_test_pdf(&specs);
    fs::write(&pdf_path, bytes).unwrap();

    // Disable gates: min_size = 0, min_thin = 0, max_ar = 0.0
    let opts = ProcessOptions::new()
        .with_output_dir(&out_dir)
        .with_images_size_gate(0, 0, 0.0);

    let (ir, report) = process_document_with_options(&pdf_path, &opts).unwrap();

    assert_eq!(report.images_dropped, 0);
    assert_eq!(ir.pages[0].images.len(), 4);
}

#[test]
fn test_pure_by_default_zero_disk_sidecars() {
    let tmp = tempfile::tempdir().unwrap();
    let pdf_path = tmp.path().join("gate.pdf");

    let specs = [(49, 36), (3, 562), (335, 149), (500, 400)];
    let bytes = create_test_pdf(&specs);
    fs::write(&pdf_path, bytes).unwrap();

    // Standard call without output_dir
    let ir = process_document(&pdf_path).unwrap();
    assert_eq!(ir.pages.len(), 1);
    assert!(ir.pages[0].images.is_empty());

    // Explicit ProcessOptions without output_dir
    let (ir2, report2) =
        process_document_with_options(&pdf_path, &ProcessOptions::default()).unwrap();
    assert_eq!(report2.images_dropped, 0);
    assert!(ir2.pages[0].images.is_empty());

    // Ensure no unexpected directories created in tmp
    let entries: Vec<_> = fs::read_dir(tmp.path())
        .unwrap()
        .map(|e| e.unwrap().file_name().to_string_lossy().to_string())
        .collect();
    assert_eq!(entries, vec!["gate.pdf"]);
}

#[test]
fn test_aspect_ratio_gate_in_document_extraction() {
    let tmp = tempfile::tempdir().unwrap();
    let pdf_path = tmp.path().join("ar_gate.pdf");
    let out_dir = tmp.path().join("out");

    // Extreme aspect ratio (50x1200, AR 24.0) and normal photo (500x400, AR 1.25)
    let specs = [(50, 1200), (500, 400)];
    let bytes = create_test_pdf(&specs);
    fs::write(&pdf_path, bytes).unwrap();

    // 1. With max_aspect_ratio = 4.0: 50x1200 drops, 500x400 kept
    let opts = ProcessOptions::new()
        .with_output_dir(&out_dir)
        .with_images_size_gate(0, 0, 4.0);

    let (ir, report) = process_document_with_options(&pdf_path, &opts).unwrap();
    assert_eq!(report.images_dropped, 1);
    assert_eq!(ir.pages[0].images.len(), 1);

    // 2. With max_aspect_ratio = 0.0 (off): both kept
    let out_dir_off = tmp.path().join("out_off");
    let opts_off = ProcessOptions::new()
        .with_output_dir(&out_dir_off)
        .with_images_size_gate(0, 0, 0.0);

    let (ir_off, report_off) = process_document_with_options(&pdf_path, &opts_off).unwrap();
    assert_eq!(report_off.images_dropped, 0);
    assert_eq!(ir_off.pages[0].images.len(), 2);
}

#[test]
fn test_page_background_scan_filter() {
    let tmp = tempfile::tempdir().unwrap();
    let pdf_path = tmp.path().join("scan.pdf");
    let out_dir = tmp.path().join("out");

    // Full-page raster covering >= 80% page area (600x800 page, 600x800 image = 100%)
    let specs = [(600, 800)];
    let bytes = create_test_pdf(&specs);
    fs::write(&pdf_path, bytes).unwrap();

    let opts = ProcessOptions::new()
        .with_output_dir(&out_dir)
        .with_images_size_gate(0, 0, 0.0);

    let (ir, report) = process_document_with_options(&pdf_path, &opts).unwrap();
    assert_eq!(report.images_dropped, 1, "Full page scan must be dropped");
    assert_eq!(ir.pages[0].images.len(), 0);
}
