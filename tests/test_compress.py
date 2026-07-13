"""Tests for compress: image recompression with a hard target size.

Image-based, so they need Pillow (test extra installs it)."""
import importlib.util
import io

import pytest
from reportlab.pdfgen import canvas

import pdfblah as pb

needs_pil = pytest.mark.skipif(not importlib.util.find_spec("PIL"),
                               reason="needs Pillow (app/test extra)")


def _photo_pdf(path, px=1600, pages=1):
    """A PDF with real text plus one big noisy photo per page (noise defeats
    Flate, so the image dominates the file size like a real scan/photo does)."""
    import random
    from PIL import Image
    rnd = random.Random(7)
    img = Image.new("RGB", (px, px))
    img.putdata([(rnd.randrange(256), rnd.randrange(256), rnd.randrange(256))
                 for _ in range(px * px)])
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    buf.seek(0)
    from reportlab.lib.utils import ImageReader
    c = canvas.Canvas(str(path), pagesize=(612, 792))
    for _ in range(pages):
        c.setFont("Helvetica", 14)
        c.drawString(40, 760, "Quarterly Report with a big photo")
        c.drawImage(ImageReader(buf), 36, 100, width=540, height=540)
        c.showPage()
    c.save()
    return str(path)


def _text_of(path):
    import pdfplumber
    with pdfplumber.open(str(path)) as pdf:
        return pdf.pages[0].extract_text() or ""


@needs_pil
def test_compress_shrinks_and_keeps_text(tmp_path):
    src = _photo_pdf(tmp_path / "in.pdf")
    out = tmp_path / "out.pdf"
    r = pb.compress(src, str(out))
    assert r["ok"], r
    assert r["bytes_after"] < r["bytes_before"] * 0.7
    assert r["images_recompressed"] >= 1
    assert "Quarterly Report" in _text_of(out)


@needs_pil
def test_compress_hits_a_reachable_target(tmp_path):
    src = _photo_pdf(tmp_path / "in.pdf")
    out = tmp_path / "out.pdf"
    import os
    target = os.path.getsize(src) // 3
    r = pb.compress(src, str(out), target_bytes=target)
    assert r["ok"], r
    assert r["bytes_after"] <= target
    assert r["attempts"] and r["attempts"][-1]["bytes"] == r["bytes_after"]
    assert "Quarterly Report" in _text_of(out)


@needs_pil
def test_compress_misses_impossible_target_honestly(tmp_path):
    src = _photo_pdf(tmp_path / "in.pdf")
    out = tmp_path / "out.pdf"
    r = pb.compress(src, str(out), target_bytes=1024)  # 1 KB: impossible
    assert not r["ok"] and r["code"] == "target_missed"
    assert out.exists() and out.stat().st_size == r["bytes_after"]
    assert "smallest achievable" in r["error"]
    assert len(r["attempts"]) == len(__import__("pdfblah.compress", fromlist=["LADDER"]).LADDER)


@needs_pil
def test_compress_grayscale(tmp_path):
    import pikepdf
    src = _photo_pdf(tmp_path / "in.pdf")
    out = tmp_path / "out.pdf"
    r = pb.compress(src, str(out), grayscale=True)
    assert r["ok"] and r["images_recompressed"] >= 1
    with pikepdf.open(str(out)) as pdf:
        spaces = [str(raw.ColorSpace) for page in pdf.pages
                  for _, raw in dict(page.get_images()).items() if "/ColorSpace" in raw]
    assert "/DeviceGray" in spaces


@needs_pil
def test_compress_leaves_transparent_images_alone(tmp_path):
    # an RGBA image lands in the PDF with an SMask; compress must not touch it
    from PIL import Image
    from reportlab.lib.utils import ImageReader
    img = Image.new("RGBA", (400, 400), (200, 40, 40, 128))
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    buf.seek(0)
    c = canvas.Canvas(str(tmp_path / "in.pdf"), pagesize=(612, 792))
    c.drawImage(ImageReader(buf), 100, 300, width=200, height=200, mask="auto")
    c.showPage()
    c.save()
    r = pb.compress(str(tmp_path / "in.pdf"), str(tmp_path / "out.pdf"))
    assert r["ok"]
    assert r["images_recompressed"] == 0


def test_parse_bytes():
    from pdfblah.cli_tools import _parse_bytes
    assert _parse_bytes("200kb") == 200 * 1024
    assert _parse_bytes("1.5mb") == int(1.5 * 1024 * 1024)
    assert _parse_bytes("300000") == 300000
    assert _parse_bytes("2M") == 2 * 1024 * 1024
    with pytest.raises(SystemExit):
        _parse_bytes("huge")
