"""Apply an ordered list of edit steps (the Edit pipeline) to one PDF and return one
combined report. Composes the engine's public API: text edits, PII, metadata, marks
(watermark/number/bates/stamp), page ops (keep/reorder/rotate/crop), and protect/optimize.
Shared by the hosted service and the local app so they never drift."""
import os
import shutil
import tempfile

from .. import (
    process, redact, apply_to_file, scrub, anonymize,
    watermark, number, bates, stamp, select_pages, rotate, crop, protect, optimize,
)

# encryption and shrinking must wrap everything, so they always run last (optimize, then protect)
_LAST = {"optimize": 1, "protect": 2}


def _reorder(rules):
    return [r for _, r in sorted(
        enumerate(rules),
        key=lambda t: (_LAST.get(t[1].get("action") or "replace", 0), t[0]))]


def _norm(r, key):
    """Normalize scrub/anonymize results to a common {ok, count} where ok means something
    was actually changed."""
    cnt = r.get(key, 0)
    return {"ok": bool(r.get("ok")) and cnt > 0, "count": cnt,
            "error": None if cnt else "no matching data found"}


def _run(action, cur, nxt, rule):
    """Run one edit step, writing cur -> nxt. Returns (result_dict, boxes)."""
    scope = rule.get("scope", "first")
    if isinstance(scope, str) and scope.isdigit():
        scope = int(scope)
    ci, word, regex = bool(rule.get("ci")), bool(rule.get("word")), bool(rule.get("regex"))
    find = rule.get("find", "")

    if action == "redact":
        r = redact(cur, nxt, find, None, scope, ci, word, regex, True)
        return r, r.get("boxes")
    if action in ("replace", "remove"):
        rep = rule.get("replace", "") if action == "replace" else ""
        r = process(cur, nxt, find, rep, None, scope, ci, word, "auto", regex,
                    substitute=bool(rule.get("substituteFont")))
        return r, r.get("boxes")
    if action == "scrub":
        return _norm(scrub(cur, nxt, rule.get("types") or None, None, True), "scrubbed"), None
    if action == "anonymize":
        return _norm(anonymize(cur, nxt, rule.get("types") or None, rule.get("names") or None,
                               rule.get("seed")), "anonymized"), None
    if action == "watermark":
        return watermark(cur, nxt, text=rule.get("text"), image=rule.get("image"),
                         font=rule.get("font", "Helvetica"), size=float(rule.get("size", 48)),
                         color=tuple(rule.get("color", (0.5, 0.5, 0.5))),
                         opacity=float(rule.get("opacity", 0.3)), rotation=float(rule.get("rotation", 45)),
                         tile=bool(rule.get("tile")), position=rule.get("position", "center"),
                         under=bool(rule.get("under")), scale=float(rule.get("scale", 0.5))), None
    if action == "number":
        return number(cur, nxt, fmt=rule.get("format", "Page {n} of {total}"),
                      position=rule.get("position", "bottom-center"), start=int(rule.get("start", 1))), None
    if action == "bates":
        return bates(cur, nxt, prefix=rule.get("prefix", ""), digits=int(rule.get("digits", 6)),
                     start=int(rule.get("start", 1)), position=rule.get("position", "bottom-right")), None
    if action == "stamp":
        return stamp(cur, nxt, image=rule.get("image"), stamp_pdf=rule.get("stampPdf"),
                     position=rule.get("position", "center"), scale=float(rule.get("scale", 0.4)),
                     opacity=float(rule.get("opacity", 1.0)), under=bool(rule.get("under"))), None
    if action == "pages":
        return select_pages(cur, nxt, keep=rule.get("keep"), drop=rule.get("drop")), None
    if action == "rotate":
        return rotate(cur, nxt, int(rule.get("degrees", 90)), rule.get("pages")), None
    if action == "crop":
        margins = rule.get("margins")
        box = rule.get("box")
        return crop(cur, nxt, margins=tuple(margins) if margins else None,
                    box=tuple(box) if box else None, pages=rule.get("pages")), None
    if action == "optimize":
        return optimize(cur, nxt, downsample_dpi=rule.get("downsampleDpi"),
                        jpeg_quality=rule.get("jpegQuality")), None
    if action == "protect":
        return protect(cur, nxt, user_password=rule.get("password", ""), owner_password=rule.get("owner"),
                       allow_print=rule.get("allowPrint", True), allow_copy=rule.get("allowCopy", True),
                       allow_modify=rule.get("allowModify", True)), None
    return {"ok": False, "error": f"unknown action {action!r}"}, None


def apply_actions(input_path, output_path, rules):
    """Returns {applied, total, all_applied, rules:[...], boxes:[...]}. `boxes` are the
    applied-edit boxes (for preview highlights)."""
    report = []
    cur = input_path
    tmps = []
    meta_sets = {}
    strip_meta = False
    all_boxes = []

    for rule in _reorder(rules):
        action = rule.get("action") or "replace"

        if action == "meta":
            field = rule.get("metaField")
            meta_sets[field] = rule.get("metaValue", "") or ""
            report.append({"action": "meta", "metaField": field,
                           "replace": rule.get("metaValue", ""), "applied": True})
            continue
        if action == "stripmeta":
            strip_meta = True
            report.append({"action": "stripmeta", "applied": True})
            continue

        nxt = tempfile.mktemp(suffix=".pdf")
        r, boxes = _run(action, cur, nxt, rule)
        applied = bool(r.get("ok"))
        entry = {"action": action, "applied": applied}
        for k in ("count", "pages", "reason", "error", "refused", "font", "saved", "before", "after", "substituted"):
            if r.get(k) is not None:
                entry[k] = r[k]
        if action in ("replace", "redact", "remove"):
            entry["find"] = find_of(rule)
        if action == "replace":
            entry["replace"] = rule.get("replace", "")
        if applied:
            cur = nxt
            tmps.append(nxt)
            all_boxes.extend(boxes or [])
        elif os.path.exists(nxt):
            os.remove(nxt)
        report.append(entry)

    shutil.copyfile(cur, output_path)
    for t in tmps:
        if os.path.abspath(t) != os.path.abspath(output_path) and os.path.exists(t):
            os.remove(t)
    if strip_meta or meta_sets:
        apply_to_file(output_path, strip_meta, meta_sets or None)

    applied_count = sum(1 for e in report if e["applied"])
    return {"applied": applied_count, "total": len(rules),
            "all_applied": applied_count == len(rules), "rules": report, "boxes": all_boxes}


def find_of(rule):
    return rule.get("find", "")
