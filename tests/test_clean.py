"""Clean-scan: dirty scanned pages come out with pure #FFFFFF paper and crisp ink.

The fixture fakes a bad scan of sheet music: gray paper with a lighting gradient, a
coffee stain, and black staff lines + noteheads. clean() must push nearly all paper
to exactly 255 while the ink survives."""
import numpy as np
import pypdfium2 as pdfium
import pytest
from PIL import Image, ImageDraw, ImageFilter

from pdfblah.clean import clean, STRENGTHS


def make_dirty_scan(path, pages=1):
    """A gray, unevenly lit page with a stain and sheet-music-ish black marks."""
    imgs = []
    for _ in range(pages):
        img = Image.new("L", (600, 800), 210)
        # lighting gradient: darker toward the bottom-right
        grad = np.fromfunction(lambda y, x: 210 - (x + y) / 28, (800, 600))
        img = Image.fromarray(np.clip(grad, 0, 255).astype(np.uint8), "L")
        dr = ImageDraw.Draw(img)
        dr.ellipse([380, 520, 560, 660], fill=185)          # the coffee stain
        for y in range(120, 700, 90):                        # staves
            for k in range(5):
                dr.line([60, y + k * 8, 540, y + k * 8], fill=0, width=2)
            dr.ellipse([150, y + 8, 166, y + 20], fill=0)    # noteheads
            dr.ellipse([300, y + 16, 316, y + 28], fill=0)
        imgs.append(img.filter(ImageFilter.GaussianBlur(0.6)).convert("RGB"))
    imgs[0].save(str(path), "PDF", save_all=True, append_images=imgs[1:], resolution=72.0)
    return str(path)


def render_gray(pdf_path, page=0, dpi=100):
    doc = pdfium.PdfDocument(str(pdf_path))
    arr = np.asarray(doc[page].render(scale=dpi / 72).to_pil().convert("L"))
    doc.close()
    return arr


def white_pct(arr):
    return 100.0 * (arr == 255).sum() / arr.size


def test_clean_whitens_paper_and_keeps_ink(tmp_path):
    src = make_dirty_scan(tmp_path / "dirty.pdf")
    out = tmp_path / "clean.pdf"
    r = clean(src, str(out), dpi=150)
    assert r["ok"] and r["pages"] == 1 and r["dpi"] == 150 and r["strength"] == "standard"
    assert r["white_pct"] > 85.0                       # was ~0%: every pixel gray
    arr = render_gray(out)
    # the round-trip re-render resamples, so "pure white" is near-white here
    assert 100.0 * (arr >= 250).sum() / arr.size > 85.0
    assert (arr < 60).sum() > 2000                     # the notation is still there
    before = render_gray(src)
    assert white_pct(before) < 1.0                     # and the fixture really was dirty


def test_strengths_are_ordered(tmp_path):
    src = make_dirty_scan(tmp_path / "dirty.pdf")
    pcts = {}
    for s in STRENGTHS:
        r = clean(src, str(tmp_path / f"{s}.pdf"), dpi=100, strength=s)
        assert r["ok"]
        pcts[s] = r["white_pct"]
    assert pcts["gentle"] <= pcts["standard"] <= pcts["strong"]


def test_bilevel_is_pure_black_and_white(tmp_path):
    src = make_dirty_scan(tmp_path / "dirty.pdf")
    out = tmp_path / "bi.pdf"
    r = clean(src, str(out), dpi=100, bilevel=True)
    assert r["ok"] and r["bilevel"] is True
    import pikepdf
    with pikepdf.open(str(out)) as pdf:  # a true 1-bit image, not gray dressed up
        img = next(iter(pdf.pages[0].images.values()))
        assert int(img.BitsPerComponent) == 1


def test_multipage_and_dpi_clamp(tmp_path):
    src = make_dirty_scan(tmp_path / "dirty3.pdf", pages=3)
    r = clean(src, str(tmp_path / "o.pdf"), dpi=9999)
    assert r["ok"] and r["pages"] == 3 and r["dpi"] == 600
    doc = pdfium.PdfDocument(str(tmp_path / "o.pdf"))
    assert len(doc) == 3
    doc.close()


def test_bad_strength_refuses(tmp_path):
    src = make_dirty_scan(tmp_path / "dirty.pdf")
    r = clean(src, str(tmp_path / "o.pdf"), strength="nuclear")
    assert r["ok"] is False and "strength" in r["error"]


def test_clean_as_workbench_action(tmp_path):
    """The stack route: {"action": "clean"} through apply_actions."""
    from pdfblah.app import apply_actions
    src = make_dirty_scan(tmp_path / "dirty.pdf")
    out = tmp_path / "o.pdf"
    rep = apply_actions(src, str(out), [{"action": "clean", "dpi": 120, "strength": "strong"}])
    assert rep["applied"] == 1
    arr = render_gray(out)
    assert 100.0 * (arr >= 250).sum() / arr.size > 85.0
