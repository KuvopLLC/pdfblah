"""Tests for access: the mechanical accessibility checks."""
import pikepdf
from reportlab.pdfgen import canvas

import pdfblah as pb


def _plain_pdf(path, pages=2, text="ordinary body text"):
    c = canvas.Canvas(str(path), pagesize=(400, 300))
    for _ in range(pages):
        if text:
            c.setFont("Helvetica", 12)
            c.drawString(30, 150, text)
        c.showPage()
    c.save()
    return str(path)


def _tagged_pdf(path):
    """A minimally tagged PDF: MarkInfo + a structure tree with two figures,
    one carrying alt text, plus /Lang and a displayed title."""
    _plain_pdf(path)
    with pikepdf.open(str(path), allow_overwriting_input=True) as pdf:
        fig_ok = pdf.make_indirect(pikepdf.Dictionary(
            S=pikepdf.Name("/Figure"), Alt=pikepdf.String("a chart of results")))
        fig_bad = pdf.make_indirect(pikepdf.Dictionary(S=pikepdf.Name("/Figure")))
        sect = pdf.make_indirect(pikepdf.Dictionary(
            S=pikepdf.Name("/Sect"), K=pikepdf.Array([fig_ok, fig_bad])))
        pdf.Root.StructTreeRoot = pdf.make_indirect(pikepdf.Dictionary(
            Type=pikepdf.Name("/StructTreeRoot"), K=sect))
        pdf.Root.MarkInfo = pdf.make_indirect(pikepdf.Dictionary(Marked=True))
        pdf.Root.Lang = pikepdf.String("en-US")
        pdf.Root.ViewerPreferences = pdf.make_indirect(
            pikepdf.Dictionary(DisplayDocTitle=True))
        pdf.docinfo["/Title"] = "Quarterly Results"
        pdf.save(str(path))
    return str(path)


def test_untagged_pdf_fails_plainly(tmp_path):
    src = _plain_pdf(tmp_path / "in.pdf")
    r = pb.check_access(src)
    assert r["ok"]
    c = r["checks"]
    assert c["tagged"] is False and c["language"] is None
    assert c["text_layer"] is True
    assert any("not tagged" in p for p in r["problems"])
    assert any("/Lang" in p or "language" in p for p in r["problems"])
    assert "veraPDF" in r["note"]


def test_tagged_pdf_counts_figures_and_alt(tmp_path):
    src = _tagged_pdf(tmp_path / "in.pdf")
    r = pb.check_access(src)
    c = r["checks"]
    assert c["tagged"] is True
    assert c["language"] == "en-US"
    assert c["title"] == "Quarterly Results" and c["display_title"] is True
    assert c["figures"] == {"total": 2, "with_alt": 1}
    assert any("1 of 2 tagged figure" in p for p in r["problems"])


def test_scan_with_no_text_layer(tmp_path):
    src = _plain_pdf(tmp_path / "scan.pdf", text=None)
    r = pb.check_access(src)
    assert r["checks"]["text_layer"] is False
    assert any("ocr" in p for p in r["problems"])


def test_long_doc_without_bookmarks_is_advisory(tmp_path):
    src = _plain_pdf(tmp_path / "long.pdf", pages=25)
    r = pb.check_access(src)
    assert any("bookmarks" in a for a in r["advisories"])


def test_access_cli(tmp_path, capsys):
    from pdfblah.cli import main
    src = _tagged_pdf(tmp_path / "in.pdf")
    rc = main(["access", src])
    out = capsys.readouterr().out
    assert rc == 1  # the alt-less figure is a problem
    assert "tagged: yes" in out and "language: en-US" in out
    assert "1 of 2 have alt text" in out
    # a fully passing doc exits 0: give the bad figure alt text
    with pikepdf.open(src, allow_overwriting_input=True) as pdf:
        sect = pdf.Root.StructTreeRoot.K
        for fig in sect.K:
            if fig.get("/Alt") is None:
                fig.Alt = pikepdf.String("second image")
        pdf.save(src)
    capsys.readouterr()
    assert main(["access", src]) == 0
