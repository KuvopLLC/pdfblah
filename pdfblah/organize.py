"""Page-level PDF operations that never touch page content: combine files, split, keep
or drop and reorder pages, rotate, and crop. All structure-only via pikepdf, so they
always work (no font reproduction needed) and every original metadata field (DocInfo and
XMP) is carried over. Report dicts match the rest of pdfblah: {"ok": bool, ...}."""
import os
from contextlib import ExitStack

import pikepdf


def parse_ranges(spec, n):
    """Turn a 1-based page spec into a list of 0-based indices, in the given order.
    '' or 'all' -> every page. '1-3,5,8-' -> 1..3, 5, 8..end. '-3' -> 1..3. Duplicates
    and custom order are allowed (e.g. '3,1,2'). Raises ValueError on out-of-range."""
    spec = (spec or "").strip().lower()
    if spec in ("", "all"):
        return list(range(n))
    out = []
    for part in spec.split(","):
        part = part.strip()
        if not part:
            continue
        if "-" in part:
            a, _, b = part.partition("-")
            a = int(a) if a.strip() else 1
            b = int(b) if b.strip() else n
        else:
            a = b = int(part)
        if not (1 <= a <= n) or not (1 <= b <= n):
            raise ValueError(f"page {part!r} is out of range 1..{n}")
        out.extend(range(a - 1, b) if a <= b else range(a - 1, b - 2, -1))
    return out


def _carry_metadata(src, dst):
    """Copy DocInfo and XMP from src Pdf to dst Pdf, so a new document keeps the
    original's metadata (pdfblah keeps metadata intact unless you ask to change it)."""
    # DocInfo. (Don't use src.open_metadata(): entering it mutates the source's DocInfo.)
    try:
        if src.docinfo is not None:
            dst.docinfo = dst.copy_foreign(src.docinfo)
    except Exception:
        pass
    # XMP: copy the raw /Metadata stream object verbatim if the source has one
    try:
        if "/Metadata" in src.Root:
            dst.Root.Metadata = dst.copy_foreign(src.Root.Metadata)
    except Exception:
        pass


def combine(inputs, output):
    """Concatenate PDFs in order into one file. Metadata comes from the first input."""
    if not inputs:
        return {"ok": False, "error": "no input files"}
    dst = pikepdf.new()
    total = 0
    with ExitStack() as stack:
        first = None
        for path in inputs:
            src = stack.enter_context(pikepdf.open(path))
            if first is None:
                first = src
            dst.pages.extend(src.pages)
            total += len(src.pages)
        _carry_metadata(first, dst)
        dst.save(output)
    dst.close()
    return {"ok": True, "files": len(inputs), "pages": total, "output": output}


def split(input_path, out_dir, every=1, ranges=None, prefix=None):
    """Split into several files. With `ranges` (a list of page specs) each spec becomes
    one file; otherwise cut into chunks of `every` pages (default one file per page)."""
    outputs = []
    with pikepdf.open(input_path) as src:
        n = len(src.pages)
        if ranges:
            groups = [parse_ranges(r, n) for r in ranges]
        else:
            step = max(1, int(every))
            groups = [list(range(i, min(i + step, n))) for i in range(0, n, step)]
        groups = [g for g in groups if g]
        if not groups:
            return {"ok": False, "error": "nothing to split"}
        os.makedirs(out_dir, exist_ok=True)
        base = prefix or os.path.splitext(os.path.basename(input_path))[0]
        width = max(2, len(str(len(groups))))
        for idx, idxs in enumerate(groups, 1):
            dst = pikepdf.new()
            for i in idxs:
                dst.pages.append(src.pages[i])
            _carry_metadata(src, dst)
            out = os.path.join(out_dir, f"{base}-{idx:0{width}d}.pdf")
            dst.save(out)
            dst.close()
            outputs.append(out)
    return {"ok": True, "parts": len(outputs), "outputs": outputs}


def select_pages(input_path, output_path, keep=None, drop=None):
    """Write a new PDF with only the pages you keep (or all but the ones you drop). `keep`
    also sets the order, so '3,1,2' reorders. Metadata is carried over."""
    with pikepdf.open(input_path) as src:
        n = len(src.pages)
        if keep is not None:
            idxs = parse_ranges(keep, n)
        elif drop is not None:
            dropped = set(parse_ranges(drop, n))
            idxs = [i for i in range(n) if i not in dropped]
        else:
            idxs = list(range(n))
        if not idxs:
            return {"ok": False, "error": "no pages selected"}
        dst = pikepdf.new()
        for i in idxs:
            dst.pages.append(src.pages[i])
        _carry_metadata(src, dst)
        dst.save(output_path)
        dst.close()
    return {"ok": True, "pages": len(idxs), "output": output_path}


def rotate(input_path, output_path, degrees, pages=None):
    """Rotate pages clockwise by degrees (a multiple of 90). `pages` limits which pages;
    default all. Rotation is relative to the page's current rotation."""
    if degrees % 90 != 0:
        return {"ok": False, "error": "rotation must be a multiple of 90"}
    with pikepdf.open(input_path) as pdf:
        idxs = parse_ranges(pages, len(pdf.pages))
        for i in set(idxs):
            pdf.pages[i].rotate(degrees, relative=True)
        pdf.save(output_path)
    return {"ok": True, "pages": len(set(idxs)), "degrees": degrees % 360, "output": output_path}


def crop(input_path, output_path, margins=None, box=None, pages=None):
    """Crop pages. `margins` = (left, bottom, right, top) points trimmed from each side, or
    `box` = (x0, y0, x1, y1) absolute crop box in points. `pages` limits which pages."""
    if not margins and not box:
        return {"ok": False, "error": "give margins or a box"}
    with pikepdf.open(input_path) as pdf:
        idxs = set(parse_ranges(pages, len(pdf.pages)))
        for i in idxs:
            page = pdf.pages[i]
            mb = [float(v) for v in (page.MediaBox if "/MediaBox" in page else [0, 0, 612, 792])]
            if box:
                nb = [float(v) for v in box]
            else:
                l, b, r, t = margins
                nb = [mb[0] + l, mb[1] + b, mb[2] - r, mb[3] - t]
            if nb[0] >= nb[2] or nb[1] >= nb[3]:
                return {"ok": False, "error": f"crop leaves page {i + 1} empty"}
            page.CropBox = nb
        pdf.save(output_path)
    return {"ok": True, "pages": len(idxs), "output": output_path}
