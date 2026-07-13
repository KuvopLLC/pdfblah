"""Tests for links: internal jumps, bookmarks, and external URLs.

External probing is faked; nothing here touches the network."""
import pikepdf
import pytest
from reportlab.pdfgen import canvas

import pdfblah as pb


def _linked_pdf(path):
    """3 pages. Page 1 carries: a good internal link to page 3, a link to a
    named destination that exists, one to a name that doesn't, and an external
    URL. Bookmarks: one good, one pointing at the missing name."""
    c = canvas.Canvas(str(path), pagesize=(400, 300))
    for i in range(3):
        c.setFont("Helvetica", 12)
        c.drawString(30, 150, f"page {i + 1}")
        c.showPage()
    c.save()
    with pikepdf.open(str(path), allow_overwriting_input=True) as pdf:
        p3 = pdf.pages[2].obj
        # a real named destination, old-style /Dests dictionary
        pdf.Root.Dests = pdf.make_indirect(pikepdf.Dictionary({
            "/chapter-end": pikepdf.Array([p3, pikepdf.Name("/Fit")])}))

        def link(rect, **a_or_dest):
            return pdf.make_indirect(pikepdf.Dictionary(
                Type=pikepdf.Name("/Annot"), Subtype=pikepdf.Name("/Link"),
                Rect=rect, **a_or_dest))

        good_explicit = link([10, 10, 60, 30],
                             Dest=pikepdf.Array([p3, pikepdf.Name("/Fit")]))
        good_named = link([10, 40, 60, 60], Dest=pikepdf.String("chapter-end"))
        bad_named = link([10, 70, 60, 90], Dest=pikepdf.String("no-such-place"))
        ext = link([10, 100, 60, 120], A=pikepdf.Dictionary(
            S=pikepdf.Name("/URI"), URI=pikepdf.String("https://example.com/x")))
        pdf.pages[0]["/Annots"] = pdf.make_indirect(
            pikepdf.Array([good_explicit, good_named, bad_named, ext]))

        good_bm = pdf.make_indirect(pikepdf.Dictionary(
            Title=pikepdf.String("The End"),
            Dest=pikepdf.Array([p3, pikepdf.Name("/Fit")])))
        bad_bm = pdf.make_indirect(pikepdf.Dictionary(
            Title=pikepdf.String("Ghost Chapter"),
            Dest=pikepdf.String("vanished")))
        good_bm.Next = bad_bm
        pdf.Root.Outlines = pdf.make_indirect(pikepdf.Dictionary(
            Type=pikepdf.Name("/Outlines"), First=good_bm, Last=bad_bm))
        pdf.save(str(path))
    return str(path)


def test_internal_and_bookmarks(tmp_path):
    src = _linked_pdf(tmp_path / "in.pdf")
    r = pb.check_links(src, external=False)
    assert r["ok"]
    assert r["internal"]["ok"] == 2
    assert [b["problem"] for b in r["internal"]["broken"]] == \
        ["named destination 'no-such-place' does not exist"]
    assert r["bookmarks"]["ok"] == 1
    assert r["bookmarks"]["broken"][0]["bookmark"] == "Ghost Chapter"
    assert r["unchecked_urls"] == 1 and r["checked_urls"] == 0


def test_external_probe_faked(tmp_path, monkeypatch):
    src = _linked_pdf(tmp_path / "in.pdf")
    from pdfblah import links as mod
    monkeypatch.setattr(mod, "_probe", lambda url, t: ("broken", "HTTP 404"))
    r = pb.check_links(src)
    e = r["external_urls"]
    assert e["broken"][0]["url"] == "https://example.com/x"
    assert e["broken"][0]["pages"] == [1]
    monkeypatch.setattr(mod, "_probe", lambda url, t: ("ok", "HTTP 200"))
    assert pb.check_links(src)["external_urls"]["ok"] == 1


def test_dangling_page_reference(tmp_path):
    src = _linked_pdf(tmp_path / "in.pdf")
    with pikepdf.open(src, allow_overwriting_input=True) as pdf:
        del pdf.pages[2]  # the target of the good links vanishes
        pdf.save(src)
    r = pb.check_links(src, external=False)
    problems = [b["problem"] for b in r["internal"]["broken"]]
    assert any("not in the document" in p for p in problems)


def test_probe_rejects_non_http():
    from pdfblah.links import _probe
    assert _probe("ftp://old.example.com/f", 5)[0] == "broken"
    assert _probe("", 5)[0] == "broken"


def test_links_cli(tmp_path, capsys):
    from pdfblah.cli import main
    src = _linked_pdf(tmp_path / "in.pdf")
    rc = main(["links", src, "--offline"])
    out = capsys.readouterr().out
    assert rc == 1  # broken things exist
    assert "internal links: 2 ok, 1 broken" in out
    assert "'Ghost Chapter'" in out
    assert "not contacted (--offline)" in out
