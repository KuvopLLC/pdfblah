import pdfplumber
from reportlab.pdfgen import canvas

from pdfblah.commands import scrub


def make_pdf(path, lines, font="Helvetica", size=14):
    c = canvas.Canvas(str(path), pagesize=(500, 300))
    c.setFont(font, size)
    for i, t in enumerate(lines):
        c.drawString(30, 260 - i * 30, t)
    c.save()
    return str(path)


def alltext(path):
    with pdfplumber.open(str(path)) as pdf:
        return "\n".join((p.extract_text() or "") for p in pdf.pages)


def test_scrub_removes_pii(tmp_path):
    src = make_pdf(tmp_path / "in.pdf", [
        "Contact john@acme.com or 555-123-4567",
        "Card 4242 4242 4242 4242 SSN 123-45-6789",
        "Invoice 12345678 total 3 items",
    ])
    o = str(tmp_path / "o.pdf")
    r = scrub(src, o)
    assert r["ok"] and r["scrubbed"] >= 4
    t = alltext(o)
    assert "john@acme.com" not in t
    assert "555-123-4567" not in t
    assert "4242 4242 4242 4242" not in t
    assert "123-45-6789" not in t
    assert "12345678" in t                 # not PII, untouched


def test_scrub_mask(tmp_path):
    src = make_pdf(tmp_path / "in.pdf", ["email a.b@example.com here"])
    o = str(tmp_path / "o.pdf")
    r = scrub(src, o, types=["email"], mask="[redacted]")
    t = alltext(o)
    assert "a.b@example.com" not in t and "[redacted]" in t


def test_scrub_keeps_invalid_card(tmp_path):
    src = make_pdf(tmp_path / "in.pdf", ["num 4242 4242 4242 4241 end"])   # bad Luhn
    o = str(tmp_path / "o.pdf")
    r = scrub(src, o, types=["credit_card"])
    assert r["scrubbed"] == 0
    assert "4242 4242 4242 4241" in alltext(o)


def test_scrub_no_pii_is_noop(tmp_path):
    src = make_pdf(tmp_path / "in.pdf", ["The quick brown fox. Chapter 3."])
    o = str(tmp_path / "o.pdf")
    r = scrub(src, o)
    assert r["ok"] and r["scrubbed"] == 0
    assert "quick brown fox" in alltext(o)


def test_scrub_report_has_no_values(tmp_path):
    src = make_pdf(tmp_path / "in.pdf", ["ssn 123-45-6789"])
    o = str(tmp_path / "o.pdf")
    r = scrub(src, o, types=["ssn"])
    # report must not leak the actual sensitive value
    assert all("123-45-6789" not in str(v) for it in r["items"] for v in it.values())
