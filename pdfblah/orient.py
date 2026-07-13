"""Fix pages that are upside down or sideways, automatically.

Scanners and copiers feed pages in whichever way they were stacked; the PDF comes
out with page 7 rotated 180 and page 12 on its side. auto_rotate() renders each
page, asks Tesseract's orientation detector (OSD) which way the text runs, and
fixes the page by setting its /Rotate key: a lossless flip of a switch, never a
re-render, so the page content stays byte-for-byte what it was.

Conservative on purpose: a page is only touched when the detector is confident,
and pages with too little text to judge are left exactly as they are and listed
in the report. Needs the Tesseract engine (`pdfblah doctor`); the small `osd`
data file is fetched like any other language (`pdfblah ocr --get-lang osd`) if
the system pack doesn't include it.
"""
import os
import re
import subprocess
import tempfile

from ._pdfium import PDFIUM_LOCK

_MIN_CONFIDENCE = 2.0  # tesseract's own scale; below this the guess is noise


def auto_rotate(input_path, output_path, min_confidence=_MIN_CONFIDENCE, dpi=150):
    """Detect and fix page orientation. Returns
    {ok, pages, fixed: [{page, by}], undetected: [page], output}."""
    try:
        import pypdfium2 as pdfium
    except ImportError:
        return {"ok": False,
                "error": 'auto-rotate needs the app extra: pip install "pdfblah[app]"'}
    import shutil

    import pikepdf

    if not shutil.which("tesseract"):
        return {"ok": False, "code": "missing_dep",
                "error": "auto-rotate needs Tesseract for orientation detection; "
                         "run `pdfblah doctor --install`"}
    from .ocr import _ensure_langs
    env_extra, missing = _ensure_langs(["osd"])
    if missing:
        return {"ok": False, "code": "no_lang",
                "error": "the orientation data file is missing; grab it with: "
                         "pdfblah ocr --get-lang osd"}

    with PDFIUM_LOCK:
        doc = pdfium.PdfDocument(input_path)
        n = len(doc)
    fixed, undetected = [], []
    env = {**os.environ, **env_extra} if env_extra else None
    try:
        with tempfile.TemporaryDirectory(prefix="pdfblah-orient-") as tmp:
            for i in range(n):
                with PDFIUM_LOCK:
                    pil = doc[i].render(scale=dpi / 72).to_pil().convert("L")
                png = os.path.join(tmp, "page.png")
                pil.save(png)
                rot, conf = _osd(_run_tesseract(png, env))
                if rot is None or conf < min_confidence:
                    undetected.append(i + 1)
                elif rot:
                    fixed.append({"page": i + 1, "by": rot})
    finally:
        with PDFIUM_LOCK:
            doc.close()

    if fixed:
        with pikepdf.open(input_path) as pdf:
            for f in fixed:
                pdf.pages[f["page"] - 1].rotate(f["by"], relative=True)
            pdf.save(output_path)
    else:
        import shutil as _sh
        _sh.copyfile(input_path, output_path)
    return {"ok": True, "pages": n, "fixed": fixed, "undetected": undetected,
            "output": output_path}


def _run_tesseract(png_path, env):
    try:
        proc = subprocess.run(["tesseract", png_path, "stdout", "--psm", "0"],
                              capture_output=True, text=True, timeout=120, env=env)
        return (proc.stdout or "") + (proc.stderr or "")
    except Exception:
        return ""


def _osd(text):
    """Parse Tesseract OSD output -> (rotate_degrees or None, confidence)."""
    rot = re.search(r"Rotate:\s*(\d+)", text)
    conf = re.search(r"Orientation confidence:\s*([\d.]+)", text)
    if not rot:
        return None, 0.0
    return int(rot.group(1)) % 360, float(conf.group(1)) if conf else 0.0
