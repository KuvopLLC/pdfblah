"""CLI handlers for the PDF toolkit commands (page ops, security, marks, forms, compare,
signatures). Each returns a process exit code. Registered into pdfblah.cli.HANDLERS."""
import argparse
import json
import os
import sys

from . import (
    combine, split, select_pages, rotate, crop, render_pages, extract_text,
    extract_images, protect, unlock, attachments, optimize, watermark, stamp,
    number, bates, form_list, form_fill, compare_pdfs, list_signatures,
    validate_signatures, convert, ocr, deps,
)
from .ocr import get_language, languages as ocr_languages


def _color(s):
    parts = [float(x) for x in s.split(",")]
    if len(parts) != 3:
        raise SystemExit("color must be r,g,b")
    return tuple(p / 255 if p > 1 else p for p in parts)


def _tuple(s, n, name):
    parts = [float(x) for x in s.split(",")]
    if len(parts) != n:
        raise SystemExit(f"{name} needs {n} comma-separated numbers")
    return tuple(parts)


def _emit(r, msg, want_json):
    if want_json:
        print(json.dumps({k: v for k, v in r.items() if k != "text"}, indent=2))
    elif r.get("ok"):
        print(msg())
    else:
        print(f"failed: {r.get('error')}", file=sys.stderr)
    return 0 if r.get("ok") else 1


def _ap(prog, desc):
    ap = argparse.ArgumentParser(prog=f"pdfblah {prog}", description=desc)
    return ap


# ---------- structure ----------
def _combine_main(argv):
    ap = _ap("combine", "Concatenate several PDFs into one, in order. With --toc the "
                        "output is a binder: a clickable Contents page, a bookmark per "
                        "document, and (with --tabs) numbered edge tabs.")
    ap.add_argument("inputs", nargs="+", help="input PDFs")
    ap.add_argument("-o", "--output", required=True)
    ap.add_argument("--toc", action="store_true", help="add a clickable table of contents")
    ap.add_argument("--tabs", action="store_true", help="numbered edge tab on each document's first page")
    ap.add_argument("--titles", help="section titles, comma-separated (default: each file's Title metadata, else its filename)")
    ap.add_argument("--toc-title", default="Contents")
    ap.add_argument("--toc-font", default="sans", choices=["sans", "serif"])
    ap.add_argument("--json", action="store_true")
    a = ap.parse_args(argv)
    titles = [t.strip() for t in a.titles.split(",")] if a.titles else None
    r = combine(a.inputs, a.output, toc=a.toc or a.tabs, titles=titles, tabs=a.tabs,
                toc_title=a.toc_title, toc_font=a.toc_font)
    extra = f" + {r['toc_pages']}-page ToC" if r.get("ok") and r.get("toc_pages") else ""
    return _emit(r, lambda: f"combined {r['files']} files, {r['pages']} pages{extra}  ->  {a.output}", a.json)


def _split_main(argv):
    ap = _ap("split", "Split a PDF into several files.")
    ap.add_argument("input")
    ap.add_argument("-o", "--out-dir", required=True)
    ap.add_argument("--every", type=int, default=1, help="pages per output file (default 1)")
    ap.add_argument("--ranges", nargs="*", metavar="SPEC",
                    help="page specs, one output each, e.g. 1-3 4-6 (overrides --every)")
    ap.add_argument("--json", action="store_true")
    a = ap.parse_args(argv)
    r = split(a.input, a.out_dir, every=a.every, ranges=a.ranges)
    return _emit(r, lambda: f"split into {r['parts']} file(s) in {a.out_dir}", a.json)


def _pages_main(argv):
    ap = _ap("pages", "Keep, drop, or reorder pages. --keep also sets the order.")
    ap.add_argument("input")
    ap.add_argument("output")
    ap.add_argument("--keep", metavar="SPEC", help="pages to keep, in order, e.g. 3,1,2 or 1-4")
    ap.add_argument("--drop", metavar="SPEC", help="pages to remove, e.g. 2,5")
    ap.add_argument("--json", action="store_true")
    a = ap.parse_args(argv)
    if not a.keep and not a.drop:
        raise SystemExit("give --keep or --drop")
    r = select_pages(a.input, a.output, keep=a.keep, drop=a.drop)
    return _emit(r, lambda: f"wrote {r['pages']} page(s)  ->  {a.output}", a.json)


