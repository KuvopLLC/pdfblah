"""Drop the pages nobody meant to send: blanks and exact repeats.

Office PDFs that pass through several hands accumulate blank separator pages and
whole re-inserted sections. tidy() renders each page and decides deterministically:
a page is blank when almost none of its pixels differ from the paper AND it carries
no real text, and a duplicate when its rendered pixels are identical to an earlier
page's. No AI, no guessing at meaning, nothing uploaded; it is pixel arithmetic and
a hash, on your machine.

Deliberately conservative: a page with any extractable words is never dropped as
blank (so "Page 3" footers and "this page intentionally left blank" survive), and a
re-scanned repeat (same words, new pixels) does not count as a duplicate. Exactness
over cleverness; the report says which pages went and why, and --dry-run shows it
without writing anything.
"""
import os

from ._pdfium import PDFIUM_LOCK

_DPI = 100          # decision resolution: fast, and identical digital copies stay identical
_EDGE_TRIM = 0.03   # ignore a 3% border: scanner edge shadows and punch holes are not ink
_INK_DELTA = 40     # a pixel this much darker than the paper tone counts as ink


def tidy(input_path, output_path=None, drop_blank=True, drop_duplicates=True,
         blank_threshold=0.003, dry_run=False):
    """Copy a PDF without its blank pages and exact duplicate pages.

    drop_blank        Drop pages with (almost) no ink and no extractable text.
    drop_duplicates   Drop pages whose rendered pixels match an earlier page exactly.
    blank_threshold   Fraction of inked pixels below which a textless page is blank
                      (default 0.003 = 0.3% of the page).
    dry_run           Only report what would be dropped; write nothing.

    Returns {ok, pages, kept, dropped: [{page, reason, ("of")}], output}.
    """
    try:
        import numpy as np
        import pypdfium2 as pdfium
    except ImportError:
        return {"ok": False,
                "error": 'tidy needs the app extra: pip install "pdfblah[app]"'}
    import hashlib

    import pdfplumber
    import pikepdf

    from .organize import _carry_metadata

    if not dry_run and not output_path:
        return {"ok": False, "error": "output_path is required unless dry_run"}

    with PDFIUM_LOCK:
        doc = pdfium.PdfDocument(input_path)
        n = len(doc)
    dropped, seen = [], {}
    plumber = pdfplumber.open(input_path) if drop_blank else None
    try:
        for i in range(n):
            with PDFIUM_LOCK:
                pil = doc[i].render(scale=_DPI / 72).to_pil().convert("L")
            g = np.asarray(pil, dtype=np.uint8)
            th, tw = int(g.shape[0] * _EDGE_TRIM), int(g.shape[1] * _EDGE_TRIM)
            core = g[th:g.shape[0] - th or None, tw:g.shape[1] - tw or None]
            if drop_blank:
                paper = float(np.percentile(core, 90))
                ink = float((core < paper - _INK_DELTA).mean())
                if ink <= blank_threshold:
                    text = (plumber.pages[i].extract_text() or "").strip()
                    if not text:
                        dropped.append({"page": i + 1, "reason": "blank"})
                        continue
            if drop_duplicates:
                key = (g.shape, hashlib.sha256(g.tobytes()).hexdigest())
                if key in seen:
                    dropped.append({"page": i + 1, "reason": "duplicate", "of": seen[key]})
                    continue
                seen[key] = i + 1
    finally:
        if plumber is not None:
            plumber.close()
        with PDFIUM_LOCK:
            doc.close()

    gone = {d["page"] - 1 for d in dropped}
    keep = [i for i in range(n) if i not in gone]
    if not keep:
        return {"ok": False,
                "error": "every page would be dropped; refusing to write an empty PDF"}
    result = {"ok": True, "pages": n, "kept": len(keep), "dropped": dropped,
              "output": None if dry_run else output_path}
    if dry_run or not dropped:
        if not dropped and not dry_run:
            # nothing to remove: still write the copy the caller asked for
            with pikepdf.open(input_path) as src:
                src.save(output_path)
        return result
    with pikepdf.open(input_path) as src:
        dst = pikepdf.new()
        for i in keep:
            dst.pages.append(src.pages[i])
        _carry_metadata(src, dst)
        dst.save(output_path)
        dst.close()
    return result
