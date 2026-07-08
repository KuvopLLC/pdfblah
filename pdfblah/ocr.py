"""Add a real, searchable text layer to a scanned (image-only) PDF.

This is the one thing a find-and-replace tool genuinely cannot do on its own: a scan
has no text to find. OCR reads the words off the image with Tesseract and lays an
invisible, selectable text layer over the page, so the scan becomes searchable and
copy-and-pasteable.

The page image is left exactly as it was; only a hidden text layer is added. Note that
the recognized text uses a subset OCR font, so pdfblah's own find-and-replace will
usually refuse to edit it (the font-safety gate protecting you from garbled output) --
OCR here is for search and extraction, not for editing the recognized words in place.
Needs the Tesseract engine and Ghostscript on the system; `pdfblah doctor` checks.
"""
import os


def languages():
    """List the OCR languages Tesseract has installed (for example ['eng', 'deu'])."""
    try:
        from ocrmypdf._exec import tesseract
        return sorted(tesseract.get_languages())
    except Exception:
        return []


def ocr(input_path, output_path, lang="eng", force=False, deskew=False,
        rotate_pages=False, optimize=1, sidecar=None):
    """OCR a PDF, writing a copy with a searchable text layer to output_path.

    lang          Tesseract language(s), e.g. "eng" or "eng+deu".
    force         Re-OCR pages that already have text (default: skip pages with text).
    deskew        Straighten crooked scans before OCR.
    rotate_pages  Auto-rotate pages to the detected orientation.
    optimize      0-3, how hard to compress images afterwards (default 1).
    sidecar       Optional path to also write the recognized plain text.

    Returns a status dict. If Tesseract or Ghostscript are missing it returns a clear
    ok=False message rather than a stack trace.
    """
    import importlib.util
    if importlib.util.find_spec("ocrmypdf") is None:
        return {"ok": False, "code": "no_ocrmypdf",
                "error": "OCR support is not installed (pip install \"pdfblah[ocr]\")."}

    want_sidecar = sidecar or (os.path.splitext(output_path)[0] + ".ocr.txt")
    # Run ocrmypdf in a short-lived subprocess: it (and Tesseract's Python bindings) load
    # native libraries that can clash with the glyph engine inside a long-running process
    # (e.g. the local gui server). This keeps this process clean and OCR fully isolated.
    import json
    import subprocess
    import sys
    args = {"inp": input_path, "out": output_path, "language": lang, "deskew": bool(deskew),
            "rotate_pages": bool(rotate_pages), "optimize": int(optimize),
            "sidecar": want_sidecar, "force": bool(force)}
    code = (
        "import sys, json\n"
        "a = json.loads(sys.argv[1])\n"
        "kw = dict(language=a['language'], deskew=a['deskew'], rotate_pages=a['rotate_pages'],\n"
        "          optimize=a['optimize'], progress_bar=False, sidecar=a['sidecar'])\n"
        "kw['force_ocr' if a['force'] else 'skip_text'] = True\n"
        "try:\n"
        "    import ocrmypdf\n"
        "    ocrmypdf.ocr(a['inp'], a['out'], **kw)\n"
        "    print(json.dumps({'ok': True}))\n"
        "except Exception as e:\n"
        "    print(json.dumps({'ok': False, 'error': str(e), 'type': type(e).__name__}))\n"
    )
    try:
        proc = subprocess.run([sys.executable, "-c", code, json.dumps(args)],
                              capture_output=True, text=True, timeout=1800)
    except subprocess.TimeoutExpired:
        return {"ok": False, "code": "timeout", "error": "OCR timed out"}
    res = None
    for line in reversed((proc.stdout or "").strip().splitlines()):
        try:
            res = json.loads(line)
            break
        except Exception:
            continue
    if res is None:
        return {"ok": False, "code": "ocr_error", "error": (proc.stderr or "OCR failed").strip()[-300:]}
    if not res.get("ok"):
        msg, typ = res.get("error", ""), res.get("type", "")
        if "PriorOcrFound" in typ:
            return {"ok": False, "code": "already_ocr",
                    "error": "this PDF already has a text layer; pass force=True to redo it."}
        if "MissingDependency" in typ:
            return {"ok": False, "code": "missing_dep",
                    "error": f"a system tool for OCR is missing: {msg}. Run `pdfblah doctor`."}
        if "Encrypted" in typ:
            return {"ok": False, "code": "encrypted", "error": "decrypt the PDF first (pdfblah unlock)."}
        if "language" in msg.lower() and "not installed" in msg.lower():
            return {"ok": False, "code": "no_lang", "error": f"Tesseract language pack not installed: {msg}"}
        return {"ok": False, "code": "ocr_error", "error": msg}

    chars = 0
    text = ""
    if os.path.exists(want_sidecar):
        with open(want_sidecar, encoding="utf-8", errors="replace") as f:
            text = f.read()
        chars = len(text.strip())
    if not sidecar and os.path.exists(want_sidecar):
        os.remove(want_sidecar)
    return {"ok": True, "output": output_path, "language": lang, "chars": chars,
            "text": text, "sidecar": sidecar if sidecar else None}