def _rotate_main(argv):
    ap = _ap("rotate", "Rotate pages clockwise (a multiple of 90).")
    ap.add_argument("input")
    ap.add_argument("output")
    ap.add_argument("--degrees", type=int, required=True)
    ap.add_argument("--pages", metavar="SPEC", help="which pages (default all)")
    ap.add_argument("--json", action="store_true")
    a = ap.parse_args(argv)
    r = rotate(a.input, a.output, a.degrees, a.pages)
    return _emit(r, lambda: f"rotated {r['pages']} page(s) by {a.degrees}  ->  {a.output}", a.json)


def _crop_main(argv):
    ap = _ap("crop", "Crop pages by trimming margins or setting an absolute box (points).")
    ap.add_argument("input")
    ap.add_argument("output")
    ap.add_argument("--margins", help="left,bottom,right,top points to trim")
    ap.add_argument("--box", help="x0,y0,x1,y1 absolute crop box")
    ap.add_argument("--pages", metavar="SPEC")
    ap.add_argument("--json", action="store_true")
    a = ap.parse_args(argv)
    margins = _tuple(a.margins, 4, "--margins") if a.margins else None
    box = _tuple(a.box, 4, "--box") if a.box else None
    r = crop(a.input, a.output, margins=margins, box=box, pages=a.pages)
    return _emit(r, lambda: f"cropped {r['pages']} page(s)  ->  {a.output}", a.json)


# ---------- render / extract ----------
def _render_main(argv):
    ap = _ap("render", "Render pages to PNG or JPG images.")
    ap.add_argument("input")
    ap.add_argument("-o", "--out-dir", required=True)
    ap.add_argument("--pages", metavar="SPEC")
    ap.add_argument("--dpi", type=int, default=150)
    ap.add_argument("--format", default="png", help="png or jpg")
    ap.add_argument("--json", action="store_true")
    a = ap.parse_args(argv)
    r = render_pages(a.input, a.out_dir, pages=a.pages, dpi=a.dpi, fmt=a.format)
    return _emit(r, lambda: f"rendered {r['pages']} page(s) at {a.dpi} dpi in {a.out_dir}", a.json)


def _clean_main(argv):
    ap = _ap("clean", "Clean scanned pages: pure white background, crisp ink. "
                      "One file (in.pdf out.pdf) or a batch (a.pdf b.pdf ... -o dir/).")
    ap.add_argument("inputs", nargs="+")
    ap.add_argument("-o", "--out", help="output PDF (single input) or directory (batch)")
    ap.add_argument("--dpi", type=int, default=300)
    ap.add_argument("--strength", default="standard", choices=["gentle", "standard", "strong"])
    ap.add_argument("--bilevel", action="store_true", help="pure 1-bit output (smallest files)")
    ap.add_argument("--json", action="store_true")
    a = ap.parse_args(argv)
    from .clean import clean
    # `pdfblah clean in.pdf out.pdf` is the natural single-file spelling
    if len(a.inputs) == 2 and a.inputs[1].lower().endswith(".pdf") and not os.path.exists(a.inputs[1]) and not a.out:
        r = clean(a.inputs[0], a.inputs[1], dpi=a.dpi, strength=a.strength, bilevel=a.bilevel)
        return _emit(r, lambda: f"cleaned {r['pages']} page(s) -> {a.inputs[1]} ({r['white_pct']}% pure white)", a.json)
    outdir = a.out or "cleaned"
    os.makedirs(outdir, exist_ok=True)
    results = []
    for src_pdf in a.inputs:
        dst = os.path.join(outdir, os.path.basename(src_pdf)[:-4] + "-clean.pdf")
        r = clean(src_pdf, dst, dpi=a.dpi, strength=a.strength, bilevel=a.bilevel)
        results.append((src_pdf, r))
        print(("ok  " if r.get("ok") else "ERR ") + os.path.basename(src_pdf)
              + (f" -> {dst} ({r['white_pct']}% white)" if r.get("ok") else f": {r.get('error')}"))
    bad = [s for s, r in results if not r.get("ok")]
    return 1 if bad else 0


