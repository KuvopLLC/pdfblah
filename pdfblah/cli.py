"""Command-line interface for pdfblah.

Default form (backwards compatible):
    pdfblah in.pdf out.pdf --find OLD --replace NEW [--regex --scope all ...]
    pdfblah in.pdf out.pdf --rules rules.txt

Subcommands:
    pdfblah redact in.pdf out.pdf --find PATTERN [--regex] [--no-bar]
"""
import argparse
import json
import sys

from .engine import process, redact, apply_rules, parse_rules
from .commands import scrub, anonymize, merge, load_data
from .detectors import ALL_TYPES
from .metadata import read_metadata, edit_metadata, apply_to_file


def _parse_color(s):
    parts = [float(x) for x in s.split(",")]
    if len(parts) != 3:
        raise SystemExit("--color must be r,g,b")
    if any(p > 1 for p in parts):
        parts = [p / 255.0 for p in parts]
    return tuple(parts)


def _add_meta_flags(ap):
    ap.add_argument("--strip-metadata", action="store_true",
                    help="also remove all metadata (DocInfo + XMP) from the output")
    ap.add_argument("--set-metadata", action="append", default=[], metavar="KEY=VALUE",
                    help="also set a metadata field on the output; repeatable")


def _meta_from_args(a):
    sets = {}
    for pair in getattr(a, "set_metadata", None) or []:
        if "=" not in pair:
            raise SystemExit(f"--set-metadata needs KEY=VALUE, got {pair!r}")
        k, v = pair.split("=", 1)
        sets[k.strip()] = v
    return bool(getattr(a, "strip_metadata", False)), (sets or None)


def _apply_meta(paths, strip, sets):
    if strip or sets:
        for p in paths:
            apply_to_file(p, strip, sets)


def _replace_main(argv):
    ap = argparse.ArgumentParser(
        prog="pdfblah",
        description="Real find and replace on the actual text in a PDF. "
                    "No overlay, metadata preserved, alignment auto-detected.")
    ap.add_argument("input", help="input PDF")
    ap.add_argument("output", help="output PDF (never overwrites the input)")
    ap.add_argument("--find", help="text to find")
    ap.add_argument("--replace", default="", help="replacement text (empty deletes the text)")
    ap.add_argument("--rules", metavar="FILE",
                    help="apply many rules from a file, one 'FIND | REPLACE | FLAGS' per line")
    ap.add_argument("--scope", default="first", metavar="WHICH",
                    help="first (default), all, or a number for the Nth match")
    ap.add_argument("--ci", action="store_true", help="ignore case")
    ap.add_argument("--word", action="store_true", help="whole word only")
    ap.add_argument("--regex", action="store_true",
                    help="treat --find as a regular expression (backrefs like \\1 work in --replace)")
    ap.add_argument("--page", type=int, help="limit to this page number")
    _add_meta_flags(ap)
    ap.add_argument("--json", action="store_true", help="print the full JSON report")
    a = ap.parse_args(argv)
    strip_meta, set_meta = _meta_from_args(a)

    if a.rules:
        with open(a.rules, encoding="utf-8") as fh:
            pr = parse_rules(fh.read())
        rules = pr["rules"]
        # rules-file directives combine with CLI flags
        strip_meta = strip_meta or pr["strip_meta"]
        set_meta = {**(pr["set_meta"] or {}), **(set_meta or {})} or None
        if not rules and not strip_meta and not set_meta:
            print(f"no rules found in {a.rules}", file=sys.stderr)
            return 2
        rep = apply_rules(a.input, a.output, rules, strip_meta, set_meta)
        if a.json:
            print(json.dumps(rep, indent=2))
        else:
            print(f"{rep['applied']}/{rep['total']} rules applied  ->  {a.output}")
            for r in rep["rules"]:
                mark = "ok  " if r["applied"] else "skip"
                extra = f" (x{r['count']})" if r.get("count") else ""
                why = "" if r["applied"] else "  " + (r.get("reason") or r.get("error") or "not applied")
                print(f"  [{mark}] {r['find']!r} -> {r['replace']!r}{extra}{why}")
            if rep.get("meta_changed"):
                print(f"  metadata: {', '.join(rep['meta_changed'])}")
        return 0 if (rep["applied"] or strip_meta or set_meta) else 1

    if not a.find:
        ap.error("give --find (with --replace), or --rules FILE")
    scope = int(a.scope) if a.scope.isdigit() else a.scope
    r = process(a.input, a.output, a.find, a.replace, a.page, scope, a.ci, a.word,
                regex=a.regex)
    if r.get("ok"):
        _apply_meta([a.output], strip_meta, set_meta)
    if a.json:
        print(json.dumps({k: v for k, v in r.items() if k != "boxes"}, indent=2))
    elif r.get("ok"):
        print(f"replaced {r['count']} match(es) of {a.find!r}  ->  {a.output}")
    elif r.get("refused"):
        print(f"refused: {r.get('reason')}", file=sys.stderr)
    else:
        print(f"failed: {r.get('error')}", file=sys.stderr)
    return 0 if r.get("ok") else (3 if r.get("refused") else 1)


