use pdf_router_core::ir::{
    BBox, Block, FigureBlock, HeadingBlock, Ir, Metadata, PageResult, SrcTool, TableBlock,
};
use pdf_router_core::renderers::{
    iter_chunks, iter_zvec_docs, render_chunks, render_zvec, ChunksConfig, ZvecConfig,
};
use sha2::{Digest, Sha256};

fn pipe_markdown(rows: &[Vec<String>]) -> String {
    let width = rows.iter().map(|r| r.len()).max().unwrap_or(1);
    let mut lines = Vec::new();
    for (i, r) in rows.iter().enumerate() {
        let mut norm: Vec<String> = r.iter().map(|c| c.trim().to_string()).collect();
        while norm.len() < width {
            norm.push(String::new());
        }
        lines.push(format!("| {} |", norm.join(" | ")));
        if i == 0 {
            let sep = vec!["---"; width].join(" | ");
            lines.push(format!("| {} |", sep));
        }
    }
    lines.join("\n")
}

fn make_test_table_block(caption: &str, rows_count: usize) -> TableBlock {
    let mut rows = vec![
        vec![caption.to_string(), String::new(), String::new()],
        vec![
            "Параметр".to_string(),
            "Значение".to_string(),
            "Расход (м3/ч)".to_string(),
        ],
    ];
    for i in 1..=rows_count {
        rows.push(vec![
            format!("Насос {}", i),
            format!("{}.5", i * 15),
            format!("{}", i * 50),
        ]);
    }
    let md = pipe_markdown(&rows);
    TableBlock {
        markdown: md,
        rows,
        header_rows: 1,
        bbox: Some(BBox {
            x0: 50.0,
            y0: 100.0,
            x1: 500.0,
            y1: 300.0,
        }),
        source: "pdf_inspector".to_string(),
    }
}

fn make_test_ir() -> Ir {
    let table = make_test_table_block("Таблица 1. Расход", 4);
    let p1_md = format!(
        "## Раздел 1. Общие сведения\n\n{}\n\nТекст после таблицы.",
        table.markdown
    );
    let p1 = PageResult {
        page: 0,
        markdown: p1_md,
        src_tool: SrcTool::PdfInspector,
        blocks: vec![
            Block::Heading(HeadingBlock {
                level: 2,
                text: "Раздел 1. Общие сведения".to_string(),
                bbox: None,
                source: "pdf_inspector".to_string(),
            }),
            Block::Table(table),
        ],
        images: vec!["renders/p01_drawing_vector.png".to_string()],
        error: None,
        needs_ocr: false,
    };

    let p2 = PageResult {
        page: 1,
        markdown: "### Насосная станция 2\n\nПараметры второго подъема.".to_string(),
        src_tool: SrcTool::PdfInspector,
        blocks: vec![
            Block::Heading(HeadingBlock {
                level: 3,
                text: "Насосная станция 2".to_string(),
                bbox: None,
                source: "pdf_inspector".to_string(),
            }),
            Block::Figure(FigureBlock {
                bbox: Some(BBox {
                    x0: 10.0,
                    y0: 20.0,
                    x1: 200.0,
                    y1: 300.0,
                }),
                caption: Some("Схема насосной станции М1:100".to_string()),
                image_index: Some(0),
            }),
        ],
        images: vec!["renders/p02_fig0.png".to_string()],
        error: None,
        needs_ocr: false,
    };

    Ir::new(
        Metadata {
            source_format: "pdf".to_string(),
            source_path: Some("/data/projects/kns_project.pdf".to_string()),
            title: Some("Том 4. КНС и сети".to_string()),
            page_count: 2,
            ..Default::default()
        },
        vec![p1, p2],
    )
}

