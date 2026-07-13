"""Tests for split --at (pattern splitting with rename templates) and
split --spread (two-page scans cut apart)."""
import pikepdf
from reportlab.pdfgen import canvas

import pdfblah as pb


def _invoices_pdf(path):
    """Cover page, then three invoices of 1-2 pages each."""
    c = canvas.Canvas(str(path), pagesize=(400, 300))
    texts = ["cover sheet, no invoice here",
             "Invoice #ACME-100 first page", "ACME-100 second page",
             "Invoice #BETA-7 single page",
             "Invoice #ACME-101 first page", "ACME-101 second page"]
    for t in texts:
        c.setFont("Helvetica", 11)
        c.drawString(20, 150, t)
        c.showPage()
    c.save()
    return str(path)


def test_split_at_with_capture_names(tmp_path):
    src = _invoices_pdf(tmp_path / "all.pdf")
    out = tmp_path / "parts"
    r = pb.split_at(src, str(out), r"Invoice #(\S+)", name="{1}.pdf")
    assert r["ok"], r
    names = sorted(p.name for p in out.iterdir())
    assert names == ["ACME-100.pdf", "ACME-101.pdf", "BETA-7.pdf", "front.pdf"]
    by = {p["file"].split("/")[-1]: p for p in r["parts"]}
    assert by["front.pdf"]["pages"] == 1
    assert by["ACME-100.pdf"]["from"] == 2 and by["ACME-100.pdf"]["to"] == 3
    assert by["BETA-7.pdf"]["pages"] == 1
    assert by["ACME-101.pdf"]["to"] == 6


def test_split_at_sequence_template_and_no_match(tmp_path):
    src = _invoices_pdf(tmp_path / "all.pdf")
    r = pb.split_at(src, str(tmp_path / "n"), r"Invoice #", name="part-{n}-p{page}.pdf")
    assert r["ok"]
    assert sorted(p.name for p in (tmp_path / "n").iterdir()) == [
        "front.pdf", "part-1-p2.pdf", "part-2-p4.pdf", "part-3-p5.pdf"]
    r = pb.split_at(src, str(tmp_path / "x"), r"Never On Any Page")
    assert not r["ok"] and r["code"] == "no_match" and "ocr" in r["error"]


def test_split_at_hostile_capture_is_neutered(tmp_path):
    c = canvas.Canvas(str(tmp_path / "in.pdf"), pagesize=(400, 300))
    c.drawString(20, 150, "Invoice #../../etc/passwd end")
    c.showPage()
    c.save()
    out = tmp_path / "parts"
    r = pb.split_at(str(tmp_path / "in.pdf"), str(out), r"Invoice #(\S+)", name="{1}.pdf")
    assert r["ok"]
    (only,) = list(out.iterdir())
    assert "/" not in only.name and only.parent == out


def test_split_spread_landscape(tmp_path):
    c = canvas.Canvas(str(tmp_path / "book.pdf"), pagesize=(600, 300))
    for spread in range(2):
        c.setFont("Helvetica", 12)
        c.drawString(60, 150, f"left page {2 * spread + 1}")
        c.drawString(360, 150, f"right page {2 * spread + 2}")
        c.showPage()
    c.save()
    out = tmp_path / "pages.pdf"
    r = pb.split_spread(str(tmp_path / "book.pdf"), str(out))
    assert r["ok"] and r["pages_in"] == 2 and r["pages_out"] == 4
    import pdfplumber
    # crop to each page's own box: pdfplumber doesn't clip chars to the
    # mediabox on its own (viewers do)
    with pdfplumber.open(str(out)) as pdf:
        texts = [(p.crop(p.bbox).extract_text() or "").strip() for p in pdf.pages]
    assert texts == ["left page 1", "right page 2", "left page 3", "right page 4"]
    with pikepdf.open(str(out)) as pdf:
        w = [float(p.MediaBox[2]) - float(p.MediaBox[0]) for p in pdf.pages]
    assert all(abs(x - 300) < 0.1 for x in w)


def test_split_spread_rl_order(tmp_path):
    c = canvas.Canvas(str(tmp_path / "manga.pdf"), pagesize=(600, 300))
    c.drawString(60, 150, "west")
    c.drawString(360, 150, "east")
    c.showPage()
    c.save()
    r = pb.split_spread(str(tmp_path / "manga.pdf"), str(tmp_path / "o.pdf"), order="rl")
    assert r["ok"]
    import pdfplumber
    with pdfplumber.open(str(tmp_path / "o.pdf")) as pdf:
        texts = [(p.crop(p.bbox).extract_text() or "").strip() for p in pdf.pages]
    assert texts == ["east", "west"]


def test_split_cli_at(tmp_path, capsys):
    from pdfblah.cli import main
    src = _invoices_pdf(tmp_path / "all.pdf")
    assert main(["split", src, "-o", str(tmp_path / "out"),
                 "--at", r"Invoice #(\S+)", "--name", "{1}.pdf"]) == 0
    out = capsys.readouterr().out
    assert "ACME-100.pdf" in out and "(pages 2-3)" in out
