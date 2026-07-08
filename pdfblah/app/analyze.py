"""Pre-flight analysis: what kind of PDF is this, can we edit it, and what metadata
does it carry. Powers the checklist so refuse cases (encrypted, scanned, exotic
fonts) surface up front. Pure compute, shared by the hosted service and desktop."""
import re

import pikepdf
import pdfplumber

from ..engine import font_safe
from ..metadata import read_metadata
from .limits import HOSTED


def _chk(key, label, ok, detail):
    return {"key": key, "label": label, "ok": ok, "detail": detail}


def _clean_font(name):
    return re.sub(r"^[A-Z]{6}\+", "", name or "") or "(unnamed)"


def analyze_pdf(path, limits=HOSTED):
    """Report the PDF and whether it can be edited: {ok, ready, encrypted, pages,
    size, hasText, fonts:{total,problems}, metadata, checks[], blocker, text}.
    `ok` is False only when the file can't be read at all."""
    import os
    size = os.path.getsize(path)
    if size > limits.max_bytes:
        mb = max(1, round(size / 1048576))
        return {"ok": False, "code": "too_large",
                "error": f"This PDF is about {mb} MB. The limit is "
                         f"{limits.max_bytes // 1048576} MB."}
    with open(path, "rb") as f:
        if f.read(5) != b"%PDF-":
            return {"ok": False, "code": "not_pdf", "error": "That file isn't a PDF."}

    try:
        pdf = pikepdf.open(path)
    except pikepdf.PasswordError:
        return {
            "ok": True, "ready": False, "encrypted": True, "pages": None, "size": size,
            "hasText": None, "fonts": {"total": 0, "problems": []},
            "metadata": {"docinfo": {}, "xmp": {}, "fields": 0}, "text": "",
            "checks": [
                _chk("text", "Text layer", None, "cannot check until unlocked"),
                _chk("encryption", "Encryption", False, "password-protected, cannot be edited"),
                _chk("fonts", "Fonts", None, "cannot check until unlocked"),
                _chk("metadata", "Metadata", None, "cannot read until unlocked"),
            ],
            "blocker": {"code": "encrypted",
                        "error": "This PDF is password-protected, so it cannot be edited."},
        }
    except Exception as e:
        return {"ok": False, "code": "corrupt", "error": f"couldn't open PDF: {e}"}

    try:
        npages = len(pdf.pages)
        has_text = False
        fontnames = {}
        text_parts = []
        text_len = 0
        TEXT_CAP = 400_000
        with pdfplumber.open(path) as pl:
            for pi, pg in enumerate(pl.pages):
                t = pg.extract_text() or ""
                if not has_text and t.strip():
                    has_text = True
                if text_len < TEXT_CAP:
                    chunk = t[:TEXT_CAP - text_len]
                    text_parts.append(chunk); text_len += len(chunk)
                for c in pg.chars:
                    fn = c.get("fontname")
                    if fn and fn not in fontnames:
                        fontnames[fn] = pi
        problems = []
        for fn, pi in fontnames.items():
            ok, reason = font_safe(pdf.pages[pi], fn, "", {})
            if not ok:
                problems.append({"font": _clean_font(fn), "reason": reason})
        total_fonts = len(fontnames)
        meta = read_metadata(path)
    finally:
        pdf.close()

    fields = len(meta["docinfo"]) + len(meta["xmp"])
    fonts_ok = not problems
    pl = lambda n, word: f"{n} {word}" + ("" if n == 1 else "s")
    checks = [
        _chk("text", "Text layer", has_text,
             f"{pl(npages, 'page')} of selectable text" if has_text
             else "no selectable text (looks like a scan)"),
        _chk("encryption", "Encryption", True, "not encrypted, editable"),
        _chk("fonts", "Fonts", fonts_ok,
             ("all " + pl(total_fonts, "font") + " reproducible") if fonts_ok
             else f"{len(problems)} of {pl(total_fonts, 'font')} cannot be reproduced"),
        _chk("metadata", "Metadata", True, pl(fields, "field") + " read"),
    ]

    blocker = None
    if npages > limits.max_pages:
        blocker = {"code": "too_many_pages",
                   "error": f"This PDF has {npages} pages. The limit is {limits.max_pages}."}
    elif not has_text:
        blocker = {"code": "no_text",
                   "error": "This looks like a scanned PDF (no selectable text). "
                            "We can only edit digital or vector PDFs, not scans."}

    return {
        "ok": True, "ready": blocker is None, "encrypted": False, "pages": npages,
        "size": size, "hasText": has_text,
        "fonts": {"total": total_fonts, "problems": problems},
        "metadata": {"docinfo": meta["docinfo"], "xmp": meta["xmp"], "fields": fields},
        "checks": checks, "blocker": blocker,
        "pdfVersion": meta.get("pdf_version"), "page1Size": meta.get("page1_size_pt"),
        "text": "\n".join(text_parts),
    }
