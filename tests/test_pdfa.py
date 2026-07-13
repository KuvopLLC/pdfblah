"""Tests for pdfa: Ghostscript conversion with real marker verification."""
import shutil

import pytest
from reportlab.pdfgen import canvas

import pdfblah as pb

needs_gs = pytest.mark.skipif(not shutil.which("gs"), reason="needs Ghostscript")


def _pdf(path):
    c = canvas.Canvas(str(path), pagesize=(400, 300))
    c.setFont("Helvetica", 14)
    c.drawString(30, 150, "archive me for the year 2050")
    c.showPage()
    c.save()
    return str(path)


@needs_gs
def test_pdfa_conversion_has_markers(tmp_path):
    src = _pdf(tmp_path / "in.pdf")
    out = tmp_path / "out.pdf"
    r = pb.to_pdfa(src, str(out))
    assert r["ok"], r
    assert r["level"] == 2
    assert r["markers"]["xmp_pdfaid"] is True
    import pikepdf
    with pikepdf.open(str(out)) as pdf:
        meta = pdf.Root.Metadata.read_bytes()
        assert b"pdfaid" in meta
    # text survives conversion
    import pdfplumber
    with pdfplumber.open(str(out)) as pdf:
        assert "archive me" in (pdf.pages[0].extract_text() or "")


@needs_gs
def test_pdfa_output_intent_when_icc_available(tmp_path):
    from pdfblah.pdfa import _find_icc
    src = _pdf(tmp_path / "in.pdf")
    out = tmp_path / "out.pdf"
    r = pb.to_pdfa(src, str(out), level=3)
    assert r["ok"] and r["level"] == 3
    if _find_icc():
        assert r["markers"]["output_intent"] is True
        assert "veraPDF" in r["note"]
    else:
        assert "no sRGB ICC profile" in r["note"]


def test_pdfa_bad_level_and_missing_gs(tmp_path, monkeypatch):
    src = _pdf(tmp_path / "in.pdf")
    assert not pb.to_pdfa(src, str(tmp_path / "o.pdf"), level=7)["ok"]
    monkeypatch.setattr("shutil.which", lambda n: None)
    r = pb.to_pdfa(src, str(tmp_path / "o.pdf"))
    assert not r["ok"] and r["code"] == "missing_dep"


@needs_gs
def test_pdfa_cli(tmp_path, capsys):
    from pdfblah.cli import main
    src = _pdf(tmp_path / "in.pdf")
    assert main(["pdfa", src, "-o", str(tmp_path / "o.pdf")]) == 0
    out = capsys.readouterr().out
    assert "converted to PDF/A-2b" in out and "XMP PDF/A id: yes" in out
