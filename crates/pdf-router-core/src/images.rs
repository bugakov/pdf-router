//! Pure-by-default sidecar image extraction and aspect ratio gating.

use std::collections::{HashMap, HashSet};
use std::io::Cursor;
use std::path::{Path, PathBuf};

use image::{DynamicImage, GenericImageView, ImageFormat};

use crate::error::RouterError;
use crate::ProcessOptions;

/// Predicate deciding whether a raster photo of dimensions `w`x`h` px is worth extracting.
///
/// Pure predicate with zero I/O:
/// - Drops when `min_size > 0` and both dimensions are strictly below `min_size` (icons, thumbnails).
/// - Drops when `min_thin > 0` and the thin dimension (`min(w, h)`) is strictly below `min_thin` (separator rules).
/// - Drops when `max_aspect_ratio > 0.0` and `max(w, h) / min(w, h) > max_aspect_ratio` (extreme aspect ratio slices).
/// - Keeps everything else (when all thresholds are 0, keeps all inputs including (0, 0)).
pub fn keep_raster_photo(
    w: u32,
    h: u32,
    min_size: usize,
    min_thin: usize,
    max_aspect_ratio: f64,
) -> bool {
    if min_size > 0 && (w as usize) < min_size && (h as usize) < min_size {
        return false;
    }
    let min_dim = w.min(h) as usize;
    if min_thin > 0 && min_dim < min_thin {
        return false;
    }
    if max_aspect_ratio > 0.0 && w > 0 && h > 0 {
        let max_dim = w.max(h) as f64;
        let min_dim = w.min(h) as f64;
        if max_dim / min_dim > max_aspect_ratio {
            return false;
        }
    }
    true
}

/// Compute 12-char hex SHA-256 ID of a document path.
pub fn doc_id_from_path(path: &str) -> String {
    use sha2::{Digest, Sha256};
    let p = Path::new(path);
    let resolved = if let Ok(canon) = std::fs::canonicalize(p) {
        canon.to_string_lossy().to_string()
    } else {
        let abs = if p.is_absolute() {
            p.to_path_buf()
        } else if let Ok(cwd) = std::env::current_dir() {
            cwd.join(p)
        } else {
            p.to_path_buf()
        };
        let mut components = Vec::new();
        for comp in abs.components() {
            match comp {
                std::path::Component::CurDir => {}
                std::path::Component::ParentDir => {
                    components.pop();
                }
                c => components.push(c),
            }
        }
        let mut normalized = PathBuf::new();
        for c in components {
            normalized.push(c.as_os_str());
        }
        normalized.to_string_lossy().to_string()
    };

    let mut hasher = Sha256::new();
    hasher.update(resolved.as_bytes());
    let hash = hasher.finalize();
    let hex = format!("{:x}", hash);
    hex[..12].to_string()
}

/// Get sidecar images directory for one document: `<output_dir>/<doc-id>/images`.
pub fn page_images_dir(output_dir: &Path, doc_id: &str) -> PathBuf {
    output_dir.join(doc_id).join("images")
}

/// Helper to decode a lopdf stream to an image::DynamicImage.
fn decode_stream_to_image(stream: &lopdf::Stream) -> Option<DynamicImage> {
    // 1. Try decoding directly if it's already an encoded image format (e.g. JPEG / PNG)
    if let Ok(img) = image::load_from_memory(&stream.content) {
        return Some(img);
    }

    // 2. Try decompressing stream content
    let raw = stream
        .decompressed_content()
        .unwrap_or_else(|_| stream.content.clone());

    if let Ok(img) = image::load_from_memory(&raw) {
        return Some(img);
    }

    let width = stream.dict.get(b"Width").and_then(|w| w.as_i64()).ok()? as u32;
    let height = stream.dict.get(b"Height").and_then(|h| h.as_i64()).ok()? as u32;

    if width == 0 || height == 0 {
        return None;
    }

    let bpc = stream
        .dict
        .get(b"BitsPerComponent")
        .and_then(|b| b.as_i64())
        .unwrap_or(8) as u32;

    if bpc == 8 {
        if raw.len() == (width * height * 3) as usize {
            return image::RgbImage::from_raw(width, height, raw).map(DynamicImage::ImageRgb8);
        } else if raw.len() == (width * height) as usize {
            return image::GrayImage::from_raw(width, height, raw).map(DynamicImage::ImageLuma8);
        } else if raw.len() == (width * height * 4) as usize {
            // Check if CMYK
            let cs = stream
                .dict
                .get(b"ColorSpace")
                .and_then(|c| c.as_name())
                .unwrap_or(b"");
            if cs == b"DeviceCMYK" {
                let mut rgb = Vec::with_capacity((width * height * 3) as usize);
                for &[c, m, y, k] in raw.as_chunks::<4>().0 {
                    let c = c as f32 / 255.0;
                    let m = m as f32 / 255.0;
                    let y = y as f32 / 255.0;
                    let k = k as f32 / 255.0;
                    let r = ((1.0 - c) * (1.0 - k) * 255.0).round().clamp(0.0, 255.0) as u8;
                    let g = ((1.0 - m) * (1.0 - k) * 255.0).round().clamp(0.0, 255.0) as u8;
                    let b = ((1.0 - y) * (1.0 - k) * 255.0).round().clamp(0.0, 255.0) as u8;
                    rgb.push(r);
                    rgb.push(g);
                    rgb.push(b);
                }
                return image::RgbImage::from_raw(width, height, rgb).map(DynamicImage::ImageRgb8);
            } else {
                return image::RgbaImage::from_raw(width, height, raw)
                    .map(DynamicImage::ImageRgba8);
            }
        }
    } else if bpc == 1 {
        let mut gray = Vec::with_capacity((width * height) as usize);
        let bytes_per_row = (width as usize).div_ceil(8);
        for row_chunk in raw.chunks(bytes_per_row) {
            for x in 0..width as usize {
                let byte_idx = x / 8;
                let bit_idx = 7 - (x % 8);
                let val =
                    if byte_idx < row_chunk.len() && ((row_chunk[byte_idx] >> bit_idx) & 1) != 0 {
                        255
                    } else {
                        0
                    };
                gray.push(val);
            }
        }
        return image::GrayImage::from_raw(width, height, gray).map(DynamicImage::ImageLuma8);
    }

    None
}