def _redact_main(argv):
    ap = argparse.ArgumentParser(
        prog="pdfblah redact",
        description="Truly remove matched text from a PDF (gone from copy and search), "
                    "and by default draw a bar over each spot that was removed.")
    ap.add_argument("input", help="input PDF")
    ap.add_argument("output", help="output PDF (never overwrites the input)")
    ap.add_argument("--find", required=True, help="text or pattern to remove")
    ap.add_argument("--regex", action="store_true", help="treat --find as a regular expression")
    ap.add_argument("--scope", default="all", metavar="WHICH",
                    help="all (default), first, or a number for the Nth match")
    ap.add_argument("--ci", action="store_true", help="ignore case")
    ap.add_argument("--word", action="store_true", help="whole word only")
    ap.add_argument("--page", type=int, help="limit to this page number")
    ap.add_argument("--no-bar", dest="bar", action="store_false",
                    help="remove the text only, draw no bar")
    ap.add_argument("--color", default="0,0,0",
                    help="bar color as r,g,b (0..1 or 0..255); default black")
    _add_meta_flags(ap)
    ap.add_argument("--json", action="store_true", help="print the full JSON report")
    a = ap.parse_args(argv)
    scope = int(a.scope) if a.scope.isdigit() else a.scope
    r = redact(a.input, a.output, a.find, a.page, scope, a.ci, a.word, a.regex,
               a.bar, _parse_color(a.color))
    if r.get("ok"):
        _apply_meta([a.output], *_meta_from_args(a))
    if a.json:
        print(json.dumps({k: v for k, v in r.items() if k != "boxes"}, indent=2))
    elif r.get("ok"):
        tail = f", drew {r['bars']} bar(s)" if a.bar else ""
        print(f"redacted {r['redacted']} match(es) of {a.find!r}{tail}  ->  {a.output}")
    elif r.get("refused"):
        print(f"refused: {r.get('reason')}", file=sys.stderr)
    else:
        print(f"failed: {r.get('error')}", file=sys.stderr)
    return 0 if r.get("ok") else (3 if r.get("refused") else 1)


