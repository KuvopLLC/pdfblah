"""Turn a combine into a binder: a generated table-of-contents page with clickable
entries, an outline bookmark per document, and staggered numbered tabs on each
section's first page, like a physical ring binder.

The ToC is drawn fresh with reportlab in a standard face (sans = Helvetica, serif =
Times). It deliberately does NOT try to reuse fonts embedded in the source documents:
those are usually subsets that can't render arbitrary new text, the same reason text
edits refuse them."""
import os
from io import BytesIO

import pikepdf

FONTS = {
    "sans": ("Helvetica", "Helvetica-Bold"),
    "serif": ("Times-Roman", "Times-Bold"),
}

# layout constants (pt)
MARGIN = 64
TITLE_SIZE = 30
ROW_H = 27
ROW_SIZE = 12.5
BADGE = 19            # tab-number badge square
TAB_W, TAB_H = 54, 26  # the edge tab on section pages
INK = (0.10, 0.10, 0.10)
MUTE = (0.55, 0.53, 0.50)
PAPER_LINE = (0.88, 0.86, 0.83)


def page_size(page):
    mb = [float(v) for v in (page.MediaBox if "/MediaBox" in page else [0, 0, 612, 792])]
    return mb[2] - mb[0], mb[3] - mb[1]


JUNK_TITLES = {"untitled", "unknown", "document", "pdf", "slide 1"}


def title_for(path, doc_title=None, given=None):
    """Pick a section title: explicit > the file's DocInfo Title > a cleaned filename.
    Boilerplate titles that authoring tools stamp by default don't count."""
    if given:
        return str(given)
    t = str(doc_title or "").strip()
    if t and t.lower() not in JUNK_TITLES and not t.lower().startswith("microsoft word - "):
        return t
    stem = os.path.splitext(os.path.basename(path))[0]
    return stem.replace("_", " ").replace("-", " ").strip() or "Untitled"


