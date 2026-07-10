"""combine(toc=True): the binder output. A generated Contents page whose rows really
click through (link annotations resolve to each document's first page), an outline
bookmark per document, and staggered numbered edge tabs on section first pages."""
import numpy as np
import pikepdf
import pypdfium2 as pdfium
import pytest
from reportlab.pdfgen import canvas

from pdfblah.binder import title_for, toc_page_count
from pdfblah.organize import combine


def make_doc(path, heading, pages=2, title=None):
    c = canvas.Canvas(str(path))
    for i in range(pages):
        c.setFont("Helvetica-Bold", 18); c.drawString(72, 750, f"{heading} p{i + 1}")
        c.showPage()
    c.save()
    if title:
        with pikepdf.open(str(path), allow_overwriting_input=True) as pdf:
            pdf.docinfo["/Title"] = title
            pdf.save(str(path))
    return str(path)


@pytest.fixture()
def packet(tmp_path):
    return [
        make_doc(tmp_path / "01_agenda.pdf", "Agenda", 1, title="Meeting Agenda"),
        make_doc(tmp_path / "02-q2_financials.pdf", "Financials", 4),  # junk reportlab Title
        make_doc(tmp_path / "03_minutes.pdf", "Minutes", 2, title="Prior Minutes"),
    ]


def link_targets(pdf):
    """The ToC rows' destination page indices, in row order (top of page first)."""
    idx = {pdf.pages[i].obj.objgen: i for i in range(len(pdf.pages))}
    annots = sorted(pdf.pages[0].get("/Annots", []), key=lambda a: -float(a.Rect[1]))
    return [idx[a.Dest[0].objgen] for a in annots]


def test_binder_toc_links_and_outline(packet, tmp_path):
    out = tmp_path / "packet.pdf"
    r = combine(packet, str(out), toc=True)
    assert r["ok"] and r["toc_pages"] == 1 and r["pages"] == 1 + 1 + 4 + 2
    assert [s["title"] for s in r["sections"]] == [
        "Meeting Agenda", "02 q2 financials", "Prior Minutes"]  # docinfo > junk > filename
    assert [s["page"] for s in r["sections"]] == [2, 3, 7]
    with pikepdf.open(str(out)) as pdf:
        assert link_targets(pdf) == [1, 2, 6]  # 0-based: rows land on section starts
        with pdf.open_outline() as ol:
            titles = [it.title for it in ol.root]
        assert titles[0] == "Contents" and titles[1] == "1. Meeting Agenda" and len(titles) == 4


def test_titles_override_and_serif(packet, tmp_path):
    out = tmp_path / "packet.pdf"
    r = combine(packet, str(out), toc=True, titles=["Tab One", "Tab Two", "Tab Three"],
                toc_font="serif", toc_title="Board Materials")
    assert [s["title"] for s in r["sections"]] == ["Tab One", "Tab Two", "Tab Three"]
    doc = pdfium.PdfDocument(str(out))
    text = doc[0].get_textpage().get_text_range()
    doc.close()
    assert "Board Materials" in text and "Tab Two" in text


def test_tabs_are_drawn_on_section_starts(packet, tmp_path):
    out = tmp_path / "packet.pdf"
    r = combine(packet, str(out), toc=True, tabs=True)
    assert r["tabs"] is True
    doc = pdfium.PdfDocument(str(out))
    for s in r["sections"]:
        arr = np.asarray(doc[s["page"] - 1].render(scale=1.0).to_pil().convert("L"))
        edge = arr[:, -60:]                       # the right edge strip
        assert (edge < 80).sum() > 200, f"no tab ink on section page {s['page']}"
    # a non-section page has a clean right edge
    arr = np.asarray(doc[3].render(scale=1.0).to_pil().convert("L"))
    assert (arr[:, -60:] < 80).sum() == 0
    doc.close()


def test_toc_spills_to_two_pages(tmp_path):
    inputs = [make_doc(tmp_path / f"d{i:02d}.pdf", f"Doc {i}", 1) for i in range(30)]
    out = tmp_path / "big.pdf"
    r = combine(inputs, str(out), toc=True)
    h = 842  # A4-ish reportlab default page height
    assert r["toc_pages"] == toc_page_count(30, h) == 2
    assert r["pages"] == 30 + 2
    assert r["sections"][0]["page"] == 3  # first doc lands after both ToC pages
    with pikepdf.open(str(out)) as pdf:  # rows on page 2 of the ToC still link
        assert len(pdf.pages[1].get("/Annots", [])) > 0


def test_title_for_rules():
    assert title_for("x/final_report-v2.pdf") == "final report v2"
    assert title_for("x/a.pdf", doc_title="Real Title") == "Real Title"
    assert title_for("x/a.pdf", doc_title="untitled") == "a"
    assert title_for("x/a.pdf", doc_title="Microsoft Word - draft.docx") == "a"
    assert title_for("x/a.pdf", doc_title="ignored", given="Given") == "Given"


def test_plain_combine_report_unchanged(packet, tmp_path):
    r = combine(packet, str(tmp_path / "o.pdf"))
    assert r == {"ok": True, "files": 3, "pages": 7, "output": str(tmp_path / "o.pdf")}
