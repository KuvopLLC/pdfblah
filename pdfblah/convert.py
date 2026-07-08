"""Convert between PDF and editable office formats.

Two directions, each with the right tool for the job:

  * PDF -> DOCX (editable Word), via pdf2docx, which reconstructs paragraphs, tables,
    and images so the result is genuinely editable rather than a page of text boxes.
  * DOCX / ODT / PPTX / XLSX / RTF / TXT / HTML -> PDF (and office -> office), via a
    headless LibreOffice, which is already the reference renderer for those formats.

Neither of these is a "true text edit" like the rest of pdfblah; converting a document
is inherently a re-layout. They are here because people constantly need to get a PDF
into something they can edit, or turn an editable doc into a PDF, and doing it well
matters. Nothing else in your PDF workflow is touched.
"""
import os
import shutil
import subprocess
import tempfile

# Formats LibreOffice can open and turn into a PDF (input extensions, lower case).
OFFICE_INPUTS = {
    "docx", "doc", "odt", "rtf", "txt", "html", "htm",
    "pptx", "ppt", "odp", "xlsx", "xls", "ods", "csv",
}


def _ext(path):
    return os.path.splitext(path)[1].lower().lstrip(".")


def _find_soffice():
    for name in ("soffice", "libreoffice"):
        p = shutil.which(name)
        if p:
            return p
    return None


def _soffice_convert(input_path, out_dir, target_ext, timeout=180):
    """Run headless LibreOffice to convert one file into out_dir as target_ext.
    Uses a throwaway user profile so it works headless and alongside other runs."""
    soffice = _find_soffice()
    if not soffice:
        return None, ("LibreOffice is not installed. Install it (for example "
                      "`sudo apt install libreoffice`) to convert office documents.")
    profile = tempfile.mkdtemp(prefix="pdfblah_lo_")
    try:
        cmd = [
            soffice, "--headless", "--norestore", "--nolockcheck", "--nodefault",
            "-env:UserInstallation=file://" + profile,
            "--convert-to", target_ext, "--outdir", out_dir, input_path,
        ]
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
        base = os.path.splitext(os.path.basename(input_path))[0]
        produced = os.path.join(out_dir, base + "." + target_ext)
        if not os.path.exists(produced):
            err = (proc.stderr or proc.stdout or "").strip()
            return None, f"LibreOffice could not produce the {target_ext.upper()} ({err[:200]})"
        return produced, None
    except subprocess.TimeoutExpired:
        return None, "LibreOffice timed out converting the document"
    finally:
        shutil.rmtree(profile, ignore_errors=True)


def pdf_to_word(input_path, output_path, pages=None):
    """Convert a PDF into an editable .docx using pdf2docx (layout-aware). `pages` is a
    range string like "1-3,5" (1-based); default is all pages."""
    import importlib.util
    if importlib.util.find_spec("pdf2docx") is None:
        return {"ok": False, "error": "pdf2docx is not installed (pip install \"pdfblah[convert]\")"}
    from .organize import parse_ranges
    import pdfplumber

    with pdfplumber.open(input_path) as pl:
        npages = len(pl.pages)
    idxs = parse_ranges(pages, npages) if pages else list(range(npages))
    if not idxs:
        return {"ok": False, "error": "no pages selected"}
    # pdf2docx imports PyMuPDF (fitz), whose native libraries can clash with the glyph
    # engine inside a long-running process (e.g. the local gui server). Run it in a
    # short-lived subprocess so fitz never loads into the caller's process. pdf2docx wants
    # a 0-based, end-exclusive page list; we pass explicit indices so gaps work.
    import json
    import subprocess
    import sys
    payload = json.dumps({"inp": input_path, "out": output_path, "pages": idxs})
    code = (
        "import sys, json, logging; logging.disable(logging.INFO);"
        "from pdf2docx import Converter;"
        "a = json.loads(sys.argv[1]); cv = Converter(a['inp']);"
        "cv.convert(a['out'], pages=a['pages']); cv.close()"
    )
    try:
        p = subprocess.run([sys.executable, "-c", code, payload],
                           capture_output=True, text=True, timeout=600)
    except subprocess.TimeoutExpired:
        return {"ok": False, "error": "PDF to Word conversion timed out"}
    if p.returncode != 0 or not os.path.exists(output_path):
        return {"ok": False, "error": "could not convert PDF to Word: " + (p.stderr or p.stdout or "").strip()[-200:]}
    return {"ok": True, "output": output_path, "pages": len(idxs), "format": "docx"}


def office_to_pdf(input_path, output_path):
    """Convert a DOCX/ODT/PPTX/XLSX/RTF/TXT/HTML document to PDF via LibreOffice."""
    with tempfile.TemporaryDirectory() as td:
        produced, err = _soffice_convert(input_path, td, "pdf")
        if err:
            return {"ok": False, "error": err}
        shutil.move(produced, output_path)
    return {"ok": True, "output": output_path, "format": "pdf"}


def to_format(input_path, output_path, target):
    """Convert an office document to another office format (for example docx -> odt)
    with LibreOffice. `target` is the output extension without the dot."""
    with tempfile.TemporaryDirectory() as td:
        produced, err = _soffice_convert(input_path, td, target)
        if err:
            return {"ok": False, "error": err}
        shutil.move(produced, output_path)
    return {"ok": True, "output": output_path, "format": target}


def convert(input_path, output_path, pages=None):
    """Convert `input_path` to `output_path`, choosing the engine from the file
    extensions:

      * PDF   -> DOCX .............. pdf2docx (editable Word)
      * office -> PDF .............. LibreOffice
      * office -> office .......... LibreOffice

    Returns a status dict. For anything it does not know how to do it returns
    ok=False with a clear message rather than guessing.
    """
    src, dst = _ext(input_path), _ext(output_path)
    if src == "pdf" and dst in ("docx", "doc"):
        return pdf_to_word(input_path, output_path, pages=pages)
    if dst == "pdf" and src in OFFICE_INPUTS:
        return office_to_pdf(input_path, output_path)
    if src in OFFICE_INPUTS and dst in OFFICE_INPUTS:
        return to_format(input_path, output_path, dst)
    if src == "pdf" and dst in ("png", "jpg", "jpeg"):
        return {"ok": False, "error": "to turn a PDF into images use `pdfblah render`"}
    return {"ok": False,
            "error": f"cannot convert .{src} to .{dst}. Supported: PDF to DOCX, and "
                     f"office documents (DOCX/ODT/PPTX/XLSX/...) to PDF."}
