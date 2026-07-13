"""Check a PDF's accessibility basics: the mechanical part, honestly scoped.

A screen reader needs a PDF to carry structure: tags that say what is a heading
and what order to read things in, alt text on images, a declared language, a
title it can announce. Most PDFs carry none of it. check_access() inspects the
file and reports each item with a clear verdict and the fix.

Scope, stated plainly (and repeated in the report): these are the DETERMINISTIC
checks. Passing them does not make a document accessible; the tags could still
be wrong, the reading order nonsense, the alt text useless. Failing them means
it is definitely not accessible. Certification is veraPDF/PAC territory, and
fixing semantics is human work in a remediation tool; what this gives you is
the honest 60-second answer to "is this document even trying?".
"""


def check_access(input_path):
    """Inspect accessibility basics. Returns {ok, checks: {...}, problems: [...],
    advisories: [...], pdfua_claimed}. `ok` is True when the inspection ran;
    the CLI exits 1 when problems exist."""
    import pdfplumber
    import pikepdf

    checks, problems, advisories = {}, [], []
    with pikepdf.open(input_path) as pdf:
        root = pdf.Root
        n_pages = len(pdf.pages)

        marked = False
        mi = root.get("/MarkInfo")
        if mi is not None:
            try:
                marked = bool(mi.get("/Marked"))
            except Exception:
                marked = False
        has_tree = root.get("/StructTreeRoot") is not None
        checks["tagged"] = marked and has_tree
        if not checks["tagged"]:
            problems.append("not tagged: no structure tree, so a screen reader "
                            "gets a bag of text with no headings or order")

        lang = root.get("/Lang")
        checks["language"] = str(lang) if lang is not None else None
        if not checks["language"]:
            problems.append("no document language declared (/Lang); screen "
                            "readers can't pick a voice")

        title = str(pdf.docinfo.get("/Title", "")) if pdf.docinfo else ""
        checks["title"] = title or None
        vp = root.get("/ViewerPreferences")
        display_title = False
        if vp is not None:
            try:
                display_title = bool(vp.get("/DisplayDocTitle"))
            except Exception:
                display_title = False
        checks["display_title"] = display_title
        if not title:
            problems.append("no Title in the metadata; assistive tech announces "
                            "the filename instead")
        elif not display_title:
            advisories.append("Title exists but viewers are not told to display "
                              "it (ViewerPreferences/DisplayDocTitle)")

        figures = _figures(root) if has_tree else None
        if figures is not None:
            checks["figures"] = figures
            if figures["total"] and figures["with_alt"] < figures["total"]:
                problems.append(f"{figures['total'] - figures['with_alt']} of "
                                f"{figures['total']} tagged figure(s) have no "
                                f"alt text")

        outlines = root.get("/Outlines")
        has_bookmarks = outlines is not None and outlines.get("/First") is not None
        checks["bookmarks"] = has_bookmarks
        if n_pages > 20 and not has_bookmarks:
            advisories.append(f"{n_pages} pages and no bookmarks; long documents "
                              "need a navigable outline")

        meta = root.get("/Metadata")
        pdfua = False
        if meta is not None:
            try:
                pdfua = b"pdfuaid" in meta.read_bytes()
            except Exception:
                pdfua = False

    with pdfplumber.open(input_path) as plumber:
        sample = plumber.pages[: min(5, n_pages)]
        any_text = any((p.extract_text() or "").strip() for p in sample)
    checks["text_layer"] = any_text
    if not any_text:
        problems.append("no text layer in the sampled pages (a scan?); nothing "
                        "for a screen reader to read. `pdfblah ocr` adds one")

    return {"ok": True, "pages": n_pages, "checks": checks, "problems": problems,
            "advisories": advisories, "pdfua_claimed": pdfua,
            "note": "mechanical checks only: failing means not accessible; "
                    "passing does not certify. Full validation is veraPDF/PAC; "
                    "fixing structure is human remediation work."}


def _figures(root):
    """Count /Figure elements in the structure tree, and how many carry /Alt."""
    total = with_alt = 0
    seen = set()

    def walk(node):
        nonlocal total, with_alt
        if node is None:
            return
        try:
            key = node.objgen
            if key != (0, 0):
                if key in seen:
                    return
                seen.add(key)
        except Exception:
            pass
        try:
            if str(node.get("/S", "")) == "/Figure":
                total += 1
                if node.get("/Alt") is not None:
                    with_alt += 1
            kids = node.get("/K")
        except Exception:
            return
        if kids is None:
            return
        try:
            for k in kids:
                if not isinstance(k, int):
                    walk(k)
        except TypeError:
            if not isinstance(kids, int):
                walk(kids)

    tree = root.get("/StructTreeRoot")
    if tree is not None:
        walk(tree.get("/K") if hasattr(tree, "get") else None)
        # /K of the root may be a single element or an array; walk handles both
    return {"total": total, "with_alt": with_alt}
