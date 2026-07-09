"""Turn a PDF into something else, or pull things out of it: render pages to images,
extract the text, and extract embedded images. Uses pypdfium2 (rendering) and pdfplumber
(text), both already pulled in by the base install, and pikepdf for embedded images."""
import os

import pikepdf

from .organize import parse_ranges


def render(input_path, out_dir, pages=None, dpi=150, fmt="png", prefix=None):
    """Render pages to PNG or JPG images at the given DPI. Writes files into out_dir and
    returns their paths. `pages` limits which pages (default all)."""
    import pypdfium2 as pdfium

    from ._pdfium import PDFIUM_LOCK

    fmt = fmt.lower().lstrip(".")
    if fmt in ("jpg", "jpeg"):
        fmt, pil_fmt = "jpg", "JPEG"
    elif fmt == "png":
        pil_fmt = "PNG"
    else:
        return {"ok": False, "error": f"unsupported image format {fmt!r} (use png or jpg)"}
    os.makedirs(out_dir, exist_ok=True)
    base = prefix or os.path.splitext(os.path.basename(input_path))[0]
    PDFIUM_LOCK.acquire()
    doc = None
    outputs = []
    try:
        doc = pdfium.PdfDocument(input_path)
        idxs = parse_ranges(pages, len(doc))
        width = max(2, len(str(len(doc))))
        scale = dpi / 72.0
        for i in idxs:
            pil = doc[i].render(scale=scale).to_pil()
            if pil_fmt == "JPEG":
                pil = pil.convert("RGB")
            out = os.path.join(out_dir, f"{base}-{i + 1:0{width}d}.{fmt}")
            pil.save(out, pil_fmt)
            outputs.append(out)
    finally:
        if doc is not None:
            doc.close()
        PDFIUM_LOCK.release()
    return {"ok": True, "pages": len(outputs), "dpi": dpi, "outputs": outputs}


def extract_text(input_path, output_path=None, pages=None):
    """Extract the selectable text. Writes it to output_path if given, and returns it in
    the report under "text". `pages` limits which pages (default all)."""
    import pdfplumber

    chunks = []
    with pdfplumber.open(input_path) as pdf:
        idxs = parse_ranges(pages, len(pdf.pages))
        for i in idxs:
            chunks.append(pdf.pages[i].extract_text() or "")
    text = "\n\n".join(chunks)
    if output_path:
        with open(output_path, "w", encoding="utf-8") as f:
            f.write(text)
    return {"ok": True, "pages": len(idxs), "chars": len(text),
            "text": text, "output": output_path}


def extract_images(input_path, out_dir, pages=None, prefix=None):
    """Extract embedded raster images. Writes each to out_dir and returns their paths."""
    os.makedirs(out_dir, exist_ok=True)
    base = prefix or os.path.splitext(os.path.basename(input_path))[0]
    outputs = []
    with pikepdf.open(input_path) as pdf:
        idxs = parse_ranges(pages, len(pdf.pages))
        for pi in idxs:
            page = pdf.pages[pi]
            images = getattr(page, "images", {}) or {}
            for n, (name, raw) in enumerate(images.items(), 1):
                try:
                    pi_img = pikepdf.PdfImage(raw)
                    stem = os.path.join(out_dir, f"{base}-p{pi + 1:03d}-{n:02d}")
                    written = pi_img.extract_to(fileprefix=stem)
                    outputs.append(written)
                except Exception:
                    continue
    return {"ok": True, "images": len(outputs), "outputs": outputs}