def _scrub_main(argv):
    ap = argparse.ArgumentParser(
        prog="pdfblah scrub",
        description="Find and remove structured personal data (email, IBAN, card, SSN, "
                    "phone) from a PDF, or mask it. Card/IBAN are checksum-validated.")
    ap.add_argument("input", help="input PDF")
    ap.add_argument("output", help="output PDF (never overwrites the input)")
    ap.add_argument("--types", help="comma-separated subset of: " + ",".join(ALL_TYPES)
                    + " (default: email,iban,credit_card,ssn,phone)")
    ap.add_argument("--mask", help="replace matches with this text instead of removing them")
    ap.add_argument("--bar", action="store_true", help="draw a bar over removed text")
    ap.add_argument("--page", type=int, help="limit to this page number")
    _add_meta_flags(ap)
    ap.add_argument("--json", action="store_true", help="print the full JSON report")
    a = ap.parse_args(argv)
    types = None
    if a.types:
        types = [t.strip() for t in a.types.split(",") if t.strip()]
        bad = [t for t in types if t not in ALL_TYPES]
        if bad:
            ap.error(f"unknown type(s): {', '.join(bad)}. choose from {', '.join(ALL_TYPES)}")
    r = scrub(a.input, a.output, types, a.mask, a.bar, a.page)
    if r.get("ok"):
        _apply_meta([a.output], *_meta_from_args(a))
    if a.json:
        print(json.dumps(r, indent=2))
    else:
        verb = "masked" if a.mask else "removed"
        print(f"{verb} {r['scrubbed']} item(s)  ->  {a.output}")
        for it in r["items"]:
            print(f"  [{verb[:4]}] {it['type']} (x{it['count']})")
    return 0 if r.get("ok") else 1


def _anonymize_main(argv):
    ap = argparse.ArgumentParser(
        prog="pdfblah anonymize",
        description="Replace detected data with realistic, shape-preserving fakes so a "
                    "document is safe to share. Same value maps to the same fake.")
    ap.add_argument("input", help="input PDF")
    ap.add_argument("output", help="output PDF (never overwrites the input)")
    ap.add_argument("--types", help="comma-separated subset of: " + ",".join(ALL_TYPES)
                    + " (default: email,iban,credit_card,ssn,phone)")
    ap.add_argument("--names", help="comma-separated names to replace with fake names")
    ap.add_argument("--seed", type=int, help="seed for reproducible output")
    ap.add_argument("--page", type=int, help="limit to this page number")
    _add_meta_flags(ap)
    ap.add_argument("--json", action="store_true", help="print the full JSON report")
    a = ap.parse_args(argv)
    types = None
    if a.types is not None:
        types = [t.strip() for t in a.types.split(",") if t.strip()]
        bad = [t for t in types if t not in ALL_TYPES]
        if bad:
            ap.error(f"unknown type(s): {', '.join(bad)}. choose from {', '.join(ALL_TYPES)}")
    names = [n.strip() for n in a.names.split(",") if n.strip()] if a.names else None
    r = anonymize(a.input, a.output, types, names, a.seed, a.page)
    if r.get("ok"):
        _apply_meta([a.output], *_meta_from_args(a))
    if a.json:
        print(json.dumps(r, indent=2))
    else:
        print(f"anonymized {r['anonymized']} item(s)  ->  {a.output}")
        for it in r["items"]:
            print(f"  [fake] {it['type']} (x{it['count']})")
    return 0 if r.get("ok") else 1


def _merge_main(argv):
    ap = argparse.ArgumentParser(
        prog="pdfblah merge",
        description="Fill a template PDF once per data row: every {{column}} placeholder "
                    "is replaced by that row's value. One output PDF per row.")
    ap.add_argument("template", help="template PDF with {{column}} placeholders")
    ap.add_argument("data", help="CSV or JSON file of rows")
    ap.add_argument("--out", required=True, metavar="DIR", help="output directory")
    ap.add_argument("--name-col", help="column to name each output file after (else row_NNNN)")
    ap.add_argument("--placeholder", default="{{FIELD}}",
                    help="placeholder form; the word FIELD is replaced by the column name "
                         "(default: {{FIELD}})")
    _add_meta_flags(ap)
    ap.add_argument("--json", action="store_true", help="print the full JSON report")
    a = ap.parse_args(argv)
    rows = load_data(a.data)
    if not rows:
        print(f"no rows found in {a.data}", file=sys.stderr)
        return 2
    r = merge(a.template, rows, a.out, a.placeholder, a.name_col)
    if r.get("ok"):
        _apply_meta([o["output"] for o in r["outputs"]], *_meta_from_args(a))
    if a.json:
        print(json.dumps(r, indent=2))
    else:
        print(f"wrote {r['count']} PDF(s) to {a.out}/")
        for o in r["outputs"]:
            print(f"  {o['output']}  ({o['applied']}/{o['total']} fields)")
    return 0 if r.get("ok") else 1