def _extract_main(argv):
    ap = _ap("extract", "Extract the text or the embedded images from a PDF.")
    ap.add_argument("input")
    ap.add_argument("-o", "--out", help="text file (for --text) or directory (for --images)")
    ap.add_argument("--text", action="store_true")
    ap.add_argument("--images", action="store_true")
    ap.add_argument("--pages", metavar="SPEC")
    ap.add_argument("--json", action="store_true")
    a = ap.parse_args(argv)
    if a.images:
        r = extract_images(a.input, a.out or ".", pages=a.pages)
        return _emit(r, lambda: f"extracted {r['images']} image(s) to {a.out or '.'}", a.json)
    r = extract_text(a.input, a.out, pages=a.pages)
    if not a.out and r.get("ok") and not a.json:
        print(r["text"])
        return 0
    return _emit(r, lambda: f"extracted {r['chars']} chars  ->  {a.out}", a.json)


# ---------- security ----------
def _protect_main(argv):
    ap = _ap("protect", "Encrypt a PDF with a password and permissions (AES-256).")
    ap.add_argument("input")
    ap.add_argument("output")
    ap.add_argument("--password", default="", help="user password (needed to open)")
    ap.add_argument("--owner", help="owner password (defaults to the user password)")
    ap.add_argument("--no-print", dest="allow_print", action="store_false")
    ap.add_argument("--no-copy", dest="allow_copy", action="store_false")
    ap.add_argument("--no-modify", dest="allow_modify", action="store_false")
    ap.add_argument("--json", action="store_true")
    a = ap.parse_args(argv)
    r = protect(a.input, a.output, user_password=a.password, owner_password=a.owner,
                allow_print=a.allow_print, allow_copy=a.allow_copy, allow_modify=a.allow_modify)
    return _emit(r, lambda: f"encrypted  ->  {a.output}", a.json)


def _unlock_main(argv):
    ap = _ap("unlock", "Remove encryption from a PDF (needs the current password).")
    ap.add_argument("input")
    ap.add_argument("output")
    ap.add_argument("--password", default="")
    ap.add_argument("--json", action="store_true")
    a = ap.parse_args(argv)
    r = unlock(a.input, a.output, password=a.password)
    return _emit(r, lambda: f"unlocked  ->  {a.output}", a.json)


def _attachments_main(argv):
    ap = _ap("attachments", "List, add, extract, or remove embedded file attachments.")
    ap.add_argument("input")
    ap.add_argument("-o", "--output", help="output PDF (for --add/--remove)")
    ap.add_argument("--add", nargs="*", metavar="FILE")
    ap.add_argument("--remove", nargs="*", metavar="NAME")
    ap.add_argument("--extract-to", metavar="DIR")
    ap.add_argument("--json", action="store_true")
    a = ap.parse_args(argv)
    r = attachments(a.input, a.output, add=a.add, extract_to=a.extract_to, remove=a.remove)

    def msg():
        if a.add or a.remove or a.extract_to:
            return f"attachments now: {', '.join(r['attachments']) or '(none)'}  ->  {a.output or a.input}"
        return "attachments: " + (", ".join(r["attachments"]) or "(none)")
    return _emit(r, msg, a.json)


def _optimize_main(argv):
    ap = _ap("optimize", "Shrink a PDF (lossless structure; optional image downsampling).")
    ap.add_argument("input")
    ap.add_argument("output")
    ap.add_argument("--downsample-dpi", type=int, help="cap embedded image resolution")
    ap.add_argument("--jpeg-quality", type=int, help="recompress images at this JPEG quality")
    ap.add_argument("--json", action="store_true")
    a = ap.parse_args(argv)
    r = optimize(a.input, a.output, downsample_dpi=a.downsample_dpi, jpeg_quality=a.jpeg_quality)

    def msg():
        pct = 100 * r["saved"] / r["before"] if r["before"] else 0
        return f"{r['before']:,} -> {r['after']:,} bytes ({pct:.0f}% smaller)  ->  {a.output}"
    return _emit(r, msg, a.json)


