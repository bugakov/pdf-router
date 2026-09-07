# TKT-RT-01 — Routing tree for v0.3

## Question

What is the routing decision tree that v0.3's `process_pdf(path, cfg)` should execute, as a flat-function Python program (no classes)?

## Resolution

```
process_pdf(path, cfg):
    ir = extract_ir(path)                                # IR 0.2, всегда
    images_dir = Path(cfg.renderers.output_dir) / "images"
    images_dir.mkdir(parents=True, exist_ok=True)
    engine = cfg.routing.ocr_fallback_engine
    params = cfg.routing.ocr_fallback_params

    with ThreadPoolExecutor(max_workers=cfg.routing.workers) as pool:
        futures = [(page,
                    pool.submit(extract_images_for_page, page, images_dir, cfg.routing),
                    pool.submit(reroute_text_for_page, page, path, engine, params, cfg.routing))
                   for page in ir.pages]
        for page, img_fut, txt_fut in futures:
            page.images = img_fut.result()
            txt_fut.result()
```

`reroute_text_for_page` и `extract_images_for_page` — две независимые ветки; см. TKT-RT-09 про параллельность.