"""Shrink a PDF to fit a hard size cap, without flattening the text.

The thing making a PDF too big is almost always its images. compress() re-encodes
them in place: downscaled to what the page can actually show, recompressed as JPEG,
while every text and vector object stays untouched (still selectable, still sharp).
That is the opposite of tools that rasterize the whole page to hit a size.

Target mode is the point: portals and courts say "200 KB max" and mean it. Give
compress() a target and it walks a quality ladder, re-attempting from the ORIGINAL
each time (never recompressing a recompression), until the file fits. When even the
floor setting cannot get there, it says so honestly, reports the smallest achievable
size, and leaves that best effort on disk so you can decide.

Deliberately conservative: images carrying transparency (SMask/Mask) are left alone
rather than re-encoded wrong, and an image is only replaced when the new encoding is
actually smaller.
"""
import io
import os

import pikepdf

# (dpi cap, JPEG quality), best first. Target mode walks down until the file fits.
LADDER = [(200, 85), (150, 75), (120, 65), (100, 55), (85, 45), (72, 35)]


def compress(input_path, output_path, target_bytes=None, dpi=None, quality=None,
             grayscale=False):
    """Write a smaller copy of a PDF, keeping text and vectors intact.

    target_bytes  Hard cap. Walks the quality ladder until the output fits; on a
                  miss returns ok=False (code "target_missed") with the smallest
                  achievable size, and that best effort is left at output_path.
    dpi           Image resolution cap relative to the page (default 150, or the
                  ladder in target mode).
    quality       JPEG quality 1-95 (default 75, or the ladder in target mode).
    grayscale     Also convert images to grayscale (smaller still).

    Returns {ok, bytes_before, bytes_after, saved_pct, dpi, quality,
             images_recompressed, images_kept, attempts?}.
    """
    try:
        from PIL import Image
    except ImportError:
        return {"ok": False,
                "error": 'compress needs the app extra: pip install "pdfblah[app]"'}

    bytes_before = os.path.getsize(input_path)
    if target_bytes is not None and target_bytes <= 0:
        return {"ok": False, "error": "target must be a positive size"}

    if target_bytes is None:
        d, q = dpi or 150, quality or 75
        size, stats = _attempt(input_path, output_path, d, q, grayscale, Image)
        return {"ok": True, "bytes_before": bytes_before, "bytes_after": size,
                "saved_pct": _pct(bytes_before, size), "dpi": d, "quality": q, **stats}

    ladder = list(LADDER)
    if dpi or quality:
        ladder.insert(0, (dpi or ladder[0][0], quality or ladder[0][1]))
    attempts, best = [], None
    for d, q in ladder:
        size, stats = _attempt(input_path, output_path, d, q, grayscale, Image)
        attempts.append({"dpi": d, "quality": q, "bytes": size})
        if best is None or size < best[0]:
            best = (size, d, q, stats)
        if size <= target_bytes:
            return {"ok": True, "bytes_before": bytes_before, "bytes_after": size,
                    "saved_pct": _pct(bytes_before, size), "dpi": d, "quality": q,
                    "target": target_bytes, "attempts": attempts, **stats}
    size, d, q, stats = best
    if attempts[-1]["bytes"] != size:
        # the floor attempt was not the smallest; put the best one back on disk
        size, stats = _attempt(input_path, output_path, d, q, grayscale, Image)
    return {"ok": False, "code": "target_missed", "target": target_bytes,
            "bytes_before": bytes_before, "bytes_after": size,
            "saved_pct": _pct(bytes_before, size), "dpi": d, "quality": q,
            "attempts": attempts, **stats,
            "error": f"smallest achievable with the text layer kept is "
                     f"{size // 1024} KB (target {target_bytes // 1024} KB); that "
                     f"best effort is at the output path. Rasterizing via "
                     f"`pdfblah clean --dpi 72` can go lower, at the cost of "
                     f"selectable text."}


def _pct(before, after):
    return round((1 - after / before) * 100, 1) if before else 0.0


def _attempt(input_path, output_path, dpi, quality, grayscale, Image):
    """One full pass from the original file at a fixed (dpi, quality)."""
    recompressed = kept = 0
    with pikepdf.open(input_path) as pdf:
        done = set()
        for page in pdf.pages:
            try:
                box = [float(x) for x in page.mediabox]
                max_w = max(1, int((box[2] - box[0]) / 72 * dpi))
                max_h = max(1, int((box[3] - box[1]) / 72 * dpi))
                # get_images also finds images nested in form XObjects (pikepdf >= 8.12)
                getter = getattr(page, "get_images", None)
                images = dict(getter()) if getter else dict(page.images)
            except Exception:
                continue
            for _, raw in images.items():
                key = raw.objgen
                if key in done:
                    continue
                done.add(key)
                if _reencode(raw, max_w, max_h, quality, grayscale, Image):
                    recompressed += 1
                else:
                    kept += 1
        pdf.save(output_path, compress_streams=True, recompress_flate=True,
                 object_stream_mode=pikepdf.ObjectStreamMode.generate)
    return os.path.getsize(output_path), {"images_recompressed": recompressed,
                                          "images_kept": kept}


def _reencode(raw, max_w, max_h, quality, grayscale, Image):
    """Replace one image stream with a smaller JPEG. False = left untouched."""
    if raw.get("/SMask") is not None or raw.get("/Mask") is not None:
        return False  # transparency: re-encoding as JPEG would break it
    try:
        pil = pikepdf.PdfImage(raw).as_pil_image()
        orig_len = len(raw.read_raw_bytes())
    except Exception:
        return False
    w, h = pil.size
    if w < 2 or h < 2:
        return False
    scale = min(1.0, max_w / w, max_h / h)
    mode = "L" if (grayscale or pil.mode == "L") else "RGB"
    try:
        if pil.mode != mode:
            pil = pil.convert(mode)
        if scale < 1.0:
            pil = pil.resize((max(1, int(w * scale)), max(1, int(h * scale))),
                             Image.LANCZOS)
        buf = io.BytesIO()
        pil.save(buf, format="JPEG", quality=quality, optimize=True)
        jpeg = buf.getvalue()
    except Exception:
        return False
    if len(jpeg) >= orig_len:
        return False  # the original encoding was already better
    raw.write(jpeg, filter=pikepdf.Name("/DCTDecode"))
    raw.Width, raw.Height = pil.size
    raw.ColorSpace = pikepdf.Name("/DeviceGray" if mode == "L" else "/DeviceRGB")
    raw.BitsPerComponent = 8
    for stale in ("/DecodeParms", "/Decode", "/Interpolate", "/ImageMask"):
        if stale in raw:
            del raw[stale]
    return True