#[test]
fn test_chunks_envelope_v2_and_tables() {
    let ir = make_test_ir();
    let config = ChunksConfig {
        max_tokens: 512,
        overlap_tokens: 64,
        summary: "facts".to_string(),
        embed_context: true,
    };

    let chunks: Vec<_> = iter_chunks(&ir, &config).collect();
    assert!(!chunks.is_empty());

    // 1. Table chunk validation
    let table_chunk = chunks
        .iter()
        .find(|c| c.chunk_type == "table")
        .expect("table chunk");
    assert_eq!(table_chunk.chunk_version, 2);
    assert_eq!(table_chunk.chunk_id, "0-t1");
    assert!(table_chunk
        .embed
        .contains("Том 4. КНС и сети › Раздел 1. Общие сведения"));
    assert!(table_chunk.embed.contains("Таблица 1. Расход"));
    assert!(table_chunk
        .embed
        .contains("Параметр: Насос 1, Значение: 15.5, Расход (м3/ч): 50"));
    assert!(table_chunk
        .embed
        .contains("Сводка: 3 кол., 4 строк данных."));

    let meta = &table_chunk.metadata;
    assert_eq!(meta.page, 0);
    assert_eq!(meta.source_format, "pdf");
    assert_eq!(meta.src_tool, "pdf_inspector");
    assert_eq!(meta.doc_title.as_deref(), Some("Том 4. КНС и сети"));
    assert!(meta.doc_id.is_some());
    assert_eq!(meta.doc_id.as_ref().unwrap().len(), 12);

    let t_meta = meta.table.as_ref().expect("table metadata");
    assert_eq!(t_meta.table_id, "0-t1");
    assert_eq!(t_meta.part, 1);
    assert_eq!(t_meta.parts, 1);
    assert_eq!(t_meta.n_rows, 6);
    assert_eq!(t_meta.n_cols, 3);
    assert_eq!(t_meta.header, vec!["Параметр", "Значение", "Расход (м3/ч)"]);

    // 2. Text chunk and pipe subtraction
    let text_chunks: Vec<_> = chunks.iter().filter(|c| c.chunk_type == "text").collect();
    assert!(!text_chunks.is_empty());

    // Page 0 text chunk should have pipe table subtracted
    let p0_texts: Vec<_> = text_chunks
        .iter()
        .filter(|c| c.metadata.page == 0)
        .collect();
    assert!(p0_texts
        .iter()
        .any(|c| c.markdown.contains("Текст после таблицы.")));
    assert!(!p0_texts
        .iter()
        .any(|c| c.markdown.contains("| Таблица 1. Расход |")));
}

#[test]
fn test_chunks_row_splitting_over_budget() {
    let mut ir = make_test_ir();
    // Replace with large table (50 rows)
    ir.pages[0].blocks = vec![Block::Table(make_test_table_block(
        "Таблица 1. Длинная",
        50,
    ))];

    // Tight budget
    let config = ChunksConfig {
        max_tokens: 64,
        overlap_tokens: 16,
        summary: "facts".to_string(),
        embed_context: true,
    };

    let chunks: Vec<_> = iter_chunks(&ir, &config).collect();
    let parts: Vec<_> = chunks
        .iter()
        .filter(|c| c.chunk_type == "table_part")
        .collect();
    assert!(parts.len() > 1);

    for (k, part) in parts.iter().enumerate() {
        let expected_id = format!("0-t1-{:02}", k + 1);
        assert_eq!(part.chunk_id, expected_id);
        let t_meta = part.metadata.table.as_ref().unwrap();
        assert_eq!(t_meta.part, k + 1);
        assert_eq!(t_meta.parts, parts.len());
        // Header band is duplicated
        assert!(part.embed.contains("Параметр:"));
        assert!(part.markdown.contains("| Параметр | Значение |"));
    }
}

