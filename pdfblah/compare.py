"""Compare two PDFs and report what changed: text differences per page (always), and
optionally a per-page visual difference ratio by rendering both. Text via pdfplumber,
rendering via pypdfium2 (both base-install dependencies)."""
import difflib
import os

from .organize import parse_ranges


def _page_texts(path):
    import pdfplumber
    with pdfplumber.open(path) as pdf:
        return [(p.extract_text() or "") for p in pdf.pages]


def compare(a_path, b_path, visual=False, out_dir=None, dpi=100):
    """Compare a_path and b_path. Returns a per-page report of text additions/removals and,
    if visual, the fraction of pixels that differ (writing diff PNGs to out_dir if given)."""
    ta, tb = _page_texts(a_path), _page_texts(b_path)
    npages = max(len(ta), len(tb))
    pages = []
    changed = 0
    for i in range(npages):
        la = (ta[i] if i < len(ta) else "").splitlines()
        lb = (tb[i] if i < len(tb) else "").splitlines()
        added = [l for l in lb if l not in la]
        removed = [l for l in la if l not in lb]
        same = (la == lb)
        if not same:
            changed += 1
        pages.append({"page": i + 1, "identical": same,
                      "added": added, "removed": removed,
                      "ratio": round(difflib.SequenceMatcher(None, "\n".join(la), "\n".join(lb)).ratio(), 3)})

    result = {"ok": True, "pages": npages, "changed": changed,
              "page_count_a": len(ta), "page_count_b": len(tb), "detail": pages}

    if visual:
        result["visual"] = _visual_diff(a_path, b_path, out_dir, dpi)
    return result


def _visual_diff(a_path, b_path, out_dir, dpi):
    import pypdfium2 as pdfium
    from PIL import Image, ImageChops

    da, db = pdfium.PdfDocument(a_path), pdfium.PdfDocument(b_path)
    if out_dir:
        os.makedirs(out_dir, exist_ok=True)
    scale = dpi / 72.0
    out = []
    try:
        for i in range(max(len(da), len(db))):
            if i >= len(da) or i >= len(db):
                out.append({"page": i + 1, "diff": 1.0, "note": "page only in one file"})
                continue
            ia = da[i].render(scale=scale).to_pil().convert("RGB")
            ib = db[i].render(scale=scale).to_pil().convert("RGB")
            if ia.size != ib.size:
                ib = ib.resize(ia.size)
            diff = ImageChops.difference(ia, ib)
            bbox = diff.getbbox()
            # fraction of pixels that differ beyond antialiasing noise
            hist = diff.convert("L").histogram()
            changed_px = sum(hist[13:])
            ratio = round(changed_px / (ia.width * ia.height), 4)
            entry = {"page": i + 1, "diff": ratio}
            if out_dir and bbox:
                p = os.path.join(out_dir, f"diff-{i + 1:03d}.png")
                diff.save(p)
                entry["image"] = p
            out.append(entry)
    finally:
        da.close()
        db.close()
    return out
