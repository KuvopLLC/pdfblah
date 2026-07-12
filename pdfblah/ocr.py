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

Languages are modular: English usually comes with Tesseract, and every other language
is one downloadable file. `get_language("deu")` (CLI: `pdfblah ocr --get-lang deu`)
fetches it from the official tessdata repos into ~/.pdfblah/tessdata, no package
manager or admin rights needed.
"""
import os

# Languages are modular. The system Tesseract usually ships with English only; any
# other language is one downloaded file (deu.traineddata etc.) from the official
# tessdata repos, and get_language() drops it here. ocr() then makes the system packs
# and the downloaded files visible to Tesseract as one collection.
TESSDATA_HOME = os.path.join(os.path.expanduser("~"), ".pdfblah", "tessdata")
_DATA_URL = "https://github.com/tesseract-ocr/tessdata_{repo}/raw/main/{code}.traineddata"


def _home_languages():
    """Language files the user has downloaded into TESSDATA_HOME."""
    try:
        return sorted(f[:-len(".traineddata")] for f in os.listdir(TESSDATA_HOME)
                      if f.endswith(".traineddata"))
    except OSError:
        return []


def _system_tessdata():
    """The system Tesseract's tessdata directory and installed languages.

    Parsed from `tesseract --list-langs`, which prints the directory in its header on
    Tesseract 5. Returns (dir_or_None, [codes]); ([], None) variants when Tesseract is
    missing entirely.
    """
    import re
    import shutil
    import subprocess
    if not shutil.which("tesseract"):
        return None, []
    try:
        proc = subprocess.run(["tesseract", "--list-langs"], capture_output=True,
                              text=True, timeout=30)
    except Exception:
        return None, []
    path, langs = None, []
    for line in ((proc.stdout or "") + "\n" + (proc.stderr or "")).splitlines():
        line = line.strip()
        if not line:
            continue
        if line.startswith("List of available languages"):
            m = re.search(r'in "?([^"(]+?)"?[: ]*\(', line)
            if m:
                path = m.group(1).strip().rstrip("/") or None
            continue
        if re.fullmatch(r"[A-Za-z][A-Za-z0-9_]{1,15}", line):
            langs.append(line)
    if path is None:
        prefix = os.environ.get("TESSDATA_PREFIX")
        if prefix and os.path.isdir(prefix):
            path = prefix.rstrip("/")
    return path, sorted(langs)


def languages():
    """Every OCR language available: system Tesseract packs plus downloaded files."""
    _, sys_langs = _system_tessdata()
    return sorted(set(sys_langs) | set(_home_languages()))


def get_language(code, best=False):
    """Download one Tesseract language file into TESSDATA_HOME.

    code   A Tesseract language code like "deu", "fra", or "chi_sim" ("osd" works too,
           for auto-rotation). The full list: github.com/tesseract-ocr/tessdata_fast.
    best   Fetch the larger, higher-accuracy model (tessdata_best) instead of the
           fast one.

    Returns a status dict; ok=False with a clear message on an unknown code or a
    network problem, never a stack trace.
    """
    import re
    import shutil
    import urllib.error
    import urllib.request
    code = (code or "").strip()
    if not re.fullmatch(r"[A-Za-z][A-Za-z0-9_]{1,15}", code):
        return {"ok": False, "code": "bad_lang",
                "error": f"'{code}' does not look like a Tesseract language code "
                         "(they look like eng, deu, fra, chi_sim)."}
    os.makedirs(TESSDATA_HOME, exist_ok=True)
    dest = os.path.join(TESSDATA_HOME, code + ".traineddata")
    url = _DATA_URL.format(repo="best" if best else "fast", code=code)
    tmp = dest + ".part"
    try:
        with urllib.request.urlopen(url, timeout=120) as r, open(tmp, "wb") as f:
            shutil.copyfileobj(r, f)
        os.replace(tmp, dest)
    except urllib.error.HTTPError as e:
        _quiet_remove(tmp)
        if e.code == 404:
            return {"ok": False, "code": "unknown_lang",
                    "error": f"no language '{code}' exists upstream; the full list is at "
                             "https://github.com/tesseract-ocr/tessdata_fast"}
        return {"ok": False, "code": "download_error",
                "error": f"download failed (HTTP {e.code}): {url}"}
    except Exception as e:
        _quiet_remove(tmp)
        return {"ok": False, "code": "download_error", "error": f"download failed: {e}"}
    return {"ok": True, "language": code, "path": dest,
            "bytes": os.path.getsize(dest), "quality": "best" if best else "fast"}


def _quiet_remove(path):
    try:
        os.remove(path)
    except OSError:
        pass


def _ensure_langs(codes):
    """Make every language in `codes` visible to Tesseract through one tessdata dir.

    If the system packs already cover everything, nothing changes. Otherwise the
    downloaded files in TESSDATA_HOME are used, and any requested system pack is
    linked in beside them (TESSDATA_PREFIX can only point at one directory).
    Returns (env_overrides, missing_codes).
    """
    import shutil
    sys_dir, sys_langs = _system_tessdata()
    if all(c in sys_langs for c in codes):
        return {}, []
    home = _home_languages()
    missing = [c for c in codes if c not in home and c not in sys_langs]
    if missing:
        return {}, missing
    for c in codes:
        if c in home or not sys_dir:
            continue
        src = os.path.join(sys_dir, c + ".traineddata")
        dst = os.path.join(TESSDATA_HOME, c + ".traineddata")
        if os.path.exists(src) and not os.path.exists(dst):
            try:
                os.symlink(src, dst)
            except OSError:
                shutil.copyfile(src, dst)
    still = [c for c in codes if not os.path.exists(os.path.join(TESSDATA_HOME, c + ".traineddata"))]
    if still:
        return {}, still
    return {"TESSDATA_PREFIX": TESSDATA_HOME}, []


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
    import shutil
    if importlib.util.find_spec("ocrmypdf") is None:
        return {"ok": False, "code": "no_ocrmypdf",
                "error": "OCR support is not installed (pip install \"pdfblah[ocr]\")."}

    # Languages are modular: check the requested ones up front and say exactly how to
    # grab a missing one. Skipped when Tesseract itself is absent; the doctor message
    # below covers that.
    env_extra = {}
    if shutil.which("tesseract"):
        need = [c for c in (lang or "eng").split("+") if c]
        if rotate_pages and "osd" not in need:
            need = need + ["osd"]
        env_extra, missing = _ensure_langs(need)
        if missing:
            return {"ok": False, "code": "no_lang",
                    "error": "OCR language(s) not installed: " + ", ".join(missing)
                             + ". Grab them with: pdfblah ocr --get-lang " + ",".join(missing)}

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
                              capture_output=True, text=True, timeout=1800,
                              env={**os.environ, **env_extra} if env_extra else None)
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
            return {"ok": False, "code": "no_lang",
                    "error": f"Tesseract language pack not installed: {msg}. "
                             "Grab languages with: pdfblah ocr --get-lang <code>"}
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
