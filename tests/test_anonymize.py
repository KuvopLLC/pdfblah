import re

import pdfplumber
from reportlab.pdfgen import canvas

from pdfblah.commands import anonymize


def make_pdf(path, lines, font="Helvetica", size=13):
    c = canvas.Canvas(str(path), pagesize=(600, 300))
    c.setFont(font, size)
    for i, t in enumerate(lines):
        c.drawString(30, 260 - i * 30, t)
    c.save()
    return str(path)


def alltext(path):
    with pdfplumber.open(str(path)) as pdf:
        return "\n".join((p.extract_text() or "") for p in pdf.pages)


def test_anonymize_replaces_structured(tmp_path):
    src = make_pdf(tmp_path / "in.pdf", ["email real.person@corp.com card 4242 4242 4242 4242"])
    o = str(tmp_path / "o.pdf")
    r = anonymize(src, o, seed=1)
    t = alltext(o)
    assert "real.person@corp.com" not in t and "4242 4242 4242 4242" not in t
    assert re.search(r"\S+@\S+\.\S+", t)                      # still looks like an email
    assert re.search(r"\d{4} \d{4} \d{4} \d{4}", t)            # same card shape


def test_anonymize_deterministic(tmp_path):
    src = make_pdf(tmp_path / "in.pdf", ["mail a@b.com and 555-123-4567"])
    o1 = str(tmp_path / "o1.pdf"); o2 = str(tmp_path / "o2.pdf")
    anonymize(src, o1, seed=42)
    anonymize(src, o2, seed=42)
    assert alltext(o1) == alltext(o2)


def test_anonymize_consistent(tmp_path):
    src = make_pdf(tmp_path / "in.pdf", ["send to same@corp.com and same@corp.com"])
    o = str(tmp_path / "o.pdf")
    anonymize(src, o, types=["email"], seed=7)
    t = alltext(o)
    assert "same@corp.com" not in t
    emails = set(re.findall(r"[\w.]+@[\w.]+\.\w+", t))
    assert len(emails) == 1                                    # both map to one fake


def test_anonymize_names(tmp_path):
    src = make_pdf(tmp_path / "in.pdf", ["From Alison Cohen, signed Alison Cohen"])
    o = str(tmp_path / "o.pdf")
    r = anonymize(src, o, types=[], names=["Alison Cohen"], seed=5)
    assert r["anonymized"] == 1
    assert "Alison Cohen" not in alltext(o)


def test_anonymize_card_shape_preserved(tmp_path):
    src = make_pdf(tmp_path / "in.pdf", ["card 4242 4242 4242 4242 end"])
    o = str(tmp_path / "o.pdf")
    anonymize(src, o, types=["credit_card"], seed=1)
    t = alltext(o)
    m = re.search(r"\d{4} \d{4} \d{4} \d{4}", t)
    assert m and m.group(0) != "4242 4242 4242 4242"