/// Walk XObjects for a page's resources recursively up to depth 5.
fn collect_image_streams<'a>(
    doc: &'a lopdf::Document,
    resources: &'a lopdf::Dictionary,
    visited: &mut HashSet<lopdf::ObjectId>,
    depth: usize,
    out: &mut Vec<(lopdf::ObjectId, &'a lopdf::Stream)>,
) {
    if depth > 5 {
        return;
    }
    let xobject = match doc.get_dict_in_dict(resources, b"XObject") {
        Ok(xo) => xo,
        Err(_) => return,
    };

    for (_, xval) in xobject.iter() {
        let (id, stream) = match xval {
            lopdf::Object::Reference(ref_id) => {
                if !visited.insert(*ref_id) {
                    continue;
                }
                match doc.get_object(*ref_id).and_then(|o| o.as_stream()) {
                    Ok(s) => (*ref_id, s),
                    Err(_) => continue,
                }
            }
            lopdf::Object::Stream(s) => ((0, 0), s),
            _ => continue,
        };

        if let Ok(subtype) = stream.dict.get(b"Subtype").and_then(|st| st.as_name()) {
            if subtype == b"Image" {
                out.push((id, stream));
            } else if subtype == b"Form" {
                if let Ok(form_res) = doc.get_dict_in_dict(&stream.dict, b"Resources") {
                    collect_image_streams(doc, form_res, visited, depth + 1, out);
                }
            }
        }
    }
}

