"""Clean scanned pages: white paper, crisp ink.

Estimates each page's background (paper tone, lighting gradients, stains) with a heavy
blur and divides it out, then remaps levels so paper becomes pure #FFFFFF while ink
keeps its anti-aliased edges. Pages are re-rendered, so the output is an image PDF at
the chosen dpi (the input is usually a scan, so nothing searchable is lost; run `ocr`
afterwards to add a text layer)."""
import os

from ._pdfium import PDFIUM_LOCK

# (white point, black point) as fractions of full brightness after normalization
STRENGTHS = {
    "gentle":   (0.93, 0.45),
    "standard": (0.88, 0.55),
    "strong":   (0.82, 0.62),
}


def clean(input_path, output_path, dpi=300, strength="standard", bilevel=False):
    """Returns {ok, pages, dpi, strength, bilevel, white_pct} or {ok: False, error}."""
    try:
        import numpy as np
        import pypdfium2 as pdfium
        from PIL import Image, ImageFilter
    except ImportError:
        return {"ok": False,
                "error": 'clean needs the app extra: pip install "pdfblah[app]"'}

    if strength not in STRENGTHS:
        return {"ok": False, "error": f"strength must be one of {sorted(STRENGTHS)}"}
    white, black = STRENGTHS[strength]
    dpi = max(72, min(600, int(dpi)))
    pages, white_px, total_px = [], 0, 0

    with PDFIUM_LOCK:
        doc = pdfium.PdfDocument(input_path)
        n = len(doc)
    for i in range(n):
        with PDFIUM_LOCK:
            pil = doc[i].render(scale=dpi / 72).to_pil().convert("L")
        g = np.asarray(pil, dtype=np.float64)
        # background estimate on a downscaled copy (fast), blurred wide enough that
        # notation vanishes but paper tone, gradients, and stains remain
        w, h = pil.size
        small = pil.resize((max(1, w // 8), max(1, h // 8)))
        bg_small = small.filter(ImageFilter.GaussianBlur(max(3, dpi // 60)))
        bg = np.asarray(bg_small.resize((w, h)), dtype=np.float64)
        norm = np.clip(g / np.maximum(bg, 1.0) * 255.0, 0, 255)
        lo, hi = black * 255.0, white * 255.0
        out = np.clip((norm - lo) / (hi - lo), 0.0, 1.0) ** 1.1 * 255.0
        page = Image.fromarray(out.astype(np.uint8), "L")
        if bilevel:
            page = page.point(lambda v: 255 if v > 160 else 0).convert("1")
        arr = np.asarray(page.convert("L"))
        white_px += int((arr == 255).sum()); total_px += arr.size
        pages.append(page)
    with PDFIUM_LOCK:
        doc.close()
    if not pages:
        return {"ok": False, "error": "no pages"}
    pages[0].save(output_path, "PDF", save_all=True, append_images=pages[1:],
                  resolution=float(dpi))
    return {"ok": True, "pages": len(pages), "dpi": dpi, "strength": strength,
            "bilevel": bool(bilevel), "white_pct": round(100.0 * white_px / total_px, 1),
            "output": output_path}