#[test]
fn test_zvec_docs_and_breadcrumbs() {
    let ir = make_test_ir();
    let config = ZvecConfig {
        max_tokens: 512,
        overlap_tokens: 64,
        summary: "facts".to_string(),
        embed_context: true,
        doc_uid: Some("kns-doc-01".to_string()),
        project_uid: Some("proj-vbr".to_string()),
    };

    let docs: Vec<_> = iter_zvec_docs(&ir, &config).collect();
    assert!(!docs.is_empty());

    // 1. Table record
    let table_doc = docs
        .iter()
        .find(|d| d.fields.chunk_type == "table")
        .expect("table doc");
    assert_eq!(table_doc.id, "kns-doc-01_p01_t01");
    assert_eq!(table_doc.fields.page_num, 1);
    assert_eq!(table_doc.fields.bbox, "[50.0, 100.0, 500.0, 300.0]");
    assert_eq!(table_doc.fields.project_uid, "proj-vbr");
    assert_eq!(table_doc.fields.doc_uid, "kns-doc-01");
    assert_eq!(table_doc.fields.doc_name, "Том 4. КНС и сети");

    let facts: serde_json::Value =
        serde_json::from_str(&table_doc.fields.table_facts).expect("valid json facts");
    assert_eq!(facts["n_rows"], 4);
    assert_eq!(facts["n_cols"], 3);
    assert!(facts["cols"].get("Расход (м3/ч)").is_some());

    // 2. Drawing figure record on Page 2
    let draw_doc = docs
        .iter()
        .find(|d| d.fields.page_num == 2 && d.fields.chunk_type == "drawing")
        .expect("drawing doc");
    assert_eq!(draw_doc.id, "kns-doc-01_p02_draw01");
    assert_eq!(draw_doc.fields.page_num, 2);
    assert_eq!(draw_doc.fields.bbox, "[10.0, 20.0, 200.0, 300.0]");
    assert!(draw_doc.embed_text.contains("Чертёж / Схема"));
    assert!(draw_doc
        .embed_text
        .contains("Схема насосной станции М1:100"));

    // 3. Hierarchical breadcrumb walk-back on Page 2 text
    let p2_text = docs
        .iter()
        .find(|d| d.fields.page_num == 2 && d.fields.chunk_type == "text")
        .expect("p2 text doc");
    assert_eq!(
        p2_text.fields.section_path,
        "Раздел 1. Общие сведения > Насосная станция 2"
    );
    assert_eq!(p2_text.fields.section_title, "Насосная станция 2");
    assert!(p2_text
        .embed_text
        .contains("Раздел 1. Общие сведения > Насосная станция 2"));
}

#[test]
fn test_idempotency_and_deterministic_output() {
    let ir = make_test_ir();

    let chunks_cfg = ChunksConfig::default();
    let render1 = render_chunks(&ir, &chunks_cfg);
    let render2 = render_chunks(&ir, &chunks_cfg);
    assert_eq!(render1, render2);

    let mut hasher1 = Sha256::new();
    hasher1.update(render1.as_bytes());
    let hash1 = format!("{:x}", hasher1.finalize());

    let mut hasher2 = Sha256::new();
    hasher2.update(render2.as_bytes());
    let hash2 = format!("{:x}", hasher2.finalize());
    assert_eq!(hash1, hash2);

    let zvec_cfg = ZvecConfig::default();
    let zvec1 = render_zvec(&ir, &zvec_cfg);
    let zvec2 = render_zvec(&ir, &zvec_cfg);
    assert_eq!(zvec1, zvec2);

    let mut zhasher1 = Sha256::new();
    zhasher1.update(zvec1.as_bytes());
    let zhash1 = format!("{:x}", zhasher1.finalize());

    let mut zhasher2 = Sha256::new();
    zhasher2.update(zvec2.as_bytes());
    let zhash2 = format!("{:x}", zhasher2.finalize());
    assert_eq!(zhash1, zhash2);
}

#[test]
fn test_constant_memory_streaming() {
    let mut pages = Vec::with_capacity(100);
    for i in 0..100 {
        pages.push(PageResult {
            page: i,
            markdown: format!("## Глава {}\nТекст страницы {}.", i + 1, i + 1),
            src_tool: SrcTool::PdfInspector,
            blocks: vec![Block::Heading(HeadingBlock {
                level: 2,
                text: format!("Глава {}", i + 1),
                bbox: None,
                source: "pdf_inspector".to_string(),
            })],
            images: vec![],
            error: None,
            needs_ocr: false,
        });
    }

    let ir = Ir::new(
        Metadata {
            source_format: "pdf".to_string(),
            page_count: 100,
            ..Default::default()
        },
        pages,
    );

    // Stream chunks one by one
    let mut chunk_count = 0;
    for chunk in iter_chunks(&ir, &ChunksConfig::default()) {
        assert_eq!(chunk.chunk_version, 2);
        chunk_count += 1;
    }
    assert_eq!(chunk_count, 100);

    // Stream zvec docs one by one
    let mut doc_count = 0;
    for doc in iter_zvec_docs(&ir, &ZvecConfig::default()) {
        assert_eq!(doc.fields.chunk_type, "text");
        doc_count += 1;
    }
    assert_eq!(doc_count, 100);
}
