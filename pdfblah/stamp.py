"""Add marks on top of (or under) a PDF: watermarks, stamps, page numbers, and Bates
numbering. These are honest overlays, deliberately drawn over the page, unlike the fake
overlays pdfblah refuses for text edits. The mark is drawn with reportlab and merged onto
each page with pikepdf, so the original content and metadata are untouched underneath."""
import math
import os
from contextlib import ExitStack
from io import BytesIO

import pikepdf

from .organize import parse_ranges

_POS = {
    "center": (0.5, 0.5, "c"), "middle": (0.5, 0.5, "c"),
    "top": (0.5, 1.0, "c"), "top-left": (0.0, 1.0, "l"), "top-center": (0.5, 1.0, "c"), "top-right": (1.0, 1.0, "r"),
    "bottom": (0.5, 0.0, "c"), "bottom-left": (0.0, 0.0, "l"), "bottom-center": (0.5, 0.0, "c"), "bottom-right": (1.0, 0.0, "r"),
    "left": (0.0, 0.5, "l"), "right": (1.0, 0.5, "r"),
}


def _anchor(position, w, h, margin=36):
    fx, fy, halign = _POS.get(position, _POS["center"])
    x = margin + fx * (w - 2 * margin)
    y = margin + fy * (h - 2 * margin)
    return x, y, halign


def _page_size(page):
    mb = [float(v) for v in (page.MediaBox if "/MediaBox" in page else [0, 0, 612, 792])]
    return mb, mb[2] - mb[0], mb[3] - mb[1]


def _make_overlay(w, h, draw):
    from reportlab.pdfgen import canvas as rcanvas
    buf = BytesIO()
    c = rcanvas.Canvas(buf, pagesize=(w, h))
    draw(c)
    c.showPage()
    c.save()
    return buf.getvalue()


def _apply(input_path, output_path, draw_for, pages, under=False, per_page=False):
    """draw_for(canvas, w, h, page_index, total) draws the mark. Overlays are cached by
    page size unless per_page (text that changes per page, like numbers)."""
    with pikepdf.open(input_path) as pdf:
        n = len(pdf.pages)
        idxs = sorted(set(parse_ranges(pages, n)))
        cache = {}
        with ExitStack() as stack:
            for pi in idxs:
                page = pdf.pages[pi]
                mb, w, h = _page_size(page)
                key = (pi, w, h) if per_page else (w, h)
                if key not in cache:
                    data = _make_overlay(w, h, lambda c, _pi=pi: draw_for(c, w, h, _pi, n))
                    op = stack.enter_context(pikepdf.open(BytesIO(data)))
                    cache[key] = op.pages[0]
                ov = cache[key]
                rect = pikepdf.Rectangle(*mb)
                page.add_underlay(ov, rect) if under else page.add_overlay(ov, rect)
            pdf.save(output_path)
    return len(idxs)


def _prep_image(image_path, opacity):
    """Return a path to a PNG with the given opacity baked into its alpha (for reportlab,
    which has no image-opacity control). Returns (path, is_temp)."""
    if opacity >= 0.999:
        return image_path, False
    from PIL import Image
    img = Image.open(image_path).convert("RGBA")
    alpha = img.split()[3].point(lambda a: int(a * opacity))
    img.putalpha(alpha)
    tmp = image_path + f".op{int(opacity * 100)}.png"
    img.save(tmp, "PNG")
    return tmp, True


