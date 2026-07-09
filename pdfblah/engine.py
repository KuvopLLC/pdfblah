"""pdfblah engine: real in-content-stream PDF text replacement.

Rewrites the actual Tj/TJ string operands in the page content stream, so the old
text is gone (pdftotext, Ctrl-F, and copy show only the new value). No overlay, no
watermark. Alignment is auto-detected and preserved. All original metadata
(DocInfo + XMP, including dates and Producer) is kept verbatim. Fonts we cannot
reproduce (non-embedded exotic or custom-encoded) are refused rather than garbled.

Library use:
    from pdfblah import process, apply_rules, parse_rules_file
    process("in.pdf", "out.pdf", "OLD", "NEW", scope="all", ci=True)
"""
import os, re, json, tempfile, shutil
import pikepdf
from pikepdf import Name, Operator, String, Array
import pdfplumber

from .metadata import apply_to_file


# ---- alignment auto-detection (proven on the trade-confirmation PDF) ----------
def detect_alignment(target, words, tol=1.2, vband=300):
    tx0, tx1, ttop = target["x0"], target["x1"], target["top"]
    tcx = (tx0 + tx1) / 2
    cx = lambda w: (w["x0"] + w["x1"]) / 2
    band = [w for w in words if abs(w["top"] - ttop) <= vband]
    m = {"left":   sum(1 for w in band if abs(w["x0"] - tx0) <= tol),
         "right":  sum(1 for w in band if abs(w["x1"] - tx1) <= tol),
         "center": sum(1 for w in band if abs(cx(w) - tcx) <= tol)}
    align = max(m, key=m.get)
    if m[align] < 2:
        align = "left"
    elif m["right"] == m["left"] and any(c.isdigit() for c in target["text"]):
        align = "right"
    return align, m


