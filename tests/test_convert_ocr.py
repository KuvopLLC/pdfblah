"""Tests for convert (PDF<->office), ocr (searchable scans), and the dependency doctor.

These features rely on system tools (LibreOffice, Tesseract) and optional pip extras.
Each test skips cleanly when its tools are missing, so the suite passes anywhere; where the
tools ARE installed (as in local dev), they run for real.
"""
import importlib.util
import io
import shutil

import pytest
from reportlab.pdfgen import canvas

import pdfblah as pb
from pdfblah import deps


def _has(mod):
    return importlib.util.find_spec(mod) is not None


HAS_LIBREOFFICE = bool(shutil.which("soffice") or shutil.which("libreoffice"))
HAS_TESSERACT = bool(shutil.which("tesseract"))
HAS_PDF2DOCX = _has("pdf2docx")
HAS_OCRMYPDF = _has("ocrmypdf")
HAS_DOCX = _has("docx")


def _make_pdf(path, text="Quarterly Report heading"):
    c = canvas.Canvas(str(path), pagesize=(400, 300))
    c.setFont("Helvetica", 16)
    c.drawString(40, 220, text)
    c.drawString(40, 180, "some body text here")
    c.save()
    return str(path)


# ---------- deps / doctor (always runnable) ----------
def test_deps_check_structure():
    st = deps.check()
    assert "tools" in st and "libs" in st
    for name in ("tesseract", "ghostscript", "libreoffice"):
        assert name in st["tools"]
        assert set(["present", "for"]).issubset(st["tools"][name])
    for mod in ("ocrmypdf", "pdf2docx"):
        assert mod in st["libs"]
    # summary lines render without raising
    assert isinstance(deps.summary_lines(), list) and deps.summary_lines()


def test_deps_install_hint():
    hint = deps.install_hint("tesseract")
    assert "tesseract" in hint and ("apt" in hint or "brew" in hint)


# ---------- convert ----------
@pytest.mark.skipif(not (HAS_LIBREOFFICE and HAS_DOCX), reason="needs LibreOffice + python-docx")
def test_office_to_pdf(tmp_path):
    from docx import Document
    src = tmp_path / "in.docx"
    d = Document(); d.add_heading("Hello Convert", 0); d.add_paragraph("body line"); d.save(str(src))
    out = tmp_path / "out.pdf"
    r = pb.convert(str(src), str(out))
    assert r["ok"], r
    import pdfplumber
    txt = pdfplumber.open(str(out)).pages[0].extract_text() or ""
    assert "Hello Convert" in txt


@pytest.mark.skipif(not HAS_PDF2DOCX, reason="needs pdf2docx")
def test_pdf_to_word(tmp_path):
    src = _make_pdf(tmp_path / "in.pdf", text="Convert Me To Word")
    out = tmp_path / "out.docx"
    r = pb.convert(src, str(out))
    assert r["ok"] and r["format"] == "docx", r
    assert out.exists() and out.stat().st_size > 0


def test_convert_unsupported_pair_is_refused(tmp_path):
    # PDF -> PNG is explicitly redirected to `render`, never a silent wrong guess
    r = pb.convert("x.pdf", "y.png")
    assert not r["ok"] and "render" in r["error"]


# ---------- ocr ----------
@pytest.mark.skipif(not (HAS_TESSERACT and HAS_OCRMYPDF), reason="needs Tesseract + ocrmypdf")
def test_ocr_makes_scan_searchable(tmp_path):
    import img2pdf
    import pdfplumber
    from PIL import Image, ImageDraw, ImageFont
    # build an image-only "scan" (no text layer)
    im = Image.new("RGB", (1000, 240), "white")
    d = ImageDraw.Draw(im)
    try:
        font = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf", 44)
    except Exception:
        font = ImageFont.load_default()
    d.text((40, 90), "OCR TARGET 4242", fill="black", font=font)
    png = tmp_path / "scan.png"; im.save(str(png))
    scan = tmp_path / "scan.pdf"
    scan.write_bytes(img2pdf.convert(str(png)))
    assert not (pdfplumber.open(str(scan)).pages[0].extract_text() or "").strip()
    out = tmp_path / "ocr.pdf"
    r = pb.ocr(str(scan), str(out), lang="eng")
    assert r["ok"], r
    text = pdfplumber.open(str(out)).pages[0].extract_text() or ""
    assert "OCR" in text and "4242" in text


def test_ocr_missing_lib_message(monkeypatch, tmp_path):
    # if ocrmypdf isn't importable, ocr() returns a clean message, not a traceback
    if HAS_OCRMYPDF:
        pytest.skip("ocrmypdf is installed here")
    r = pb.ocr(str(tmp_path / "x.pdf"), str(tmp_path / "y.pdf"))
    assert not r["ok"] and "ocr" in r["error"].lower()