# ---------- marks ----------
def _watermark_main(argv):
    ap = _ap("watermark", "Stamp a text or image watermark over (or under) the pages.")
    ap.add_argument("input")
    ap.add_argument("output")
    ap.add_argument("--text")
    ap.add_argument("--image")
    ap.add_argument("--pages", metavar="SPEC")
    ap.add_argument("--font", default="Helvetica")
    ap.add_argument("--size", type=float, default=48)
    ap.add_argument("--color", default="0.5,0.5,0.5")
    ap.add_argument("--opacity", type=float, default=0.3)
    ap.add_argument("--rotation", type=float, default=45)
    ap.add_argument("--tile", action="store_true", help="repeat across the whole page")
    ap.add_argument("--position", default="center")
    ap.add_argument("--scale", type=float, default=0.5, help="image width as a fraction of the page")
    ap.add_argument("--under", action="store_true")
    ap.add_argument("--json", action="store_true")
    a = ap.parse_args(argv)
    r = watermark(a.input, a.output, text=a.text, image=a.image, pages=a.pages, font=a.font,
                  size=a.size, color=_color(a.color), opacity=a.opacity, rotation=a.rotation,
                  tile=a.tile, position=a.position, under=a.under, scale=a.scale)
    return _emit(r, lambda: f"watermarked {r['pages']} page(s)  ->  {a.output}", a.json)


def _stamp_main(argv):
    ap = _ap("stamp", "Overlay an image or the first page of another PDF onto each page.")
    ap.add_argument("input")
    ap.add_argument("output")
    ap.add_argument("--image")
    ap.add_argument("--pdf", dest="stamp_pdf")
    ap.add_argument("--pages", metavar="SPEC")
    ap.add_argument("--position", default="center")
    ap.add_argument("--scale", type=float, default=0.4)
    ap.add_argument("--opacity", type=float, default=1.0)
    ap.add_argument("--under", action="store_true")
    ap.add_argument("--json", action="store_true")
    a = ap.parse_args(argv)
    r = stamp(a.input, a.output, image=a.image, stamp_pdf=a.stamp_pdf, pages=a.pages,
              position=a.position, scale=a.scale, opacity=a.opacity, under=a.under)
    return _emit(r, lambda: f"stamped {r['pages']} page(s)  ->  {a.output}", a.json)


def _number_main(argv):
    ap = _ap("number", "Add page numbers.")
    ap.add_argument("input")
    ap.add_argument("output")
    ap.add_argument("--format", default="Page {n} of {total}", help="uses {n} and {total}")
    ap.add_argument("--pages", metavar="SPEC")
    ap.add_argument("--position", default="bottom-center")
    ap.add_argument("--font", default="Helvetica")
    ap.add_argument("--size", type=float, default=10)
    ap.add_argument("--color", default="0.2,0.2,0.2")
    ap.add_argument("--start", type=int, default=1)
    ap.add_argument("--json", action="store_true")
    a = ap.parse_args(argv)
    r = number(a.input, a.output, fmt=a.format, pages=a.pages, position=a.position,
               font=a.font, size=a.size, color=_color(a.color), start=a.start)
    return _emit(r, lambda: f"numbered {r['pages']} page(s)  ->  {a.output}", a.json)


def _bates_main(argv):
    ap = _ap("bates", "Add Bates numbering (prefix + sequential zero-padded number).")
    ap.add_argument("input")
    ap.add_argument("output")
    ap.add_argument("--prefix", default="")
    ap.add_argument("--digits", type=int, default=6)
    ap.add_argument("--start", type=int, default=1)
    ap.add_argument("--pages", metavar="SPEC")
    ap.add_argument("--position", default="bottom-right")
    ap.add_argument("--json", action="store_true")
    a = ap.parse_args(argv)
    r = bates(a.input, a.output, prefix=a.prefix, digits=a.digits, start=a.start,
              pages=a.pages, position=a.position)
    return _emit(r, lambda: f"bates-numbered {r['pages']} page(s)  ->  {a.output}", a.json)


