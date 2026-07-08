"""Render a preview of the edited PDF: the pages stacked, each edited spot outlined,
optionally with a 'PREVIEW' watermark (hosted uses it; local does not). Uses pypdfium2
so there is no system dependency (poppler) to install or bundle."""
import os

try:  # preview rendering is an optional extra (pdfblah[app]); the local app never renders
    import pypdfium2 as pdfium
    from PIL import Image, ImageDraw, ImageFont
    _HAVE_RENDER = True
except ImportError:
    pdfium = None
    Image = ImageDraw = ImageFont = None
    _HAVE_RENDER = False

_FONT_CANDIDATES = [
    "/usr/share/fonts/truetype/liberation/LiberationSans-Bold.ttf",
    "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
    "/Library/Fonts/Arial Bold.ttf",
    "C:/Windows/Fonts/arialbd.ttf",
]


def render_preview(pdf_path, out_png, pages=(1,), dpi=110, max_h=1600,
                   highlights=None, watermark=True):
    """Render the given 1-indexed pages, stack them, outline each edited spot, and
    (if watermark) stamp 'PREVIEW · pdfblah.com'. Writes a PNG to out_png."""
    if not _HAVE_RENDER:
        raise RuntimeError(
            'preview rendering needs the app extra: pip install "pdfblah[app]"'
        )
    scale = dpi / 72.0
    doc = pdfium.PdfDocument(pdf_path)
    try:
        imgs = []
        for p in pages:
            page = doc[p - 1]
            pil = page.render(scale=scale).to_pil().convert("RGB")
            imgs.append((pil, p))
        if not imgs:
            raise RuntimeError("nothing rendered")
        w = max(i.width for i, _ in imgs)
        canvas = Image.new("RGB", (w, sum(i.height for i, _ in imgs)), "white")
        offsets = {}
        y = 0
        for img, p in imgs:
            canvas.paste(img, (0, y)); offsets[p] = (y, img.height); y += img.height
        if highlights:
            _draw_highlights(canvas, highlights, offsets, scale)
        if canvas.height > max_h:
            s = max_h / canvas.height
            canvas = canvas.resize((int(canvas.width * s), max_h))
        if watermark:
            _watermark(canvas)
        canvas.save(out_png, "PNG", optimize=True)
    finally:
        doc.close()
    return out_png


def _draw_highlights(canvas, highlights, offsets, scale):
    overlay = Image.new("RGBA", canvas.size, (0, 0, 0, 0))
    d = ImageDraw.Draw(overlay)
    for box in highlights:
        try:
            pi, x0, y0, x1, y1 = box
        except (ValueError, TypeError):
            continue
        page = pi + 1
        if page not in offsets:
            continue
        yoff, H = offsets[page]
        pad = 2
        d.rectangle([x0 * scale - pad, yoff + (H - y1 * scale) - pad,
                     x1 * scale + pad, yoff + (H - y0 * scale) + pad],
                    fill=(255, 106, 62, 46), outline=(255, 106, 62, 220), width=2)
    canvas.paste(Image.alpha_composite(canvas.convert("RGBA"), overlay).convert("RGB"), (0, 0))


def _load_font(size):
    for path in _FONT_CANDIDATES:
        if os.path.exists(path):
            try:
                return ImageFont.truetype(path, size)
            except Exception:
                pass
    return ImageFont.load_default()


def _watermark(img):
    W, H = img.size
    size = max(22, W // 26)
    font = _load_font(size)
    text = "PREVIEW · pdfblah.com"
    diag = int((W * W + H * H) ** 0.5) + size * 4
    layer = Image.new("RGBA", (diag, diag), (0, 0, 0, 0))
    d = ImageDraw.Draw(layer)
    tw = d.textlength(text, font=font)
    gap_x = int(tw * 1.7)
    gap_y = int(size * 3.6)
    for row, yy in enumerate(range(0, diag, gap_y)):
        shift = (gap_x // 2) if (row % 2) else 0
        for xx in range(-gap_x, diag, gap_x):
            d.text((xx + shift, yy), text, font=font, fill=(120, 120, 120, 60))
    layer = layer.rotate(30, resample=Image.BICUBIC, expand=False)
    ox, oy = (diag - W) // 2, (diag - H) // 2
    layer = layer.crop((ox, oy, ox + W, oy + H))
    img.paste(Image.alpha_composite(img.convert("RGBA"), layer).convert("RGB"), (0, 0))
