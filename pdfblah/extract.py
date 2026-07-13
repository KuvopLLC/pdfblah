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


def extract_tables(input_path, output_path=None, pages=None):
    """Pull the tables out of a PDF as CSV.

    Table detection is pdfplumber's (ruled and whitespace-aligned tables in the
    real text layer; a scanned table needs `pdfblah ocr` first). If output_path
    ends in .csv every table goes into that one file, separated by a blank row;
    if it is a directory, each table gets its own file (table-p3-1.csv). With no
    output_path the rows come back in the report only.

    Returns {ok, tables, rows, outputs, found: [{page, rows, cols}], data?}.
    """
    import csv
    import io

    import pdfplumber

    found, all_tables = [], []
    with pdfplumber.open(input_path) as pdf:
        idxs = parse_ranges(pages, len(pdf.pages))
        for i in idxs:
            for t in pdf.pages[i].extract_tables():
                rows = [["" if c is None else str(c) for c in row] for row in t]
                if not any(any(cell.strip() for cell in row) for row in rows):
                    continue  # an all-empty grid is a ruling artifact, not a table
                found.append({"page": i + 1, "rows": len(rows),
                              "cols": max(len(r) for r in rows)})
                all_tables.append(rows)
    if not all_tables:
        return {"ok": False, "code": "no_tables",
                "error": "no tables detected in the text layer (a scanned table "
                         "needs `pdfblah ocr` first)"}

    def _write(fh, tables):
        w = csv.writer(fh)
        for n, rows in enumerate(tables):
            if n:
                w.writerow([])
            w.writerows(rows)

    outputs = []
    if output_path and output_path.lower().endswith(".csv"):
        with open(output_path, "w", newline="", encoding="utf-8") as fh:
            _write(fh, all_tables)
        outputs = [output_path]
    elif output_path:
        os.makedirs(output_path, exist_ok=True)
        counts = {}
        for meta, rows in zip(found, all_tables):
            counts[meta["page"]] = counts.get(meta["page"], 0) + 1
            out = os.path.join(output_path,
                               f"table-p{meta['page']}-{counts[meta['page']]}.csv")
            with open(out, "w", newline="", encoding="utf-8") as fh:
                _write(fh, [rows])
            outputs.append(out)
    r = {"ok": True, "tables": len(all_tables),
         "rows": sum(m["rows"] for m in found), "found": found, "outputs": outputs}
    if not output_path:
        buf = io.StringIO()
        _write(buf, all_tables)
        r["data"] = buf.getvalue()
    return r
