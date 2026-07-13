"""Tests for recolor: dark mode, sepia, and ink color schemes."""
import importlib.util

import pytest
from reportlab.pdfgen import canvas

import pdfblah as pb
from pdfblah.recolor import parse_color

needs_render = pytest.mark.skipif(
    not (importlib.util.find_spec("pypdfium2") and importlib.util.find_spec("numpy")),
    reason="needs the app extra")


def _pdf(path):
    c = canvas.Canvas(str(path), pagesize=(300, 200))
    c.setFont("Helvetica-Bold", 40)
    c.drawString(30, 90, "INK INK INK")
    c.showPage()
    c.save()
    return str(path)


def _corner_and_ink(path):
    """(paper RGB from a corner, darkest-ink RGB) of the first page."""
    import numpy as np
    import pypdfium2 as pdfium
    doc = pdfium.PdfDocument(str(path))
    arr = np.asarray(doc[0].render(scale=2).to_pil().convert("RGB"))
    doc.close()
    paper = tuple(int(x) for x in arr[3, 3])
    lum = arr.sum(axis=2).astype(int)
    # ink = the pixel most different from the paper (works in dark mode too)
    y, x = divmod(int(abs(lum - lum[3, 3]).argmax()), lum.shape[1])
    return paper, tuple(int(v) for v in arr[y, x])


def test_parse_color():
    assert parse_color("navy") == (16, 42, 100)
    assert parse_color("#ff0000") == (255, 0, 0)
    assert parse_color("1a2b3c") == (26, 43, 60)
    assert parse_color("chartreuse-ish") is None


@needs_render
def test_dark_scheme(tmp_path):
    src = _pdf(tmp_path / "in.pdf")
    out = tmp_path / "dark.pdf"
    r = pb.recolor(src, str(out), scheme="dark")
    assert r["ok"] and r["pages"] == 1
    paper, ink = _corner_and_ink(out)
    assert max(paper) < 60, paper       # near-black paper, not pure black
    assert min(paper) > 10, paper
    assert min(ink) > 180, ink          # light ink


@needs_render
def test_ink_navy_keeps_white_paper(tmp_path):
    src = _pdf(tmp_path / "in.pdf")
    out = tmp_path / "navy.pdf"
    r = pb.recolor(src, str(out), scheme="ink=navy")
    assert r["ok"]
    paper, ink = _corner_and_ink(out)
    assert min(paper) > 245, paper                      # paper stays white
    assert ink[2] > ink[0] and ink[2] > ink[1], ink     # ink went blue


@needs_render
def test_sepia_scheme(tmp_path):
    src = _pdf(tmp_path / "in.pdf")
    out = tmp_path / "sepia.pdf"
    r = pb.recolor(src, str(out), scheme="sepia")
    assert r["ok"]
    paper, _ = _corner_and_ink(out)
    assert paper[0] > paper[2] and paper[0] > 230, paper  # warm cream paper


@needs_render
def test_bad_scheme_and_color(tmp_path):
    src = _pdf(tmp_path / "in.pdf")
    assert not pb.recolor(src, str(tmp_path / "o.pdf"), scheme="neon")["ok"]
    r = pb.recolor(src, str(tmp_path / "o.pdf"), scheme="ink=vermillionish")
    assert not r["ok"] and "unknown ink color" in r["error"]


@needs_render
def test_recolor_cli(tmp_path, capsys):
    from pdfblah.cli import main
    src = _pdf(tmp_path / "in.pdf")
    assert main(["recolor", src, "-o", str(tmp_path / "o.pdf"), "--scheme", "ink=navy"]) == 0
    assert "re-inked 1 page(s) (ink=navy" in capsys.readouterr().out
