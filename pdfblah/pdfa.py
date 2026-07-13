"""Convert a PDF to PDF/A, the archival format that must still open in 2050.

Registries, courts, and archives increasingly say "PDF/A only": fonts embedded,
color defined by an embedded ICC profile, no JavaScript, no encryption, metadata
in XMP. Ghostscript does the heavy conversion; this wraps it properly (with a
real sRGB output intent, which is the part naive -dPDFA runs skip) and then
inspects its own output for the PDF/A markers before reporting success.

Honesty note, also printed in the report: full standards validation is a
discipline of its own (veraPDF is the reference validator). What this guarantees
is a Ghostscript PDF/A conversion with an embedded sRGB output intent and XMP
PDF/A identification, which is what the portals asking for "PDF/A" want.
"""
import glob
import os
import subprocess
import tempfile

_ICC_GLOBS = [
    "/usr/share/ghostscript/*/iccprofiles/srgb.icc",
    "/usr/local/share/ghostscript/*/iccprofiles/srgb.icc",
    "/opt/homebrew/share/ghostscript/*/iccprofiles/srgb.icc",
    "/usr/share/color/icc/*/sRGB.icc",
]

_DEF_PS = """%!
/ICCProfile ({icc}) def
[/_objdef {{icc_PDFA}} /type /stream /OBJ pdfmark
[{{icc_PDFA}} <</N 3>> /PUT pdfmark
[{{icc_PDFA}} ICCProfile (r) file /PUT pdfmark
[/_objdef {{OutputIntent_PDFA}} /type /dict /OBJ pdfmark
[{{OutputIntent_PDFA}} <<
  /Type /OutputIntent
  /S /GTS_PDFA1
  /DestOutputProfile {{icc_PDFA}}
  /OutputConditionIdentifier (sRGB)
>> /PUT pdfmark
[{{Catalog}} <</OutputIntents [ {{OutputIntent_PDFA}} ]>> /PUT pdfmark
"""


def _find_icc():
    for pattern in _ICC_GLOBS:
        hits = sorted(glob.glob(pattern))
        if hits:
            return hits[-1]
    return None


def to_pdfa(input_path, output_path, level=2):
    """Convert to PDF/A-{level}b (level 1, 2, or 3; default 2).

    Returns {ok, level, markers: {output_intent, xmp_pdfaid}, icc, note} or a
    clear failure. Needs Ghostscript (`pdfblah doctor --install`)."""
    import shutil

    import pikepdf

    if level not in (1, 2, 3):
        return {"ok": False, "error": "level must be 1, 2, or 3"}
    if not shutil.which("gs"):
        return {"ok": False, "code": "missing_dep",
                "error": "PDF/A conversion needs Ghostscript; run "
                         "`pdfblah doctor --install`"}
    icc = _find_icc()

    with tempfile.TemporaryDirectory(prefix="pdfblah-pdfa-") as tmp:
        cmd = ["gs", f"-dPDFA={level}", "-dBATCH", "-dNOPAUSE", "-dNOOUTERSAVE",
               "-sDEVICE=pdfwrite", "-dPDFACompatibilityPolicy=1",
               "-sColorConversionStrategy=RGB",
               f"-sOutputFile={output_path}"]
        if icc:
            def_ps = os.path.join(tmp, "PDFA_def.ps")
            with open(def_ps, "w") as f:
                f.write(_DEF_PS.format(icc=icc.replace("\\", "\\\\")
                                       .replace("(", "\\(").replace(")", "\\)")))
            cmd.append(def_ps)
        cmd.append(input_path)
        try:
            proc = subprocess.run(cmd, capture_output=True, text=True, timeout=1800)
        except subprocess.TimeoutExpired:
            return {"ok": False, "error": "Ghostscript timed out"}
    if proc.returncode or not os.path.exists(output_path):
        said = " ".join(((proc.stderr or "") + (proc.stdout or "")).split())[-300:]
        if "password" in said.lower() or "encrypted" in said.lower():
            return {"ok": False, "code": "encrypted",
                    "error": "the input is encrypted; `pdfblah unlock` it first"}
        return {"ok": False, "error": f"Ghostscript failed: {said}"}

    # inspect our own output for the PDF/A markers before claiming success
    markers = {"output_intent": False, "xmp_pdfaid": False}
    try:
        with pikepdf.open(output_path) as pdf:
            markers["output_intent"] = pdf.Root.get("/OutputIntents") is not None
            meta = pdf.Root.get("/Metadata")
            if meta is not None:
                markers["xmp_pdfaid"] = b"pdfaid" in meta.read_bytes()
    except Exception as e:
        return {"ok": False, "error": f"conversion wrote an unreadable file: {e}"}
    if not markers["xmp_pdfaid"]:
        return {"ok": False, "code": "no_markers", "markers": markers,
                "error": "Ghostscript ran but the output lacks PDF/A "
                         "identification; the input may be beyond conversion"}

    return {"ok": True, "level": level, "markers": markers, "icc": icc,
            "output": output_path,
            "note": ("converted with an embedded sRGB output intent; for formal "
                     "certification, validate with veraPDF"
                     if icc else
                     "no sRGB ICC profile was found on this system, so the "
                     "output has PDF/A metadata but no embedded output intent; "
                     "install Ghostscript's icc profiles for full conversion")}
