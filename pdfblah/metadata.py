"""Read and edit PDF metadata (DocInfo + XMP).

The rest of pdfblah preserves metadata verbatim on purpose. This module is the one
place that deliberately reports or changes it, so "keep it intact" stays the default
everywhere else (and on the hosted site).
"""
import os

import pikepdf

# friendly names -> XMP keys. Editing XMP through open_metadata() keeps the matching
# DocInfo entry in sync, so both views agree.
_XMP = {
    "title": "dc:title",
    "author": "dc:creator",
    "subject": "dc:description",
    "keywords": "pdf:Keywords",
    "creator": "xmp:CreatorTool",
    "producer": "pdf:Producer",
}


def read_metadata(input_path):
    """Return a report of everything metadata-ish in the PDF: DocInfo, XMP, page
    count, PDF version, encryption, and the first page size."""
    with pikepdf.open(input_path) as pdf:
        docinfo = {}
        try:
            for k, v in pdf.docinfo.items():
                docinfo[str(k).lstrip("/")] = str(v)
        except Exception:
            pass
        xmp = {}
        try:
            with pdf.open_metadata() as m:
                for k in m:
                    val = m[k]
                    xmp[k] = list(val) if isinstance(val, (list, set, tuple)) else str(val)
        except Exception:
            pass
        report = {
            "pdf_version": str(pdf.pdf_version),
            "pages": len(pdf.pages),
            "encrypted": bool(pdf.is_encrypted),
            "docinfo": docinfo,
            "xmp": xmp,
        }
        try:
            b = pdf.pages[0].mediabox
            report["page1_size_pt"] = [round(float(b[2]) - float(b[0]), 1),
                                       round(float(b[3]) - float(b[1]), 1)]
        except Exception:
            pass
        return report


def strip_and_set(pdf, strip=False, sets=None):
    """Mutate an open pikepdf: remove all metadata if `strip`, then set fields from
    `sets`. Returns the list of changes made."""
    changed = []
    if strip:
        if pikepdf.Name.Info in pdf.trailer:
            del pdf.trailer.Info
        if pikepdf.Name.Metadata in pdf.Root:
            del pdf.Root.Metadata
        changed.append("stripped")
    if sets:
        custom = {}
        # set_pikepdf_as_editor=False so we do not stamp pikepdf's identity into the
        # metadata we are supposed to be cleaning.
        with pdf.open_metadata(set_pikepdf_as_editor=False) as meta:   # syncs DocInfo
            for k, v in sets.items():
                lk = k.lower()
                if lk in _XMP:
                    xk = _XMP[lk]
                    meta[xk] = [v] if xk == "dc:creator" else v   # creator is a Seq
                    changed.append(lk)
                else:
                    custom[k] = v
        for k, v in custom.items():
            pdf.docinfo[pikepdf.Name("/" + k)] = v
            changed.append(k)
    return changed


def apply_to_file(path, strip=False, sets=None):
    """Apply metadata edits to an existing PDF in place. No-op if nothing requested."""
    if not strip and not sets:
        return []
    pdf = pikepdf.open(path, allow_overwriting_input=True)
    try:
        changed = strip_and_set(pdf, strip, sets)
        pdf.save(path, fix_metadata_version=False, deterministic_id=False)
    finally:
        pdf.close()
    return changed


def edit_metadata(input_path, output_path, strip=False, sets=None):
    """Write an edited copy. `strip` removes all DocInfo and XMP; `sets` (a dict of
    field -> value) sets standard fields (title/author/subject/keywords/creator/
    producer, kept in sync across XMP and DocInfo) or any custom DocInfo key. The page
    content is untouched. Returns a status dict."""
    if os.path.abspath(input_path) == os.path.abspath(output_path):
        return {"ok": False, "error": "refusing to overwrite the input"}
    pdf = pikepdf.open(input_path)
    try:
        changed = strip_and_set(pdf, strip, sets)
        pdf.save(output_path, fix_metadata_version=False, deterministic_id=False)
    finally:
        pdf.close()
    return {"ok": True, "changed": changed, "output": output_path}