def _rows_per_page(page_h, first):
    top = MARGIN + (TITLE_SIZE + 46 if first else 18)
    return max(1, int((page_h - top - MARGIN) // ROW_H))


def toc_page_count(n_entries, page_h):
    pages, left, first = 0, n_entries, True
    while left > 0:
        left -= _rows_per_page(page_h, first)
        pages += 1
        first = False
    return pages


def draw_toc(entries, page_w, page_h, toc_title="Contents", font="sans"):
    """Draw the ToC pages. `entries` = [(number, title, dest_page_label)] with the label
    already final (1-based, counting the ToC pages themselves). Returns (pdf_bytes,
    link_rects) where link_rects = [(toc_page_index, x1, y1, x2, y2, entry_index)]."""
    from reportlab.lib.utils import simpleSplit  # noqa: F401  (import proves reportlab present)
    from reportlab.pdfbase.pdfmetrics import stringWidth
    from reportlab.pdfgen import canvas

    body, bold = FONTS.get(font, FONTS["sans"])
    buf = BytesIO()
    c = canvas.Canvas(buf, pagesize=(page_w, page_h))
    rects = []
    i, first = 0, True
    while i < len(entries):
        y = page_h - MARGIN
        if first:
            c.setFillColorRGB(*INK)
            c.setFont(bold, TITLE_SIZE)
            c.drawString(MARGIN, y - TITLE_SIZE, toc_title)
            c.setStrokeColorRGB(*INK); c.setLineWidth(1.4)
            c.line(MARGIN, y - TITLE_SIZE - 14, page_w - MARGIN, y - TITLE_SIZE - 14)
            y -= TITLE_SIZE + 46
        else:
            y -= 18
        rows = _rows_per_page(page_h, first)
        for _ in range(rows):
            if i >= len(entries):
                break
            num, title, label = entries[i]
            base = y - ROW_H + (ROW_H - ROW_SIZE) / 2
            # the tab-number badge
            c.setFillColorRGB(*INK)
            c.roundRect(MARGIN, base - 3.5, BADGE, BADGE, 4, stroke=0, fill=1)
            c.setFillColorRGB(1, 1, 1)
            c.setFont(bold, 10)
            c.drawCentredString(MARGIN + BADGE / 2, base + 1.5, str(num))
            # title, ellipsized to leave room for the page number + leader
            label_s = str(label)
            c.setFont(body, ROW_SIZE)
            num_w = stringWidth(label_s, body, ROW_SIZE)
            tx = MARGIN + BADGE + 14
            avail = (page_w - MARGIN) - num_w - 24 - tx
            t = title
            while t and stringWidth(t, body, ROW_SIZE) > avail:
                t = t[:-1]
            if t != title:
                t = t.rstrip() + "…"
            c.setFillColorRGB(*INK)
            c.drawString(tx, base, t)
            # dot leader in the gap, page number flush right
            c.setFillColorRGB(*MUTE)
            lead_x0 = tx + stringWidth(t, body, ROW_SIZE) + 8
            lead_x1 = (page_w - MARGIN) - num_w - 10
            if lead_x1 > lead_x0:
                dots = int((lead_x1 - lead_x0) / 5)
                c.setFont(body, ROW_SIZE - 2)
                c.drawString(lead_x0, base, " " + ". " * max(0, dots // 2))
            c.setFont(body, ROW_SIZE)
            c.setFillColorRGB(*INK)
            c.drawRightString(page_w - MARGIN, base, label_s)
            # a faint row rule, and the clickable rect covering the whole row
            c.setStrokeColorRGB(*PAPER_LINE); c.setLineWidth(0.6)
            c.line(MARGIN, y - ROW_H + 2, page_w - MARGIN, y - ROW_H + 2)
            rects.append((c.getPageNumber() - 1, MARGIN, y - ROW_H + 2, page_w - MARGIN, y, i))
            y -= ROW_H
            i += 1
        c.showPage()
        first = False
    c.save()
    return buf.getvalue(), rects


def draw_tab(number, page_w, page_h, slot, slots, font="sans"):
    """A binder-style edge tab for a section's first page: a dark rounded label at the
    right edge, staggered down the page by slot (like physical index tabs)."""
    from reportlab.pdfgen import canvas

    _, bold = FONTS.get(font, FONTS["sans"])
    buf = BytesIO()
    c = canvas.Canvas(buf, pagesize=(page_w, page_h))
    span = page_h - 2 * MARGIN - TAB_H
    y = page_h - MARGIN - TAB_H - (span * (slot % slots) / max(1, slots - 1) if slots > 1 else 0)
    x = page_w - TAB_W
    c.setFillColorRGB(*INK)
    c.roundRect(x, y, TAB_W + 8, TAB_H, 6, stroke=0, fill=1)  # bleeds off the edge
    c.setFillColorRGB(1, 1, 1)
    c.setFont(bold, 10.5)
    c.drawCentredString(x + TAB_W / 2 - 1, y + TAB_H / 2 - 3.5, f"TAB {number}")
    c.showPage()
    c.save()
    return buf.getvalue()


def add_links(dst, rects, entries, section_pages):
    """Clickable rows: a /Link annotation per ToC row, pointing at its section start."""
    for toc_idx, x1, y1, x2, y2, entry_i in rects:
        target = dst.pages[section_pages[entry_i]]
        annot = dst.make_indirect(pikepdf.Dictionary(
            Type=pikepdf.Name.Annot, Subtype=pikepdf.Name.Link,
            Rect=[x1, y1, x2, y2], Border=[0, 0, 0],
            Dest=[target.obj, pikepdf.Name.Fit],
        ))
        page = dst.pages[toc_idx]
        if "/Annots" not in page:
            page.Annots = dst.make_indirect(pikepdf.Array())
        page.Annots.append(annot)


def add_outline(dst, entries, section_pages, toc_pages):
    """One bookmark per document, plus one for the ToC itself."""
    from pikepdf import OutlineItem

    with dst.open_outline() as outline:
        if toc_pages:
            outline.root.append(OutlineItem("Contents", 0))
        for i, (num, title, _label) in enumerate(entries):
            outline.root.append(OutlineItem(f"{num}. {title}", section_pages[i]))