def watermark(input_path, output_path, text=None, image=None, pages=None,
              font="Helvetica", size=48, color=(0.5, 0.5, 0.5), opacity=0.3,
              rotation=45, tile=False, position="center", under=False, scale=0.5):
    """Stamp a text or image watermark. Text options: font, size, color (r,g,b 0-1),
    opacity, rotation, tile (repeat across the page), position. Image options: opacity,
    position, scale (fraction of page width). Set under=True to place it behind the page."""
    if not text and not image:
        return {"ok": False, "error": "give --text or --image"}

    tmp_img = None
    if image:
        img_path, tmp_img = _prep_image(image, opacity)

    def draw(c, w, h, pi, total):
        if text:
            c.setFillColorRGB(*color)
            c.setFillAlpha(opacity)
            c.setFont(font, size)
            if tile:
                c.saveState()
                c.translate(w / 2, h / 2)
                c.rotate(rotation)
                tw = c.stringWidth(text, font, size)
                gx, gy = tw * 1.6, size * 3.2
                diag = int(math.hypot(w, h)) + int(gy)
                yy = -diag
                while yy < diag:
                    xx = -diag
                    while xx < diag:
                        c.drawString(xx, yy, text)
                        xx += gx
                    yy += gy
                c.restoreState()
            else:
                x, y, _ = _anchor(position, w, h)
                c.saveState()
                c.translate(x, y)
                c.rotate(rotation)
                c.drawCentredString(0, 0, text)
                c.restoreState()
        else:
            from PIL import Image
            iw, ih = Image.open(image).size
            dw = w * scale
            dh = dw * ih / iw
            x, y, _ = _anchor(position, w, h)
            c.drawImage(img_path, x - dw / 2, y - dh / 2, dw, dh, mask="auto")

    try:
        pages_done = _apply(input_path, output_path, draw, pages, under=under)
    finally:
        if tmp_img and os.path.exists(img_path):
            os.remove(img_path)
    return {"ok": True, "pages": pages_done, "kind": "text" if text else "image", "output": output_path}


def stamp(input_path, output_path, image=None, stamp_pdf=None, pages=None,
          position="center", scale=0.4, opacity=1.0, under=False):
    """Overlay an image or the first page of another PDF onto each page."""
    if not image and not stamp_pdf:
        return {"ok": False, "error": "give --image or --pdf"}

    if stamp_pdf:
        with pikepdf.open(input_path) as pdf, pikepdf.open(stamp_pdf) as sp:
            src = sp.pages[0]
            smb, sw, sh = _page_size(src)
            n = len(pdf.pages)
            idxs = sorted(set(parse_ranges(pages, n)))
            for pi in idxs:
                page = pdf.pages[pi]
                _, w, h = _page_size(page)
                dw = w * scale
                dh = dw * sh / sw
                x, y, _ = _anchor(position, w, h)
                rect = pikepdf.Rectangle(x - dw / 2, y - dh / 2, x + dw / 2, y + dh / 2)
                page.add_underlay(src, rect) if under else page.add_overlay(src, rect)
            pdf.save(output_path)
        return {"ok": True, "pages": len(idxs), "kind": "pdf", "output": output_path}

    return watermark(input_path, output_path, image=image, pages=pages,
                     position=position, scale=scale, opacity=opacity, under=under)


def number(input_path, output_path, fmt="Page {n} of {total}", pages=None,
           position="bottom-center", font="Helvetica", size=10,
           color=(0.2, 0.2, 0.2), start=1):
    """Add page numbers. `fmt` may use {n} (page number) and {total}. `start` sets the
    number on the first stamped page."""
    def draw(c, w, h, pi, total):
        label = fmt.format(n=pi + start, total=total, page=pi + start)
        c.setFillColorRGB(*color)
        c.setFont(font, size)
        x, y, halign = _anchor(position, w, h, margin=28)
        if halign == "l":
            c.drawString(x, y, label)
        elif halign == "r":
            c.drawRightString(x, y, label)
        else:
            c.drawCentredString(x, y, label)

    done = _apply(input_path, output_path, draw, pages, per_page=True)
    return {"ok": True, "pages": done, "output": output_path}


def bates(input_path, output_path, prefix="", digits=6, start=1, pages=None,
          position="bottom-right", font="Helvetica", size=9, color=(0.2, 0.2, 0.2)):
    """Add Bates numbers: a running prefix + zero-padded sequential number on each page."""
    counter = {"i": 0}

    def draw(c, w, h, pi, total):
        num = start + counter["i"]
        counter["i"] += 1
        label = f"{prefix}{num:0{digits}d}"
        c.setFillColorRGB(*color)
        c.setFont(font, size)
        x, y, halign = _anchor(position, w, h, margin=28)
        if halign == "l":
            c.drawString(x, y, label)
        elif halign == "r":
            c.drawRightString(x, y, label)
        else:
            c.drawCentredString(x, y, label)

    done = _apply(input_path, output_path, draw, pages, per_page=True)
    return {"ok": True, "pages": done, "output": output_path}
