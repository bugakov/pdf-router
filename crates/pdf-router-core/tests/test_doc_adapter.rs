use std::path::Path;

use pdf_router_core::adapters::doc::process_doc;
use pdf_router_core::error::RouterError;
use pdf_router_core::formats::{detect_format, detect_format_path, Format};
use pdf_router_core::ir::{Block, HeadingBlock, SrcTool, TableBlock};
use pdf_router_core::process_document;

const OLE2_MAGIC: &[u8; 8] = &[0xD0, 0xCF, 0x11, 0xE0, 0xA1, 0xB1, 0x1A, 0xE1];

const F2_VOLUME: &str = "/home/scanum/kotel-zamechanna/archive/ВЫДАЧА_БМК Чижевщина/\
ИИ ред. формат/ИГДИ ред. формат/2. Приложения/Приложение Б. Программа работ/\
ПР ИГДИ (00845.2024.07).doc";

#[test]
fn test_corrupt_ole2_raises_document_error() {
    let mut bytes = Vec::new();
    bytes.extend_from_slice(OLE2_MAGIC);
    bytes.extend_from_slice(&[0u8; 64]);

    assert_eq!(detect_format(&bytes), Format::Doc);

    let res = process_doc(&bytes, Some("broken.doc"));
    match res {
        Err(RouterError::InvalidDocument(msg)) => {
            assert!(
                msg.contains("malformed doc"),
                "expected 'malformed doc' in {msg}"
            );
        }
        other => panic!("expected InvalidDocument error, got {other:?}"),
    }
}

#[test]
fn test_non_ole2_rejected() {
    let bytes = b"This is plain text not OLE2 binary";
    let res = process_doc(bytes, Some("plain.txt"));
    assert!(matches!(res, Err(RouterError::UnsupportedFormat(_))));
}

#[test]
fn test_lock_file_rejected_by_name() {
    let dir = tempfile::tempdir().unwrap();
    let lock_path = dir.path().join("~$смета.doc");
    let mut bytes = vec![0x04, b'u', b's', b'e', b'r', 0x00];
    bytes.extend_from_slice(&[0u8; 154]);
    std::fs::write(&lock_path, &bytes).unwrap();

    let res = detect_format_path(&lock_path);
    match res {
        Err(RouterError::UnsupportedFormat(msg)) => {
            assert!(
                msg.contains("Word lock/owner file"),
                "expected Word lock/owner file message, got {msg}"
            );
        }
        other => panic!("expected UnsupportedFormat, got {other:?}"),
    }

    let proc_res = process_document(&lock_path);
    assert!(matches!(proc_res, Err(RouterError::UnsupportedFormat(_))));
}

#[test]
fn test_missing_doc_raises_io_error() {
    let res = process_document(Path::new("/nonexistent/file/path/test.doc"));
    assert!(matches!(res, Err(RouterError::Io(_))));
}

#[test]
fn test_golden_f2_if_available() {
    let path = Path::new(F2_VOLUME);
    if !path.exists() {
        // Fast tier: skip if corpus anchor not present
        return;
    }

    let ir = process_document(path).unwrap();
    let m = &ir.metadata;
    assert_eq!(m.page_count, 1);
    assert_eq!(m.source_format, "doc");
    assert_eq!(m.pdf_type, "text_based");
    assert_eq!(m.confidence, 1.0);
    assert!(!m.is_complex_layout);
    assert_eq!(m.pages_with_tables, vec![1]);
    assert!(m
        .title
        .as_ref()
        .map(|t| t.contains("Капитальный ремонт"))
        .unwrap_or(false));

    let page = &ir.pages[0];
    assert!(!page.needs_ocr);
    assert_eq!(page.src_tool, SrcTool::DocAdapter);
    assert!(page.images.is_empty());

    let tables: Vec<&TableBlock> = page
        .blocks
        .iter()
        .filter_map(|b| match b {
            Block::Table(t) => Some(t),
            _ => None,
        })
        .collect();
    assert_eq!(tables.len(), 4);

    let heads: Vec<&HeadingBlock> = page
        .blocks
        .iter()
        .filter_map(|b| match b {
            Block::Heading(h) => Some(h),
            _ => None,
        })
        .collect();
    assert_eq!(heads.len(), 7);
    assert!(page.markdown.contains("Топографо-геодезическая"));
    assert!(page.markdown.contains("| --- |"));
}