# ---------- forms / compare / signatures ----------
def _form_main(argv):
    ap = _ap("form", "List form fields, fill them from a JSON file, or flatten.")
    ap.add_argument("input")
    ap.add_argument("output", nargs="?")
    ap.add_argument("--list", action="store_true")
    ap.add_argument("--fill", metavar="DATA.json")
    ap.add_argument("--flatten", action="store_true")
    ap.add_argument("--json", action="store_true")
    a = ap.parse_args(argv)
    if a.fill or a.flatten:
        if not a.output:
            raise SystemExit("give an output file for --fill/--flatten")
        data = json.load(open(a.fill)) if a.fill else {}
        r = form_fill(a.input, a.output, data, flatten=a.flatten)
        return _emit(r, lambda: f"filled {r['count']} field(s)"
                     + (" and flattened" if r["flattened"] else "") + f"  ->  {a.output}", a.json)
    r = form_list(a.input)
    if a.json:
        print(json.dumps(r, indent=2))
    else:
        for f in r["fields"]:
            print(f"{f['name']}  ({f['type']})  = {f['value']!r}")
        if not r["fields"]:
            print("no form fields")
    return 0


def _compare_main(argv):
    ap = _ap("compare", "Compare two PDFs: text differences per page, optional visual diff.")
    ap.add_argument("a")
    ap.add_argument("b")
    ap.add_argument("--visual", action="store_true")
    ap.add_argument("--out-dir", help="write per-page visual diff images here")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args(argv)
    r = compare_pdfs(args.a, args.b, visual=args.visual, out_dir=args.out_dir)
    if args.json:
        print(json.dumps(r, indent=2))
        return 0
    print(f"{r['changed']} of {r['pages']} page(s) differ "
          f"(A: {r['page_count_a']} pages, B: {r['page_count_b']} pages)")
    for p in r["detail"]:
        if p["identical"]:
            continue
        print(f"  page {p['page']}: +{len(p['added'])} -{len(p['removed'])} lines")
    return 0


def _signatures_main(argv):
    ap = _ap("signatures", "List digital signatures, or --validate their integrity.")
    ap.add_argument("input")
    ap.add_argument("--validate", action="store_true", help="check each signature (needs the sign extra)")
    ap.add_argument("--json", action="store_true")
    a = ap.parse_args(argv)
    r = validate_signatures(a.input) if a.validate else list_signatures(a.input)
    if a.json:
        print(json.dumps(r, indent=2))
        return 0 if r.get("ok") else 1
    if not r.get("ok"):
        print(r.get("error"), file=sys.stderr)
        return 1
    if not r["signatures"]:
        print("no signatures found")
        return 0
    for s in r["signatures"]:
        if a.validate:
            print(f"  {s.get('signer') or s.get('field')}: "
                  f"intact={s.get('intact')} valid={s.get('valid')} trusted={s.get('trusted')}")
        else:
            print(f"  {s.get('name') or s.get('field')}"
                  + (f" ({s['reason']})" if s.get('reason') else ""))
    return 0


# ---------- convert / ocr / doctor ----------
def _convert_main(argv):
    ap = _ap("convert", "Convert between PDF and office formats: PDF to DOCX, or "
                        "DOCX/ODT/PPTX/XLSX/... to PDF. The output extension picks the target.")
    ap.add_argument("input")
    ap.add_argument("-o", "--output", required=True, help="output file, e.g. out.docx or out.pdf")
    ap.add_argument("--pages", help="PDF to DOCX only: page range like 1-3,5")
    ap.add_argument("--json", action="store_true")
    a = ap.parse_args(argv)
    r = convert(a.input, a.output, pages=a.pages)
    return _emit(r, lambda: f"wrote {r['output']} ({r.get('format')})", a.json)


