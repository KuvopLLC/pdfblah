"""Check for (and optionally install) the system tools that OCR and document
conversion need, and report clearly what is missing.

OCR needs Tesseract and Ghostscript; document conversion needs LibreOffice. These are
system programs, not Python packages, so pip cannot pull them in. `pdfblah doctor`
inspects what is present, and `pdfblah doctor --install` tries to fetch what is not,
using whichever package manager it can find (apt or Homebrew). When it cannot install
automatically it prints the exact command to run.
"""
import platform
import shutil
import subprocess

# name -> what to look for and how to get it
TOOLS = {
    "tesseract": {
        "bins": ["tesseract"], "apt": ["tesseract-ocr", "tesseract-ocr-eng"],
        "brew": ["tesseract"], "for": "OCR: read text off scanned pages",
    },
    "ghostscript": {
        "bins": ["gs"], "apt": ["ghostscript"], "brew": ["ghostscript"],
        "for": "OCR: clean up and optimize scanned images",
    },
    "libreoffice": {
        "bins": ["soffice", "libreoffice"],
        "apt": ["libreoffice-writer", "libreoffice-calc", "libreoffice-impress"],
        "brew": ["--cask libreoffice"], "for": "convert office documents to and from PDF",
    },
}

# pip extras that back optional features (import name -> pip install target)
PY_LIBS = {
    "ocrmypdf": "pdfblah[ocr]",
    "pdf2docx": "pdfblah[convert]",
}


def _which(bins):
    for b in bins:
        p = shutil.which(b)
        if p:
            return p
    return None


def _version(path):
    for flag in ("--version", "-v"):
        try:
            out = subprocess.run([path, flag], capture_output=True, text=True, timeout=15)
            line = (out.stdout or out.stderr or "").strip().splitlines()
            if line:
                return line[0][:60]
        except Exception:
            continue
    return "installed"


def check():
    """Return a dict describing every system tool and Python lib pdfblah's optional
    features use, whether each is present, and how to get it."""
    tools = {}
    for name, info in TOOLS.items():
        path = _which(info["bins"])
        tools[name] = {
            "present": bool(path), "path": path,
            "version": _version(path) if path else None,
            "for": info["for"], "apt": info["apt"], "brew": info["brew"],
        }
    import importlib.util
    libs = {}
    for mod, target in PY_LIBS.items():
        # find_spec checks availability WITHOUT importing — importing ocrmypdf/pdf2docx
        # (which pulls in PyMuPDF) would load native libraries into this process.
        present = importlib.util.find_spec(mod) is not None
        libs[mod] = {"present": present, "pip": target}
    return {"tools": tools, "libs": libs}


def libreoffice_works():
    """Functionally test LibreOffice by converting a tiny text file to PDF. Catches the
    case where the `soffice` binary exists but the Writer/Calc components that actually
    do conversions are not installed (then conversions fail with 'source file could not
    be loaded'). Returns True/False."""
    import os
    import tempfile
    soffice = _which(TOOLS["libreoffice"]["bins"])
    if not soffice:
        return False
    with tempfile.TemporaryDirectory() as td:
        src = os.path.join(td, "pdfblah_check.txt")
        with open(src, "w") as f:
            f.write("pdfblah dependency check")
        try:
            subprocess.run(
                [soffice, "-env:UserInstallation=file://" + os.path.join(td, "prof"),
                 "--headless", "--convert-to", "pdf", "--outdir", td, src],
                capture_output=True, timeout=90)
        except Exception:
            return False
        return os.path.exists(os.path.join(td, "pdfblah_check.pdf"))


def _pkg_manager():
    if shutil.which("apt-get"):
        return "apt"
    if shutil.which("brew"):
        return "brew"
    return None


def _run(cmd):
    try:
        p = subprocess.run(cmd, capture_output=True, text=True, timeout=1200)
        return p.returncode == 0, (p.stderr or p.stdout or "")[-400:]
    except Exception as e:  # noqa: BLE001
        return False, str(e)


def install(names=None, use_sudo=True):
    """Try to install the missing system tools in `names` (default: all missing).
    Returns a list of per-tool results. Does not raise; when it cannot install it says
    what to run by hand."""
    state = check()["tools"]
    if names is None:
        names = [n for n, s in state.items() if not s["present"]]
    mgr = _pkg_manager()
    results = []
    for name in names:
        if state.get(name, {}).get("present"):
            results.append({"tool": name, "ok": True, "note": "already installed"})
            continue
        info = TOOLS.get(name)
        if not info:
            results.append({"tool": name, "ok": False, "note": "unknown tool"})
            continue
        if mgr == "apt":
            pkgs = info["apt"]
            cmd = (["sudo"] if use_sudo else []) + ["apt-get", "install", "-y", "-q", *pkgs]
        elif mgr == "brew":
            cmd = ["brew", "install", *(" ".join(info["brew"]).split())]
        else:
            results.append({"tool": name, "ok": False,
                            "note": "no supported package manager found; install "
                                    + " ".join(info["apt"]) + " with your system's tools"})
            continue
        ok, out = _run(cmd)
        # re-check rather than trust the exit code
        present = bool(_which(info["bins"]))
        results.append({"tool": name, "ok": present, "cmd": " ".join(cmd),
                        "note": "" if present else out.strip()[:200]})
    return results


def install_hint(name):
    """The one-line command a user should run to install a given tool by hand."""
    info = TOOLS.get(name, {})
    system = platform.system()
    if system == "Darwin":
        return "brew install " + " ".join(info.get("brew", [name]))
    return "sudo apt install " + " ".join(info.get("apt", [name]))


def summary_lines():
    """Human-readable status lines, present first then missing with how to fix."""
    st = check()
    lines = []
    for name, s in st["tools"].items():
        mark = "ok " if s["present"] else "MISSING"
        tail = s["version"] if s["present"] else "-> " + install_hint(name)
        lines.append(f"[{mark}] {name:12} {s['for']}\n           {tail}")
    for mod, s in st["libs"].items():
        mark = "ok " if s["present"] else "MISSING"
        tail = "" if s["present"] else "-> pip install \"" + s["pip"] + "\""
        lines.append(f"[{mark}] {mod:12} python library {tail}")
    return lines
