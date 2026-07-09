"""Explicit font substitution: the OPT-IN escape hatch for detect-and-refuse.

Without {"substituteFont": true} a non-reproducible font refuses exactly as before.
With it, the refused match is rewritten in a similar base-14 font, the report says so
({"substituted": {from, to, count}}), and everything the rule didn't touch keeps its
original font bytes."""
import os

import pikepdf
import pdfplumber
import pytest
import reportlab
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.pdfgen import canvas

from pdfblah.app import apply_actions
from pdfblah.engine import pick_substitute


@pytest.fixture()
def vera_pdf(tmp_path):
    """Text in an embedded SUBSET font: replacements with unseen glyphs refuse."""
    try:
        pdfmetrics.getFont("VeraTest")
    except KeyError:
        pdfmetrics.registerFont(TTFont("VeraTest", os.path.join(
            os.path.dirname(reportlab.__file__), "fonts", "Vera.ttf")))
    p = tmp_path / "vera.pdf"
    c = canvas.Canvas(str(p))
    c.setFont("VeraTest", 14)
    c.drawString(72, 700, "Client: Acme Corp")
    c.save()
    return str(p)


RULE = {"action": "replace", "find": "Acme Corp", "scope": "all"}


def test_still_refuses_without_opt_in(vera_pdf, tmp_path):
    rep = apply_actions(vera_pdf, str(tmp_path / "o.pdf"),
                        [dict(RULE, replace="Zebra & Sons GmbH")])
    assert rep["applied"] == 0 and rep["rules"][0]["refused"] is True


def test_opt_in_substitutes_and_reports(vera_pdf, tmp_path):
    out = tmp_path / "o.pdf"
    rep = apply_actions(vera_pdf, str(out),
                        [dict(RULE, replace="Zebra & Sons GmbH", substituteFont=True)])
    e = rep["rules"][0]
    assert rep["applied"] == 1
    assert e["substituted"] == {"from": "BitstreamVeraSans-Roman", "to": "Helvetica", "count": 1}
    with pdfplumber.open(str(out)) as pdf:
        text = pdf.pages[0].extract_text()
        fonts = {c["fontname"] for c in pdf.pages[0].chars}
    assert "Zebra & Sons GmbH" in text and "Acme Corp" not in text
    # the replacement is Helvetica; the text the rule didn't touch keeps the original font
    assert any("Helvetica" in f for f in fonts)
    assert any("BitstreamVeraSans" in f for f in fonts)
    with pikepdf.open(str(out)) as pdf:
        assert any(str(k).startswith("/PBsub") for k in pdf.pages[0].Resources.Font.keys())


def test_safe_fonts_ignore_the_flag(tmp_path):
    p = tmp_path / "helv.pdf"
    c = canvas.Canvas(str(p)); c.setFont("Helvetica", 12)
    c.drawString(72, 700, "Client: Acme Corp"); c.save()
    rep = apply_actions(str(p), str(tmp_path / "o.pdf"),
                        [dict(RULE, replace="Blahco Ltd", substituteFont=True)])
    assert rep["applied"] == 1 and "substituted" not in rep["rules"][0]


def test_unencodable_replacement_still_refuses(vera_pdf, tmp_path):
    rep = apply_actions(vera_pdf, str(tmp_path / "o.pdf"),
                        [dict(RULE, replace="Ω市場", substituteFont=True)])
    e = rep["rules"][0]
    assert e["refused"] is True and "standard encoding" in e["reason"]


def test_pick_substitute_families():
    assert pick_substitute("AAAAAA+BitstreamVeraSans-Roman") == "Helvetica"
    assert pick_substitute("AGaramond-Bold") == "Times-Bold"
    assert pick_substitute("SomeMono-Italic") == "Courier-Oblique"
    assert pick_substitute("FooSansBoldItalic") == "Helvetica-BoldOblique"


CID_PDF = os.path.join(os.path.dirname(__file__), "data", "cidfont.pdf")


def test_cid_fonts_refuse_honestly_and_are_not_substitutable(tmp_path):
    """Identity-H (CID/Type0) fonts store glyph IDs; rewriting and substitution are both
    impossible today, and the report must say so instead of offering a dead end."""
    for sub in (False, True):
        rep = apply_actions(CID_PDF, str(tmp_path / f"o{sub}.pdf"),
                            [dict(RULE, replace="Foo", substituteFont=sub)])
        e = rep["rules"][0]
        assert rep["applied"] == 0
        assert e["refused"] is True and e["substitutable"] is False
        assert "glyph IDs" in e["reason"]
        assert "error" not in e  # never the confusing 'located visually but...' fallback