# ---- per-character advance widths, taken from the REAL font as rendered --------
def unit_widths(page, fontname):
    """Map char -> advance width per 1pt of font size, measured from actual glyph
    advances of the same font on the page. Font-exact, no substitute needed."""
    tbl, samples = {}, {}
    chars = [c for c in page.chars if c.get("fontname") == fontname]
    # advance = delta of x0 to the next char on the same line & word run
    chars_sorted = sorted(chars, key=lambda c: (round(c["top"]), c["x0"]))
    for a, b in zip(chars_sorted, chars_sorted[1:]):
        if abs(a["top"] - b["top"]) > 1:            # different line
            continue
        adv = b["x0"] - a["x0"]
        size = a.get("size") or 0
        if size <= 0 or adv <= 0 or adv > size * 3:  # sane advance only
            continue
        samples.setdefault(a["text"], []).append(adv / size)
    # successor deltas never sample the LAST glyph of a line/run (no successor), which
    # used to refuse replacements that reuse that glyph. Fall back to the glyph's own
    # advance width from the layout engine for anything not yet sampled.
    for c in chars_sorted:
        ch = c["text"]
        if ch in samples:
            continue
        size = c.get("size") or 0
        adv = c["x1"] - c["x0"]
        if size > 0 and 0 < adv <= size * 3:
            samples.setdefault(ch, []).append(adv / size)
    for ch, vals in samples.items():
        vals.sort(); tbl[ch] = vals[len(vals) // 2]   # median
    return tbl


def string_width(s, tbl, size, fallback):
    return sum(tbl.get(ch, fallback) for ch in s) * size


def matcher(find, ci=False, word=False, regex=False):
    """Compiled pattern for `find`: literal (re.escape) or, when regex=True, the
    string as a regular expression. Optional case-insensitive and whole-word (not
    inside a longer alphanumeric run)."""
    pat = find if regex else re.escape(find)
    if word:
        pat = r"(?<![0-9A-Za-z])" + pat + r"(?![0-9A-Za-z])"
    return re.compile(pat, re.IGNORECASE if ci else 0)


def _sub_x(w, off, n):
    """x of character offset `off` within word `w` (proportional estimate)."""
    return w["x0"] + (w["x1"] - w["x0"]) * (off / (n or 1))


def _line_matches(words, rx):
    """Run `rx` over each line (words joined by single spaces) and return
    [(x0, top, x1, bottom, text)]. Handles matches that span several words or sit
    next to punctuation glued to a word (e.g. 'Cohen,')."""
    boxes = []
    lines = {}
    for w in words:
        lines.setdefault(round(w["top"]), []).append(w)
    for key in sorted(lines):
        lw = sorted(lines[key], key=lambda w: w["x0"])
        text = ""; spans = []                        # spans[i] = (start, end, word)
        for w in lw:
            if text:
                text += " "
            start = len(text); text += w["text"]; spans.append((start, len(text), w))
        for m in rx.finditer(text):
            grp = [(s, e, w) for (s, e, w) in spans if s < m.end() and e > m.start()]
            if not grp:
                continue
            fs, _fe, fw = grp[0]; ls, _le, lw2 = grp[-1]
            x0 = _sub_x(fw, max(0, m.start() - fs), len(fw["text"]))
            x1 = _sub_x(lw2, min(len(lw2["text"]), m.end() - ls), len(lw2["text"]))
            boxes.append((x0, min(w["top"] for _, _, w in grp),
                          x1, max(w["bottom"] for _, _, w in grp), m.group(0)))
    return boxes


def locate_boxes(words, find, ci=False, word=False, regex=False):
    """Ordered matches where `find` occurs. Returns [(x0, top, x1, bottom, text)],
    where `text` is the exact matched substring. Single-word literal matches keep the
    document's word order (so Nth-match rules are stable); phrases and regex fall back
    to a whole-line search."""
    rx = matcher(find, ci, word, regex)
    if regex:
        return _line_matches(words, rx)
    boxes = []
    for w in words:
        m = rx.search(w["text"])
        if not m:
            continue
        n = len(w["text"])
        boxes.append((_sub_x(w, m.start(), n), w["top"], _sub_x(w, m.end(), n),
                      w["bottom"], w["text"][m.start():m.end()]))
    if boxes:
        return boxes
    return _line_matches(words, rx)


# ---- content-stream rewrite (position-matched to the located instance) --------
def _mul(m, n):
    a, b, c, d, e, f = m; A, B, C, D, E, F = n
    return (a*A + b*C, a*B + b*D, c*A + d*C, c*B + d*D,
            e*A + f*C + E, e*B + f*D + F)

def _op_text(operands, so):
    if so == "TJ":
        return "".join(bytes(e).decode("latin-1") for e in operands[0]
                       if isinstance(e, (String, bytes)))
    if so == "\"":
        return bytes(operands[2]).decode("latin-1")
    return bytes(operands[0]).decode("latin-1")          # Tj and '


# ---- coalesce glyph-by-glyph text into editable strings -----------------------
def _show_char(operands, so):
    """Return the single character this op shows, or None if it shows != 1 char.
    Handles Tj and single-element TJ; leaves ' and " alone (they also move the
    line, so merging across them is not safe)."""
    if so == "Tj":
        s = bytes(operands[0]).decode("latin-1")
        return s if len(s) == 1 else None
    if so == "TJ":
        s = "".join(bytes(e).decode("latin-1") for e in operands[0]
                    if isinstance(e, (String, bytes)))
        return s if len(s) == 1 else None
    return None


def _mkfield(ops, gops, gaps):
    text = "".join(_show_char(ops[g][0], str(ops[g][1])) for g in gops)
    return {"text": text, "gops": gops, "gaps": gaps, "first": gops[0], "last": gops[-1]}


def glyph_fields(ops):
    """Find glyph-by-glyph fields: >=2 consecutive single-character show ops on one
    baseline joined by small Td moves (each character drawn and positioned on its
    own). Returns a list of fields; each is a dict with 'text', 'gops' (glyph op
    indices, contiguous), 'gaps' (list of (td_idx, dx, dy) BETWEEN successive glyphs,
    length len(gops)-1), 'first', 'last'. A Td gap far larger than the run's median
    is a column jump: it splits fields and stays put to position the next one.
    Ordinary multi-character text (normal Tj words) is never part of a field, so it
    is left untouched."""
    n = len(ops); fields = []; i = 0
    while i < n:
        if _show_char(ops[i][0], str(ops[i][1])) is None:
            i += 1; continue
        gops = [i]; chain = []               # chain[k] = (td_idx, dx, dy)
        j = i + 1
        while j + 1 < n and str(ops[j][1]) in ("Td", "TD"):
            dx, dy = float(ops[j][0][0]), float(ops[j][0][1])
            if abs(dy) > 0.3:
                break
            if _show_char(ops[j + 1][0], str(ops[j + 1][1])) is None:
                break
            chain.append((j, dx, dy)); gops.append(j + 1); j += 2
        if len(gops) < 2:
            i += 1; continue
        dxs = sorted(g[1] for g in chain)
        thresh = 2.5 * dxs[len(dxs) // 2] + 0.5
        seg_g = [gops[0]]; seg_gap = []
        for k, (gi, dx, dy) in enumerate(chain):
            if dx > thresh:                  # column jump -> field boundary
                if len(seg_g) >= 2:
                    fields.append(_mkfield(ops, seg_g, seg_gap))
                seg_g = [gops[k + 1]]; seg_gap = []
            else:
                seg_g.append(gops[k + 1]); seg_gap.append((gi, dx, dy))
        if len(seg_g) >= 2:
            fields.append(_mkfield(ops, seg_g, seg_gap))
        i = gops[-1] + 1
    return fields


def rewrite_stream(pdf, page, find, replace, target_xy, align, size, wtbl, fb,
                   ci=False, word=False, tol=14.0, regex=False, sub=None):
    """Replace the single find-match whose device position is nearest target_xy
    (x, y_from_bottom). `replace` is the concrete string for this instance (the
    caller has already expanded any backrefs). Returns (True, dist) or
    (False, dist|None)."""
    rx = matcher(find, ci, word, regex)
    ID = (1, 0, 0, 1, 0, 0)
    ctm = ID; gstack = []; tm = ID; tlm = ID; leading = 0.0
    cur_tf = None                    # (name operand, size operand) of the active font
    tx, ty = target_xy
    best = None                      # (dist, kind, idx, mstart, mend, tf)
    ops = list(pikepdf.parse_content_stream(page))
    fields = glyph_fields(ops)       # glyph-by-glyph runs, matched as whole strings
    field_by_first = {f["first"]: f for f in fields}
    member = {g for f in fields for g in f["gops"]}

    def show_pos():
        m = _mul(tm, ctm)
        return m[4], m[5]

    def consider(kind, idx, txt, x, y):
        nonlocal best
        for m in rx.finditer(txt):
            cx = x + string_width(txt[:m.start()], wtbl, size, fb)
            d = ((cx - tx) ** 2 + (y - ty) ** 2) ** 0.5
            if best is None or d < best[0]:
                best = (d, kind, idx, m.start(), m.end(), cur_tf)

    for idx, (operands, op) in enumerate(ops):
        so = str(op)
        if so == "q": gstack.append(ctm)
        elif so == "Q": ctm = gstack.pop() if gstack else ctm
        elif so == "cm": ctm = _mul(tuple(float(x) for x in operands), ctm)
        elif so == "BT": tm = tlm = ID
        elif so == "TL": leading = float(operands[0])
        elif so in ("Td", "TD"):
            dx, dy = float(operands[0]), float(operands[1])
            if so == "TD": leading = -dy
            tlm = _mul((1, 0, 0, 1, dx, dy), tlm); tm = tlm
        elif so == "Tm": tm = tlm = tuple(float(x) for x in operands)
        elif so == "Tf": cur_tf = (operands[0], operands[1])
        elif so == "T*": tlm = _mul((1, 0, 0, 1, 0, -leading), tlm); tm = tlm
        elif so in ("Tj", "TJ", "'", "\""):
            if so in ("'", "\""):
                tlm = _mul((1, 0, 0, 1, 0, -leading), tlm); tm = tlm
            x, y = show_pos()
            if idx in field_by_first:                 # start of a glyph-by-glyph field
                consider("field", idx, field_by_first[idx]["text"], x, y)
            elif idx in member:                       # inner glyph of a field: skip
                pass
            else:                                     # ordinary Tj/TJ string
                consider("op", idx, _op_text(operands, so), x, y)
    if best is None or best[0] > tol:
        return (False, None if best is None else best[0])

    _, kind, idx, ms, me, tf = best
    if sub is not None:
        # substituted rewrite: the replacement is shown in the injected base-14 font.
        # We need the active Tf to restore afterwards; without one (inherited from an
        # outer stream) we bail rather than guess.
        if tf is None:
            return (False, best[0])
        try:
            replace.encode("latin-1")
        except UnicodeEncodeError:
            return (False, best[0])
        if kind == "field":
            new_ops = _rewrite_field(ops, field_by_first[idx], ms, me,
                                     replace, align, size, wtbl, fb, sub=sub, tf=tf)
        else:
            operands, op = ops[idx]; so = str(op)
            if so == "Tj":
                seq = _sub_splice_tj(bytes(operands[0]).decode("latin-1"), ms, me,
                                     replace, align, size, wtbl, fb, sub, tf)
            elif so == "TJ":
                seq = _sub_splice_tjarr(operands[0], ms, me,
                                        replace, align, size, wtbl, fb, sub, tf)
            else:                       # ' and " also move the line; not worth the risk
                return (False, best[0])
            new_ops = list(ops[:idx]) + seq + list(ops[idx + 1:])
        page.Contents = pdf.make_stream(pikepdf.unparse_content_stream(new_ops))
        return (True, best[0])
    if kind == "field":
        new_ops = _rewrite_field(ops, field_by_first[idx], ms, me,
                                 replace, align, size, wtbl, fb)
    else:
        operands, op = ops[idx]
        patched = _rewrite_one(operands, str(op), ms, me, replace, align, size, wtbl, fb)
        if patched is None:             # span crosses TJ elements -> unsupported
            return (False, best[0])
        new_ops = list(ops); new_ops[idx] = patched
    page.Contents = pdf.make_stream(pikepdf.unparse_content_stream(new_ops))
    return (True, best[0])


def _rewrite_field(ops, f, ms, me, replace, align, size, wtbl, fb, sub=None, tf=None):
    """Rewrite the [ms:me] character span of a glyph-by-glyph field. Only the matched
    glyphs (and the Td moves strictly between them) are replaced, with one Tj/TJ for
    the new text plus a single compensating Td that reproduces their exact line-matrix
    advance. Every other op on the page, including the field's own untouched glyphs,
    is left byte-for-byte identical."""
    gops = f["gops"]; gaps = f["gaps"]; text = f["text"]
    p, q = ms, me                                  # matched glyphs = gops[p:q]
    if sub is not None:
        new_ops = _sub_show(replace, align,
                            string_width(text[ms:me], wtbl, size, fb), size, sub, tf)
    else:
        new_ops = [_patch_span(text[ms:me], 0, me - ms, replace, align, size, wtbl, fb)]
    sdx = sum(gaps[k][1] for k in range(p, q - 1))
    sdy = sum(gaps[k][2] for k in range(p, q - 1))
    out = []
    for k in range(0, p):                          # glyphs before the match, with gaps
        out.append(ops[gops[k]]); out.append(ops[gaps[k][0]])
    out.extend(new_ops)
    out.append(([sdx, sdy], Operator("Td")))       # compensating line-matrix advance
    for k in range(q, len(gops)):                  # glyphs after the match
        if k == q:
            out.append(ops[gaps[q - 1][0]])        # gap that positions the first one
        out.append(ops[gops[k]])
        if k < len(gops) - 1:
            out.append(ops[gaps[k][0]])
    return ops[:f["first"]] + out + ops[f["last"] + 1:]


def _rewrite_one(operands, so, ms, me, replace, align, size, wtbl, fb):
    """Rewrite the [ms:me] span of an op's shown text with `replace`."""
    if so == "Tj":
        s = bytes(operands[0]).decode("latin-1")
        return _patch_span(s, ms, me, replace, align, size, wtbl, fb)
    if so == "'":
        s = bytes(operands[0]).decode("latin-1")
        return ([String((s[:ms] + replace + s[me:]).encode("latin-1"))], Operator("'"))
    if so == "\"":
        s = bytes(operands[2]).decode("latin-1")
        return ([operands[0], operands[1],
                 String((s[:ms] + replace + s[me:]).encode("latin-1"))], Operator("\""))
    # TJ: the shown text is the concatenation of the string elements (numbers
    # between them are kerning). Map the joined [ms:me] span across however many
    # elements it covers: place `replace` in the first overlapping element, strip
    # the matched chars from the rest, and drop kerning that falls inside the match.
    arr = operands[0]; pos = 0; new_arr = []; placed = False; hit = False
    for e in arr:
        if isinstance(e, (String, bytes)):
            es = bytes(e).decode("latin-1"); L = len(es)
            ov_s, ov_e = max(ms, pos), min(me, pos + L)
            if ov_s < ov_e:                          # element overlaps the match
                hit = True
                before, after = es[:ov_s - pos], es[ov_e - pos:]
                new_s = (before + replace + after) if not placed else (before + after)
                placed = True
                new_arr.append(String(new_s.encode("latin-1")))
            else:
                new_arr.append(e)
            pos += L
        else:                                        # kerning number
            if not (ms < pos < me):                  # keep unless strictly inside match
                new_arr.append(e)
    return ([Array(new_arr)], Operator("TJ")) if hit else None


# ---- explicit font substitution (the OPT-IN escape hatch for refused fonts) ----
# Never automatic: the caller must ask (rule {"substituteFont": true}), the result is
# reported ({"substituted": {from, to}}), and the substitute is a base-14 font every
# viewer renders identically. "Similar" = serif/sans/mono + bold/italic from the name.

def pick_substitute(fontname):
    n = (fontname or "").lower()
    bold = any(k in n for k in ("bold", "black", "heavy", "semibold"))
    ital = "italic" in n or "oblique" in n
    mono = any(k in n for k in ("mono", "courier", "consol"))
    serif = "sans" not in n and any(k in n for k in
        ("times", "serif", "georgia", "garamond", "book", "roman", "cambria", "minion", "palatino"))
    if mono:
        base = "Courier"
    elif serif:
        return "Times-" + ("BoldItalic" if bold and ital else "Bold" if bold
                           else "Italic" if ital else "Roman")
    else:
        base = "Helvetica"
    return base + ("-BoldOblique" if bold and ital else "-Bold" if bold
                   else "-Oblique" if ital else "")


def _b14_width(text, ps, size):
    """Width of `text` in the base-14 font `ps` at `size` (reportlab AFM metrics)."""
    from reportlab.pdfbase.pdfmetrics import stringWidth
    return stringWidth(text, ps, size)


def _ensure_sub_font(pdf, page, ps):
    """Register the base-14 font `ps` in the page's /Font resources (idempotent).
    Returns the resource name (e.g. "/PBsubHelvetica")."""
    name = "/PBsub" + ps.replace("-", "")
    if "/Resources" not in page:
        page.Resources = pikepdf.Dictionary()
    if "/Font" not in page.Resources:
        page.Resources.Font = pikepdf.Dictionary()
    if name not in page.Resources.Font:
        page.Resources.Font[name] = pdf.make_indirect(pikepdf.Dictionary(
            Type=Name("/Font"), Subtype=Name("/Type1"),
            BaseFont=Name("/" + ps), Encoding=Name("/WinAnsiEncoding")))
    return name


def _sub_show(replace, align, ow, size, sub, tf):
    """The ops that show `replace` in the substitute font and restore the original:
    Tf switch, the (kern-anchored) show, Tf back. `ow` = width of the matched text in
    the ORIGINAL font; `tf` = the (name, size) operands of the active Tf."""
    seq = [([Name(sub["res"]), tf[1]], Operator("Tf"))]
    nw = _b14_width(replace, sub["ps"], size)
    if align in ("right", "center") and size:
        frac = (ow - nw) if align == "right" else (ow - nw) / 2.0
        seq.append(([Array([-frac * 1000.0 / size, String(replace.encode("latin-1"))])], Operator("TJ")))
    else:
        seq.append(([String(replace.encode("latin-1"))], Operator("Tj")))
    seq.append(([tf[0], tf[1]], Operator("Tf")))
    return seq


def _sub_splice_tj(s, ms, me, replace, align, size, wtbl, fb, sub, tf):
    """A plain Tj whose [ms:me] span is replaced in the substitute font."""
    seq = []
    if s[:ms]:
        seq.append(([String(s[:ms].encode("latin-1"))], Operator("Tj")))
    seq += _sub_show(replace, align, string_width(s[ms:me], wtbl, size, fb), size, sub, tf)
    if s[me:]:
        seq.append(([String(s[me:].encode("latin-1"))], Operator("Tj")))
    return seq


def _sub_splice_tjarr(arr, ms, me, replace, align, size, wtbl, fb, sub, tf):
    """A TJ array whose joined [ms:me] span is replaced in the substitute font. Kerning
    inside the match is dropped (it kerned glyphs that no longer exist); the rest keeps
    its exact elements."""
    pre, post, pos, matched = [], [], 0, []
    for e in arr:
        if isinstance(e, (String, bytes)):
            es = bytes(e).decode("latin-1"); L = len(es)
            if pos + L <= ms:
                pre.append(e)
            elif pos >= me:
                post.append(e)
            else:
                b, a = es[:max(0, ms - pos)], es[max(0, me - pos):]
                matched.append(es[max(0, ms - pos):min(L, me - pos)])
                if b:
                    pre.append(String(b.encode("latin-1")))
                if a:
                    post.append(String(a.encode("latin-1")))
            pos += L
        else:  # kerning number: keep outside the match, drop inside
            if pos <= ms:
                pre.append(e)
            elif pos >= me:
                post.append(e)
    seq = []
    if pre:
        seq.append(([Array(pre)], Operator("TJ")))
    seq += _sub_show(replace, align, string_width("".join(matched), wtbl, size, fb), size, sub, tf)
    if post:
        seq.append(([Array(post)], Operator("TJ")))
    return seq


def _patch_span(s, ms, me, replace, align, size, wtbl, fb):
    """Show `s` with [ms:me] replaced by `replace`, repositioned so the detected
    anchor edge stays fixed (right/center); left = in place."""
    ns = s[:ms] + replace + s[me:]
    if align == "left":
        return [String(ns.encode("latin-1"))], Operator("Tj")
    ow = string_width(s[ms:me], wtbl, size, fb)
    nw = string_width(replace, wtbl, size, fb)
    frac = (ow - nw) if align == "right" else (ow - nw) / 2.0
    num = -frac * 1000.0 / size
    return [Array([num, String(ns.encode("latin-1"))])], Operator("TJ")


# Non-embedded families that render faithfully because every PDF viewer has them
# (or a metric-compatible substitute), AND whose text is stored as real WinAnsi
# bytes (so we can edit it). Exotic non-embedded fonts stay refused.
SAFE_FAMILIES = {
    "helvetica", "arial", "arialmt",
    "times", "timesnewroman", "timesroman",
    "courier", "couriernew",
    "symbol", "zapfdingbats",
    "calibri", "cambria", "georgia", "verdana", "tahoma",
    "trebuchet", "trebuchetms", "comicsansms",
    "liberationsans", "liberationserif", "liberationmono",
    "dejavusans", "dejavuserif", "dejavusansmono",
    "nimbussans", "nimbusroman", "nimbusmono",
}

def _font_dict(page, fontname):
    """The page's /Font entry whose BaseFont matches `fontname`, or None."""
    fam = base_family_lc(fontname)
    try:
        fonts = page.get("/Resources", {}).get("/Font", {})
    except Exception:
        fonts = {}
    for _, f in dict(fonts).items():
        base = strip_subset(str(f.get("/BaseFont", "")).lstrip("/"))
        if base and (base.lower() == strip_subset(fontname or "").lower()
                     or base_family_lc(base) == fam):
            return f
    return None


def font_hard_refusal(page, fontname):
    """Fonts whose TEXT BYTES we can't rewrite at all (so substitution can't help
    either): Type0/CID fonts store glyph IDs, Type3 fonts draw vector glyphs.
    Returns a reason string, or None."""
    fd = _font_dict(page, fontname)
    sub = str(fd.get("/Subtype", "")) if fd is not None else ""
    if sub == "/Type0":
        return ("text in this font is stored as glyph IDs (a CID font), "
                "which pdfblah can't rewrite yet")
    if sub == "/Type3":
        return "this text is drawn as vector glyphs (a Type3 font); there's nothing to rewrite"
    return None


def font_safe(page, fontname, replace, wtbl):
    """Decide whether NEW text in `fontname` will render faithfully.
      - embedded font: safe only if every replace char was already observed
        (in-subset); a brand-new glyph may be missing.
      - non-embedded: safe only for the true standard-14 families with a
        standard byte encoding. Everything else (exotic AFP fonts, custom-encoded
        Arial, etc.) is REFUSED — proven to garble.
    Returns (bool, reason)."""
    fam = base_family_lc(fontname)
    hard = font_hard_refusal(page, fontname)
    if hard:
        return False, hard
    fd = _font_dict(page, fontname)
    embedded, enc_custom = False, False
    if fd is not None:
        desc = fd.get("/FontDescriptor", {})
        embedded = any(k in desc for k in ("/FontFile", "/FontFile2", "/FontFile3"))
        enc = fd.get("/Encoding", None)
        enc_custom = isinstance(enc, pikepdf.Dictionary)  # has /Differences
    if embedded:
        missing = [c for c in replace if c not in wtbl and c != " "]
        if missing:
            return False, f"embedded subset font missing glyph(s) {missing!r}"
        return True, "embedded; all replacement glyphs already present"
    if fam in SAFE_FAMILIES and not enc_custom:
        return True, "non-embedded common font with standard encoding"
    return False, (f"non-embedded font {fontname!r} (family {fam!r}) is not a common "
                   f"font{' and uses a custom encoding' if enc_custom else ''}, so new "
                   "text could render wrong")


def base_family_lc(n):
    return base_family(strip_subset(n or "")).lower().replace(" ", "")


def strip_subset(n): return re.sub(r"^[A-Z]{6}\+", "", n or "")
def base_family(n):
    n = re.split(r"[-,]", strip_subset(n))[0]
    return re.sub(r"(?i)(bold|italic|oblique|regular|mt|ps)+$", "", n).strip() or strip_subset(n)


def process(input_path, output_path, find, replace, page=None, scope="first",
            ci=False, word=False, align="auto", regex=False, repl=None, substitute=False):
    """Real in-stream replacement. `scope` is "first", "all", or an int N (Nth).
    `ci` = ignore case, `word` = whole-word only, `regex` = treat `find` as a regular
    expression. `repl`, if given, is called with the regex match object to compute the
    replacement per instance (else `replace` is used, expanding backrefs when regex).
    Returns a status dict; metadata (DocInfo + XMP) is preserved verbatim on save."""
    if os.path.abspath(input_path) == os.path.abspath(output_path):
        return {"ok": False, "error": "refusing to overwrite the input"}
    nth = scope if isinstance(scope, int) else None
    rx = matcher(find, ci, word, regex)

    def rep_for(mtext):
        if repl is not None:
            m = rx.search(mtext)
            return repl(m) if m else ""
        if regex:
            m = rx.search(mtext)
            return m.expand(replace) if m else replace
        return replace

    # 1) locate all matches (across pages), measure font/alignment per match
    targets = []           # {pi, xy, align, size, wtbl, fb, fontname, rep}
    with pdfplumber.open(input_path) as pdf:
        pages = range(len(pdf.pages)) if page is None else [page - 1]
        for pi in pages:
            pg = pdf.pages[pi]
            words = pg.extract_words(use_text_flow=True)
            for (x0, top, x1, bottom, mtext) in locate_boxes(words, find, ci, word, regex):
                al, amap = (align, {})
                if align == "auto":
                    al, amap = detect_alignment(
                        {"x0": x0, "top": top, "x1": x1, "bottom": bottom, "text": mtext}, words)
                chars = [c for c in pg.chars if x0 - 1 <= c["x0"] <= x1 + 1
                         and top - 1 <= c["top"] <= bottom + 1]
                fontname = chars[0].get("fontname") if chars else None
                size = chars[0].get("size") if chars else 10.0
                wtbl = unit_widths(pg, fontname) if fontname else {}
                fb = (sum(wtbl.values()) / len(wtbl)) if wtbl else 0.5
                targets.append({"pi": pi, "xy": (x0, pg.height - bottom), "align": al,
                                "amap": amap, "size": size or 10.0, "wtbl": wtbl,
                                "fb": fb, "fontname": fontname, "rep": rep_for(mtext),
                                "box": (pi, x0, pg.height - bottom, x1, pg.height - top)})
    if not targets:
        return {"ok": False, "error": f"text {find!r} not found"}
    if nth is not None:
        if nth > len(targets):
            return {"ok": False, "error": f"{find!r} has only {len(targets)} match(es); "
                    f"asked for #{nth}"}
        targets = [targets[nth - 1]]
    elif scope != "all":
        targets = [targets[0]]                    # "first"

    # 2) FONT-SAFETY GATE — refuse if any selected match uses a font we can't reproduce.
    # With substitute=True (an EXPLICIT user choice, never automatic) the refused match
    # is rewritten in a similar base-14 font instead, and the report says so.
    pdf = pikepdf.open(input_path)
    sub_info = None
    page_subs = {}                                   # pi -> {ps_name: resource_name}
    for t in targets:
        t["sub"] = None
        safe, reason = font_safe(pdf.pages[t["pi"]], t["fontname"], t["rep"], t["wtbl"])
        if safe:
            continue
        hard = font_hard_refusal(pdf.pages[t["pi"]], t["fontname"])
        if not substitute or hard:
            out = {"ok": False, "refused": True, "font": t["fontname"], "reason": reason,
                   "hint": "non-embedded/exotic or custom-encoded font; new text can "
                           "garble, so this is the detect-and-refuse path"}
            if hard:
                out["substitutable"] = False
            return out
        try:
            t["rep"].encode("latin-1")
        except UnicodeEncodeError:
            return {"ok": False, "refused": True, "font": t["fontname"], "reason":
                    "replacement has characters outside the standard encoding, so even "
                    "a substitute font cannot show it faithfully"}
        ps = pick_substitute(t["fontname"])
        resmap = page_subs.setdefault(t["pi"], {})
        if ps not in resmap:
            resmap[ps] = _ensure_sub_font(pdf, pdf.pages[t["pi"]], ps)
        t["sub"] = {"res": resmap[ps], "ps": ps}
        sub_info = {"from": strip_subset(t["fontname"] or "unknown"), "to": ps}

    # 3) rewrite each selected match. Process right-to-left / bottom-to-top so that a
    #    width-changing edit (e.g. a deletion) never shifts a not-yet-rewritten match
    #    out from under its recorded position.
    order = sorted(range(len(targets)),
                   key=lambda i: (targets[i]["pi"], -targets[i]["xy"][1], targets[i]["xy"][0]),
                   reverse=True)
    count = 0; subbed = 0; applied_boxes = []
    for i in order:
        t = targets[i]
        ok, _ = rewrite_stream(pdf, pdf.pages[t["pi"]], find, t["rep"], t["xy"],
                               t["align"], t["size"], t["wtbl"], t["fb"], ci, word,
                               regex=regex, sub=t["sub"])
        if ok:
            count += 1; applied_boxes.append(t["box"])
            if t["sub"]:
                subbed += 1
    if count == 0:
        return {"ok": False,
                "error": f"{find!r} located visually but not found as an editable "
                         "Tj/TJ string (split/encoded run)"}
    pdf.save(output_path, fix_metadata_version=False, deterministic_id=False)
    first = targets[0]
    out = {"ok": True, "page": first["pi"] + 1, "font": first["fontname"],
           "size_pt": round(first["size"], 2), "align": first["align"],
           "align_votes": first["amap"], "scope": scope, "count": count,
           "replaced": f"{find!r} -> {replace!r}", "output": output_path,
           "boxes": applied_boxes}
    if sub_info:
        out["substituted"] = dict(sub_info, count=subbed)
    return out


def _draw_fill_rects(pdf, page, rects, color):
    """Append filled rectangles to a page's content stream (drawn on top). `rects`
    are (x0, y0, x1, y1) in PDF user space; `color` is (r, g, b) in 0..1."""
    r, g, b = color
    ops = list(pikepdf.parse_content_stream(page))
    ops.append(([], Operator("q")))
    ops.append(([r, g, b], Operator("rg")))
    for (x0, y0, x1, y1) in rects:
        pad = 1.0                                    # cover antialiased edges
        ops.append(([x0 - pad, y0 - pad, (x1 - x0) + 2 * pad, (y1 - y0) + 2 * pad],
                    Operator("re")))
    ops.append(([], Operator("f")))
    ops.append(([], Operator("Q")))
    page.Contents = pdf.make_stream(pikepdf.unparse_content_stream(ops))


def redact(input_path, output_path, find, page=None, scope="all", ci=False,
           word=False, regex=False, bar=True, color=(0, 0, 0)):
    """Truly remove matched text from the content stream (gone from pdftotext, Ctrl-F
    and copy), and by default draw a filled bar over each spot that was removed. Bars
    are only drawn where the text was actually deleted, so a bar never hides text that
    is still selectable underneath. Returns a status dict."""
    r = process(input_path, output_path, find, "", page, scope, ci, word, regex=regex)
    if not r.get("ok"):
        return r
    boxes = r.get("boxes", [])
    if bar and boxes:
        pdf = pikepdf.open(output_path, allow_overwriting_input=True)
        by_page = {}
        for (pi, x0, y0, x1, y1) in boxes:
            by_page.setdefault(pi, []).append((x0, y0, x1, y1))
        for pi, rects in by_page.items():
            _draw_fill_rects(pdf, pdf.pages[pi], rects, color)
        pdf.save(output_path, fix_metadata_version=False, deterministic_id=False)
    r["redacted"] = r.get("count", 0)
    r["bars"] = len(boxes) if bar else 0
    return r


def apply_rules(input_path, output_path, rules, strip_meta=False, set_meta=None):
    """Apply many rules in order. Unapplicable rules are reported and skipped. Then,
    if requested, strip and/or set metadata on the output. rules: list of
    {"find","replace","scope"?,"ci"?,"word"?,"page"?,"align"?,"regex"?,"repl"?}."""
    report = []; cur = input_path; tmps = []; applied = 0
    for rule in rules:
        find = rule["find"]; replace = rule.get("replace", "")
        scope = rule.get("scope", "first")
        if isinstance(scope, str) and scope.isdigit():
            scope = int(scope)
        nxt = tempfile.mktemp(suffix=".pdf")
        r = process(cur, nxt, find, replace, rule.get("page"), scope,
                    bool(rule.get("ci")), bool(rule.get("word")), rule.get("align", "auto"),
                    bool(rule.get("regex")), rule.get("repl"),
                    substitute=bool(rule.get("substituteFont")))
        entry = {"find": find, "replace": replace, "applied": bool(r.get("ok")), "scope": scope}
        for k in ("count", "font", "reason", "error", "refused", "page", "substituted", "substitutable"):
            if k in r:
                entry[k] = r[k]
        report.append(entry)
        if r.get("ok"):
            cur = nxt; tmps.append(nxt); applied += 1
        elif os.path.exists(nxt):
            os.remove(nxt)
    shutil.copyfile(cur, output_path)
    for t in tmps:
        if os.path.abspath(t) != os.path.abspath(output_path) and os.path.exists(t):
            os.remove(t)
    meta_changed = apply_to_file(output_path, strip_meta, set_meta) if (strip_meta or set_meta) else []
    return {"applied": applied, "total": len(rules),
            "all_applied": applied == len(rules), "rules": report,
            "meta_changed": meta_changed}


def parse_flags(s):
    out = {"scope": "first", "ci": False, "word": False, "regex": False}
    for f in (s or "").strip().lower().split():
        if f == "all": out["scope"] = "all"
        elif f == "first": out["scope"] = "first"
        elif f.isdigit(): out["scope"] = int(f)
        elif f == "ci": out["ci"] = True
        elif f == "word": out["word"] = True
        elif f in ("re", "regex"): out["regex"] = True
        elif re.fullmatch(r"p\d+", f): out["page"] = int(f[1:])
    return out


def parse_rules(text):
    """Parse a rules file into {"rules", "strip_meta", "set_meta"}.
    Normal lines are 'FIND | REPLACE | FLAGS'. '#' comments and blanks are skipped.
    Metadata directives (a line starting with '@'):
        @strip-metadata
        @set-metadata author = Jane Roe
    """
    rules = []; strip_meta = False; sets = {}
    for raw in text.splitlines():
        t = raw.strip()
        if not t or t.startswith("#"):
            continue
        if t.startswith("@"):
            d = t[1:].strip()
            low = d.lower()
            if low.replace("_", "-") in ("strip-metadata", "strip-meta"):
                strip_meta = True
            elif low.startswith(("set-metadata", "set-meta")):
                body = d.split(None, 1)[1] if len(d.split(None, 1)) > 1 else ""
                if "=" in body:
                    k, v = body.split("=", 1)
                    sets[k.strip()] = v.strip()
            continue
        parts = t.split("|")
        find = parts[0].strip()
        if not find:
            continue
        rule = {"find": find, "replace": parts[1].strip() if len(parts) > 1 else ""}
        rule.update(parse_flags(parts[2] if len(parts) > 2 else ""))
        rules.append(rule)
    return {"rules": rules, "strip_meta": strip_meta, "set_meta": sets or None}


def parse_rules_file(text):
    """Backwards-compatible: return just the list of rules (metadata directives,
    if any, are ignored here; use parse_rules to get them)."""
    return parse_rules(text)["rules"]
