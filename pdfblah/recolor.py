"""Re-ink a PDF: dark mode for night reading, sepia for warmth, or any ink color.

Three schemes, all deterministic maps over each page's rendered luminance:

  dark        Paper becomes near-black (#1E1E1E, not pure black), ink becomes soft
              light gray. The classic dark-reader look, baked into the file so any
              viewer, tablet, or e-reader shows it.
  sepia       Cream paper, warm brown ink.
  ink=COLOR   Paper stays white; the ink takes a color. Made for the sheet-music
              ask ("print my scores in dark blue") and branding one-offs. COLOR is
              a name (navy, blue, red, green, brown, gray) or #rrggbb.

Like `clean`, the output pages are re-rendered images at your chosen dpi, so this
is for reading and printing; the text layer does not survive (run `pdfblah ocr`
after if you want searchable dark-mode files). Color originals are mapped through
their luminance: photographs come out monochrome in the chosen scheme.
"""
import re

from ._pdfium import PDFIUM_LOCK

_COLORS = {"navy": (16, 42, 100), "blue": (25, 70, 160), "red": (150, 30, 30),
           "green": (25, 95, 50), "brown": (90, 60, 30), "gray": (90, 90, 90),
           "black": (0, 0, 0)}
_DARK_PAPER, _DARK_INK = 30, 232       # dark scheme endpoints (paper, ink)
_SEPIA_PAPER, _SEPIA_INK = (245, 235, 214), (62, 42, 22)


def parse_color(spec):
    """'navy' or '#1a2b3c' -> (r, g, b), or None."""
    if spec in _COLORS:
        return _COLORS[spec]
    m = re.fullmatch(r"#?([0-9a-fA-F]{6})", spec or "")
    if m:
        v = m.group(1)
        return tuple(int(v[i:i + 2], 16) for i in (0, 2, 4))
    return None


def recolor(input_path, output_path, scheme="dark", dpi=200):
    """Write a re-inked copy. scheme: "dark", "sepia", or "ink=COLOR".
    Returns {ok, pages, scheme, dpi, output}."""
    try:
        import numpy as np
        import pypdfium2 as pdfium
        from PIL import Image
    except ImportError:
        return {"ok": False,
                "error": 'recolor needs the app extra: pip install "pdfblah[app]"'}

    if scheme.startswith("ink="):
        color = parse_color(scheme[4:])
        if color is None:
            return {"ok": False,
                    "error": f"unknown ink color '{scheme[4:]}' (use one of "
                             f"{', '.join(sorted(_COLORS))}, or #rrggbb)"}
    elif scheme not in ("dark", "sepia"):
        return {"ok": False,
                "error": "scheme must be dark, sepia, or ink=COLOR"}

    dpi = max(72, min(600, int(dpi)))
    with PDFIUM_LOCK:
        doc = pdfium.PdfDocument(input_path)
        n = len(doc)
    pages = []
    try:
        for i in range(n):
            with PDFIUM_LOCK:
                pil = doc[i].render(scale=dpi / 72).to_pil().convert("L")
            g = np.asarray(pil, dtype=np.float64) / 255.0  # 0 = ink, 1 = paper
            if scheme == "dark":
                out = _DARK_INK + g * (_DARK_PAPER - _DARK_INK)
                pages.append(Image.fromarray(out.astype(np.uint8), "L"))
            elif scheme == "sepia":
                ink, paper = np.array(_SEPIA_INK), np.array(_SEPIA_PAPER)
                out = ink + g[..., None] * (paper - ink)
                pages.append(Image.fromarray(out.astype(np.uint8), "RGB"))
            else:
                c = np.array(color)
                out = 255 - (255 - c) * (1.0 - g[..., None])
                pages.append(Image.fromarray(out.astype(np.uint8), "RGB"))
    finally:
        with PDFIUM_LOCK:
            doc.close()
    if not pages:
        return {"ok": False, "error": "no pages"}
    pages[0].save(output_path, "PDF", save_all=True, append_images=pages[1:],
                  resolution=float(dpi))
    return {"ok": True, "pages": n, "scheme": scheme, "dpi": dpi,
            "output": output_path}
