"""Make a copy of a PDF that is safe to send outside: the hidden things, removed.

A PDF quietly carries more than its pages: author and company names in the
metadata, comment threads nobody meant to ship, JavaScript, attached files,
private application data, and (in incrementally-saved files) every earlier
revision of the document. sanitize() strips all of it in one pass and reports
exactly what it found, because seeing the list is the point.

What it deliberately does NOT do: touch the visible page content. Words on the
page are redaction's job (`pdfblah redact`, `pdfblah scrub`); sanitize handles
everything hiding around them. Form fields keep working (their widgets are not
comments), and layered content is reported rather than ripped out, since pulling
layers can break what a page shows.

--dry-run answers the question people actually have: "what is this file
carrying?" without writing anything.
"""
import os

# annotation subtypes that are interactive form widgets, not comments
_WIDGET = "/Widget"


def sanitize(input_path, output_path=None, keep_annotations=False, dry_run=False):
    """Strip hidden data from a PDF and say what was there.

    keep_annotations  Leave comments/highlights in place (metadata, scripts,
                      attachments and the rest are still stripped).
    dry_run           Only report what the file carries; write nothing.

    Returns {ok, found: {metadata, annotations, javascript, embedded_files,
             revisions, layers}, removed: [...], kept: [...], output}.
    """
    import pikepdf

    if not dry_run and not output_path:
        return {"ok": False, "error": "output_path is required unless dry_run"}

    with open(input_path, "rb") as f:
        raw = f.read()
    revisions = max(0, raw.count(b"%%EOF") - 1)

    with pikepdf.open(input_path) as pdf:
        root = pdf.Root
        found = {
            "metadata": sorted(str(k) for k in pdf.docinfo.keys()),
            "xmp": "/Metadata" in root,
            "annotations": _annot_counts(pdf),
            "javascript": _count_js(root),
            "embedded_files": _attachment_names(pdf),
            "revisions": revisions,
            "layers": _count_layers(root),
            "private_data": _count_pieceinfo(pdf),
        }
        if dry_run:
            return {"ok": True, "found": found, "removed": [], "output": None}

        removed, kept = [], []
        if found["metadata"] or found["xmp"]:
            pdf.trailer["/Info"] = pikepdf.Dictionary()
            if "/Metadata" in root:
                del root["/Metadata"]
            removed.append(f"metadata ({len(found['metadata'])} field(s) + XMP)"
                           if found["xmp"] else f"metadata ({len(found['metadata'])} field(s))")
        total_annots = sum(found["annotations"].values())
        if total_annots and not keep_annotations:
            _strip_annots(pdf)
            removed.append(f"annotations ({total_annots})")
        elif total_annots:
            kept.append(f"annotations ({total_annots}, --keep-annotations)")
        if found["javascript"]:
            _strip_js(root, pdf)
            removed.append(f"javascript ({found['javascript']})")
        if found["embedded_files"]:
            _strip_attachments(pdf)
            removed.append(f"embedded files ({len(found['embedded_files'])})")
        if found["private_data"]:
            _strip_pieceinfo(pdf)
            removed.append(f"private application data ({found['private_data']})")
        if revisions:
            removed.append(f"earlier revisions ({revisions})")  # full rewrite drops them
        if found["layers"]:
            kept.append(f"layers ({found['layers']}; removing layers can change "
                        "what the page shows, so they stay)")
        pdf.save(output_path)

    return {"ok": True, "found": found, "removed": removed, "kept": kept,
            "output": output_path,
            "bytes_out": os.path.getsize(output_path)}


def _annot_counts(pdf):
    counts = {}
    for page in pdf.pages:
        for a in page.get("/Annots", []):
            sub = str(a.get("/Subtype", "/Unknown"))
            if sub != _WIDGET:
                counts[sub.lstrip("/")] = counts.get(sub.lstrip("/"), 0) + 1
    return counts


def _strip_annots(pdf):
    import pikepdf
    for page in pdf.pages:
        annots = page.get("/Annots")
        if annots is None:
            continue
        widgets = [a for a in annots if str(a.get("/Subtype", "")) == _WIDGET]
        if widgets:
            page.Annots = pdf.make_indirect(pikepdf.Array(widgets))
        else:
            del page["/Annots"]


def _count_js(root):
    n = 0
    names = root.get("/Names", {})
    js = names.get("/JavaScript") if hasattr(names, "get") else None
    if js is not None:
        kids = js.get("/Names")
        n += len(kids) // 2 if kids is not None else 1
    oa = root.get("/OpenAction")
    if oa is not None and hasattr(oa, "get") and str(oa.get("/S", "")) == "/JavaScript":
        n += 1
    if root.get("/AA") is not None:
        n += 1
    return n


def _strip_js(root, pdf):
    names = root.get("/Names")
    if names is not None and "/JavaScript" in names:
        del names["/JavaScript"]
    oa = root.get("/OpenAction")
    if oa is not None and hasattr(oa, "get") and str(oa.get("/S", "")) == "/JavaScript":
        del root["/OpenAction"]
    if "/AA" in root:
        del root["/AA"]
    for page in pdf.pages:
        if "/AA" in page:
            del page["/AA"]


def _attachment_names(pdf):
    try:
        return sorted(str(k) for k in pdf.attachments.keys())
    except Exception:
        return []


def _strip_attachments(pdf):
    try:
        for k in list(pdf.attachments.keys()):
            del pdf.attachments[k]
    except Exception:
        names = pdf.Root.get("/Names")
        if names is not None and "/EmbeddedFiles" in names:
            del names["/EmbeddedFiles"]


def _count_layers(root):
    ocp = root.get("/OCProperties")
    if ocp is None:
        return 0
    ocgs = ocp.get("/OCGs")
    return len(ocgs) if ocgs is not None else 0


def _count_pieceinfo(pdf):
    n = 1 if pdf.Root.get("/PieceInfo") is not None else 0
    for page in pdf.pages:
        if page.get("/PieceInfo") is not None:
            n += 1
    return n


def _strip_pieceinfo(pdf):
    if "/PieceInfo" in pdf.Root:
        del pdf.Root["/PieceInfo"]
    for page in pdf.pages:
        if "/PieceInfo" in page:
            del page["/PieceInfo"]