/// Extract raster images from a PDF document to disk sidecars.
///
/// Returns (images_by_page_index, dropped_count).
pub fn extract_pdf_images(
    bytes: &[u8],
    doc_id: &str,
    output_dir: &Path,
    options: &ProcessOptions,
) -> Result<(HashMap<usize, Vec<String>>, usize), RouterError> {
    if !options.extract_raster_photos {
        return Ok((HashMap::new(), 0));
    }

    let doc = match lopdf::Document::load_mem(bytes) {
        Ok(d) => d,
        Err(_) => return Ok((HashMap::new(), 0)),
    };

    let pages = doc.get_pages();
    let img_dir = page_images_dir(output_dir, doc_id);

    let mut images_by_page: HashMap<usize, Vec<String>> = HashMap::new();
    let mut total_dropped = 0;
    let mut dir_created = false;

    for (&page_num_1indexed, &page_id) in &pages {
        let page_0indexed = (page_num_1indexed.saturating_sub(1)) as usize;

        let page_dict = match doc.get_dictionary(page_id) {
            Ok(d) => d,
            Err(_) => continue,
        };

        // Determine page area from MediaBox (traverse parent if needed)
        let mut curr_mb_dict = page_dict;
        let mut mediabox_opt = curr_mb_dict
            .get(b"MediaBox")
            .and_then(|mb| mb.as_array())
            .ok();
        for _ in 0..5 {
            if mediabox_opt.is_some() {
                break;
            }
            if let Ok(parent_id) = curr_mb_dict.get(b"Parent").and_then(|p| p.as_reference()) {
                if let Ok(parent_dict) = doc.get_dictionary(parent_id) {
                    mediabox_opt = parent_dict
                        .get(b"MediaBox")
                        .and_then(|mb| mb.as_array())
                        .ok();
                    curr_mb_dict = parent_dict;
                } else {
                    break;
                }
            } else {
                break;
            }
        }

        let page_area: f64 = if let Some(mediabox) = mediabox_opt {
            if mediabox.len() >= 4 {
                let x0 = mediabox[0].as_float().unwrap_or(0.0) as f64;
                let y0 = mediabox[1].as_float().unwrap_or(0.0) as f64;
                let x1 = mediabox[2].as_float().unwrap_or(0.0) as f64;
                let y1 = mediabox[3].as_float().unwrap_or(0.0) as f64;
                (x1 - x0).abs() * (y1 - y0).abs()
            } else {
                0.0
            }
        } else {
            0.0
        };

        // Determine Resources (traverse parent if needed)
        let mut resources = doc.get_dict_in_dict(page_dict, b"Resources").ok();
        if resources.is_none() {
            let mut curr_res_dict = page_dict;
            for _ in 0..5 {
                if let Ok(parent_id) = curr_res_dict.get(b"Parent").and_then(|p| p.as_reference()) {
                    if let Ok(parent_dict) = doc.get_dictionary(parent_id) {
                        if let Ok(r) = doc.get_dict_in_dict(parent_dict, b"Resources") {
                            resources = Some(r);
                            break;
                        }
                        curr_res_dict = parent_dict;
                    } else {
                        break;
                    }
                } else {
                    break;
                }
            }
        }

        let resources = match resources {
            Some(r) => r,
            None => continue,
        };

        let mut visited = HashSet::new();
        let mut image_streams = Vec::new();
        collect_image_streams(&doc, resources, &mut visited, 0, &mut image_streams);

        let mut saved = Vec::new();
        let mut photo_count = 0;

        for (_id, stream) in image_streams {
            let width = stream
                .dict
                .get(b"Width")
                .and_then(|w| w.as_i64())
                .unwrap_or(0) as u32;
            let height = stream
                .dict
                .get(b"Height")
                .and_then(|h| h.as_i64())
                .unwrap_or(0) as u32;

            if width == 0 || height == 0 {
                continue;
            }

            // Page-background scan filter (>= 80% page area)
            if page_area > 0.0 && (width as f64 * height as f64) >= 0.80 * page_area {
                total_dropped += 1;
                continue;
            }

            // Size and aspect ratio filters
            if !keep_raster_photo(
                width,
                height,
                options.images_min_size,
                options.images_min_thin,
                options.images_max_aspect_ratio,
            ) {
                total_dropped += 1;
                continue;
            }

            // Decode image
            let dyn_img = match decode_stream_to_image(stream) {
                Some(img) => img,
                None => continue,
            };

            if !dir_created {
                std::fs::create_dir_all(&img_dir)?;
                dir_created = true;
            }

            let fname = format!("page{:04}_photo_{:02}.png", page_num_1indexed, photo_count);
            let fpath = img_dir.join(&fname);

            if let Err(e) = dyn_img.save_with_format(&fpath, ImageFormat::Png) {
                eprintln!("Failed to save sidecar image {}: {}", fpath.display(), e);
                continue;
            }

            let rel_path = format!("{}/images/{}", doc_id, fname);
            saved.push(rel_path);
            photo_count += 1;
        }

        if !saved.is_empty() {
            images_by_page.insert(page_0indexed, saved);
        }
    }

    Ok((images_by_page, total_dropped))
}

/// Extract embedded images from a DOCX file to disk sidecars.
pub fn extract_docx_images(
    bytes: &[u8],
    doc_id: &str,
    output_dir: &Path,
    options: &ProcessOptions,
) -> Result<(HashMap<usize, Vec<String>>, usize), RouterError> {
    if !options.extract_raster_photos {
        return Ok((HashMap::new(), 0));
    }

    let mut archive = match zip::ZipArchive::new(Cursor::new(bytes)) {
        Ok(a) => a,
        Err(_) => return Ok((HashMap::new(), 0)),
    };

    let img_dir = page_images_dir(output_dir, doc_id);
    let mut saved = Vec::new();
    let mut total_dropped = 0;
    let mut photo_count = 0;
    let mut dir_created = false;

    for i in 0..archive.len() {
        let mut file = match archive.by_index(i) {
            Ok(f) => f,
            Err(_) => continue,
        };

        let name = file.name().to_string();
        if !name.starts_with("word/media/") {
            continue;
        }

        use std::io::Read;
        let mut img_bytes = Vec::new();
        if file.read_to_end(&mut img_bytes).is_err() {
            continue;
        }

        let dyn_img = match image::load_from_memory(&img_bytes) {
            Ok(img) => img,
            Err(_) => continue,
        };

        let (w, h) = dyn_img.dimensions();
        if !keep_raster_photo(
            w,
            h,
            options.images_min_size,
            options.images_min_thin,
            options.images_max_aspect_ratio,
        ) {
            total_dropped += 1;
            continue;
        }

        if !dir_created {
            std::fs::create_dir_all(&img_dir)?;
            dir_created = true;
        }

        let fname = format!("page0001_photo_{:02}.png", photo_count);
        let fpath = img_dir.join(&fname);

        if let Err(e) = dyn_img.save_with_format(&fpath, ImageFormat::Png) {
            eprintln!("Failed to save docx image {}: {}", fpath.display(), e);
            continue;
        }

        let rel_path = format!("{}/images/{}", doc_id, fname);
        saved.push(rel_path);
        photo_count += 1;
    }

    let mut map = HashMap::new();
    if !saved.is_empty() {
        map.insert(0, saved);
    }
    Ok((map, total_dropped))
}
