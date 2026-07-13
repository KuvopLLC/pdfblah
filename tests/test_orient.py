"""Tests for auto_rotate: orientation detection and lossless fixing.

The OSD parser and decision logic run everywhere; the end-to-end test needs
Tesseract with osd data and skips cleanly without it."""
import shutil
import subprocess

import pikepdf
import pytest
from reportlab.pdfgen import canvas

import pdfblah as pb
from pdfblah.orient import _osd


def _has_osd():
    if not shutil.which("tesseract"):
        return False
    try:
        out = subprocess.run(["tesseract", "--list-langs"], capture_output=True,
                             text=True, timeout=15)
        return "osd" in (out.stdout + out.stderr)
    except Exception:
        return False


def _texty_pdf(path, upside_down_pages=()):
    c = canvas.Canvas(str(path), pagesize=(612, 792))
    for _ in range(2):
        c.setFont("Helvetica", 13)
        for line in range(30):
            c.drawString(60, 740 - line * 22,
                         "The quick brown fox jumps over the lazy dog again and again.")
        c.showPage()
    c.save()
    with pikepdf.open(str(path), allow_overwriting_input=True) as pdf:
        for p in upside_down_pages:
            pdf.pages[p - 1].rotate(180, relative=True)
        pdf.save(str(path))
    return str(path)


def test_osd_parser():
    sample = ("Page number: 0\nOrientation in degrees: 180\nRotate: 180\n"
              "Orientation confidence: 14.83\nScript: Latin\n")
    assert _osd(sample) == (180, 14.83)
    assert _osd("Too few characters. Skipping this page") == (None, 0.0)
    assert _osd("Rotate: 270") == (270, 0.0)


@pytest.mark.skipif(not _has_osd(), reason="needs Tesseract with osd data")
def test_auto_rotate_fixes_upside_down_page(tmp_path):
    src = _texty_pdf(tmp_path / "in.pdf", upside_down_pages=(2,))
    out = tmp_path / "out.pdf"
    r = pb.auto_rotate(src, str(out))
    assert r["ok"], r
    assert [f["page"] for f in r["fixed"]] == [2]
    assert r["fixed"][0]["by"] == 180
    # the fix is the lossless /Rotate switch: net rotation back to upright
    with pikepdf.open(str(out)) as pdf:
        assert int(pdf.pages[1].get("/Rotate", 0)) % 360 == 0


@pytest.mark.skipif(not _has_osd(), reason="needs Tesseract with osd data")
def test_auto_rotate_leaves_upright_pages_alone(tmp_path):
    src = _texty_pdf(tmp_path / "in.pdf")
    out = tmp_path / "out.pdf"
    r = pb.auto_rotate(src, str(out))
    assert r["ok"] and r["fixed"] == []
    assert out.stat().st_size > 0


def test_auto_rotate_without_tesseract(monkeypatch, tmp_path):
    _texty_pdf(tmp_path / "in.pdf")
    monkeypatch.setattr("shutil.which", lambda n: None)
    r = pb.auto_rotate(str(tmp_path / "in.pdf"), str(tmp_path / "o.pdf"))
    assert not r["ok"] and r["code"] == "missing_dep"