def _print_meta(rep):
    print(f"PDF {rep['pdf_version']}  ·  {rep['pages']} page(s)  ·  "
          f"encrypted: {rep['encrypted']}")
    if rep.get("page1_size_pt"):
        w, h = rep["page1_size_pt"]
        print(f"page 1 size: {w} x {h} pt")
    for section in ("docinfo", "xmp"):
        print(f"{'DocInfo' if section == 'docinfo' else 'XMP'}:")
        d = rep[section]
        if d:
            for k, v in d.items():
                print(f"  {k}: {v}")
        else:
            print("  (none)")


def _meta_main(argv):
    ap = argparse.ArgumentParser(
        prog="pdfblah meta",
        description="Report a PDF's metadata; with an output file, strip it or set "
                    "fields. Without --strip/--set, other pdfblah commands keep it intact.")
    ap.add_argument("input", help="input PDF")
    ap.add_argument("output", nargs="?", help="if given, write an edited copy")
    ap.add_argument("--strip", action="store_true",
                    help="remove all metadata (DocInfo + XMP)")
    ap.add_argument("--set", action="append", default=[], metavar="KEY=VALUE",
                    help="set title/author/subject/keywords/creator/producer or a custom "
                         "key; repeatable")
    ap.add_argument("--json", action="store_true", help="machine-readable output")
    a = ap.parse_args(argv)
    if a.output is None:
        rep = read_metadata(a.input)
        print(json.dumps(rep, indent=2)) if a.json else _print_meta(rep)
        return 0
    sets = {}
    for pair in a.set:
        if "=" not in pair:
            ap.error(f"--set needs KEY=VALUE, got {pair!r}")
        k, v = pair.split("=", 1)
        sets[k.strip()] = v
    if not a.strip and not sets:
        ap.error("give --strip and/or --set when writing an output file")
    r = edit_metadata(a.input, a.output, a.strip, sets)
    if a.json:
        print(json.dumps(r, indent=2))
    elif r.get("ok"):
        print(f"metadata updated ({', '.join(r['changed']) or 'no change'})  ->  {a.output}")
    else:
        print(f"failed: {r.get('error')}", file=sys.stderr)
    return 0 if r.get("ok") else 1


def _gui_main(argv):
    ap = argparse.ArgumentParser(
        prog="pdfblah gui",
        description="Open the desktop app: the same PDF tool as the website, running "
                    "locally and free. Nothing is uploaded.")
    ap.parse_args(argv)
    from .gui import launch
    launch()
    return 0


HANDLERS = {"redact": _redact_main, "scrub": _scrub_main,
            "anonymize": _anonymize_main, "merge": _merge_main, "meta": _meta_main,
            "gui": _gui_main}


def _all_handlers():
    from .cli_tools import TOOL_HANDLERS  # deferred: pulls in reportlab/pikepdf helpers
    return {**HANDLERS, **TOOL_HANDLERS}


def main(argv=None):
    argv = list(sys.argv[1:] if argv is None else argv)
    if argv and argv[0] in HANDLERS:
        return HANDLERS[argv[0]](argv[1:])
    if argv and argv[0] in ("combine", "split", "pages", "rotate", "crop", "render", "clean",
                            "tidy", "compress", "find", "batch", "repair", "sanitize",
                            "recolor", "links", "extract", "protect", "unlock", "attachments",
                            "optimize", "watermark", "stamp", "number", "bates", "form",
                            "compare", "signatures", "convert", "ocr", "doctor"):
        from .cli_tools import TOOL_HANDLERS
        return TOOL_HANDLERS[argv[0]](argv[1:])
    return _replace_main(argv)


if __name__ == "__main__":
    sys.exit(main())