def _ocr_main(argv):
    ap = _ap("ocr", "Add a searchable text layer to a scanned (image-only) PDF. "
                    "Languages are modular: --langs lists what you have, --get-lang "
                    "downloads any other language as one file.")
    ap.add_argument("input", nargs="?")
    ap.add_argument("-o", "--output")
    ap.add_argument("--lang", default="eng", help="Tesseract language(s), e.g. eng or eng+deu")
    ap.add_argument("--force", action="store_true", help="re-OCR pages that already have text")
    ap.add_argument("--deskew", action="store_true", help="straighten crooked scans")
    ap.add_argument("--rotate", action="store_true", help="auto-rotate pages to detected orientation")
    ap.add_argument("--sidecar", help="also write the recognized text to this file")
    ap.add_argument("--langs", action="store_true",
                    help="list the OCR languages available on this machine")
    ap.add_argument("--get-lang", metavar="CODE",
                    help="download language file(s) into ~/.pdfblah/tessdata, e.g. deu or deu,fra")
    ap.add_argument("--best", action="store_true",
                    help="with --get-lang: the larger, higher-accuracy model")
    ap.add_argument("--json", action="store_true")
    a = ap.parse_args(argv)
    if a.langs:
        langs = ocr_languages()
        if a.json:
            print(json.dumps({"ok": bool(langs), "languages": langs}, indent=2))
        elif langs:
            print("\n".join(langs))
        else:
            print("no OCR languages found (is Tesseract installed? run `pdfblah doctor`)",
                  file=sys.stderr)
        return 0 if langs else 1
    if a.get_lang:
        rc = 0
        for code in [c.strip() for c in a.get_lang.split(",") if c.strip()]:
            r = get_language(code, best=a.best)
            rc |= _emit(r, lambda r=r: f"got {r['language']} ({r['quality']}, "
                                       f"{r['bytes'] // 1024} KB): {r['path']}", a.json)
        return rc
    if not a.input or not a.output:
        ap.error("input and -o/--output are required to OCR a PDF "
                 "(or use --langs / --get-lang for language files)")
    r = ocr(a.input, a.output, lang=a.lang, force=a.force, deskew=a.deskew,
            rotate_pages=a.rotate, sidecar=a.sidecar)
    return _emit(r, lambda: f"OCR done: {r['output']} ({r.get('chars', 0)} chars recognized)", a.json)


def _doctor_main(argv):
    ap = _ap("doctor", "Check the system tools that OCR and document conversion need, "
                       "and optionally install what is missing.")
    ap.add_argument("--install", action="store_true", help="try to install missing system tools")
    ap.add_argument("--json", action="store_true")
    a = ap.parse_args(argv)
    if a.install:
        print("Installing missing tools...")
        for res in deps.install():
            mark = "ok" if res.get("ok") else "FAILED"
            print(f"  [{mark}] {res['tool']}: {res.get('note') or res.get('cmd', '')}")
        print()
    st = deps.check()
    lo_present = st["tools"]["libreoffice"]["present"]
    lo_works = deps.libreoffice_works() if lo_present else False
    if a.json:
        st["libreoffice_works"] = lo_works
        print(json.dumps(st, indent=2))
        return 0
    print("pdfblah dependencies\n")
    for line in deps.summary_lines():
        print("  " + line.replace("\n", "\n  "))
    if lo_present and not lo_works:
        print("\n  [warn] libreoffice is installed but cannot convert documents (the "
              "Writer/Calc\n         components are missing). Fix with:\n"
              "         sudo apt install libreoffice-writer libreoffice-calc libreoffice-impress")
    missing = [n for n, s in st["tools"].items() if not s["present"]]
    missing += [m for m, s in st["libs"].items() if not s["present"]]
    if not missing and lo_works:
        print("\n  All set: OCR and document conversion are ready.")
    else:
        print("\n  Run `pdfblah doctor --install` to fetch what is missing, or install it by hand.")
    return 0


TOOL_HANDLERS = {
    "combine": _combine_main, "split": _split_main, "pages": _pages_main,
    "rotate": _rotate_main, "crop": _crop_main, "render": _render_main, "clean": _clean_main,
    "extract": _extract_main, "protect": _protect_main, "unlock": _unlock_main,
    "attachments": _attachments_main, "optimize": _optimize_main,
    "watermark": _watermark_main, "stamp": _stamp_main, "number": _number_main,
    "bates": _bates_main, "form": _form_main, "compare": _compare_main,
    "signatures": _signatures_main, "convert": _convert_main, "ocr": _ocr_main,
    "doctor": _doctor_main,
}
