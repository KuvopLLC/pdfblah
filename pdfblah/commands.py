"""High-level presets over the core engine. Each is thin sugar for the same
primitive (locate text, substitute something): scrub, anonymize, merge.

They detect exact strings in Python (via detectors) and then rewrite them with the
engine's literal find/replace, so there is no regex greediness at rewrite time.
"""
import csv
import json
import os
import random
import re
import shutil
import string
import tempfile

import pdfplumber

from .engine import process, redact, apply_rules
from .detectors import find_all

SCRUB_DEFAULT = ["email", "iban", "credit_card", "ssn", "phone"]
ANON_DEFAULT = ["email", "iban", "credit_card", "ssn", "phone"]


def _detect_unique(input_path, types, page):
    """All detected (type, exact_string) pairs in the document, de-duplicated,
    longest string first (so a longer match is removed before any substring)."""
    found = []
    with pdfplumber.open(input_path) as pdf:
        pages = range(len(pdf.pages)) if page is None else [page - 1]
        for pi in pages:
            text = pdf.pages[pi].extract_text() or ""
            for (name, s, _a, _b) in find_all(text, types):
                found.append((name, s))
    uniq, seen = [], set()
    for name, s in sorted(found, key=lambda t: -len(t[1])):
        if s not in seen:
            seen.add(s); uniq.append((name, s))
    return uniq


def _chain(input_path, output_path, jobs):
    """Apply a list of (find_string, do) steps in sequence via temp files, where
    `do(cur, nxt, s)` returns an engine result dict. Returns (applied_items, tmps)."""
    cur = input_path; tmps = []; items = []
    for name, s, do in jobs:
        nxt = tempfile.mktemp(suffix=".pdf")
        r = do(cur, nxt, s)
        if r.get("ok"):
            items.append({"type": name, "value_len": len(s),
                          "count": r.get("redacted", r.get("count", 0))})
            cur = nxt; tmps.append(nxt)
        elif os.path.exists(nxt):
            os.remove(nxt)
    shutil.copyfile(cur, output_path)
    for t in tmps:
        if os.path.abspath(t) != os.path.abspath(output_path) and os.path.exists(t):
            os.remove(t)
    return items


def scrub(input_path, output_path, types=None, mask=None, bar=False, page=None):
    """Find structured PII (email, IBAN, card, SSN, phone by default) and remove it
    (truly, from the content stream) or, with `mask`, replace it with that text.
    Never logs the actual values. Returns a status dict."""
    types = SCRUB_DEFAULT if types is None else types
    uniq = _detect_unique(input_path, types, page)

    def remover(cur, nxt, s):
        return redact(cur, nxt, s, page=page, scope="all", bar=bar)

    def masker(cur, nxt, s):
        return process(cur, nxt, s, mask, page=page, scope="all")

    do = masker if mask is not None else remover
    items = _chain(input_path, output_path, [(n, s, do) for (n, s) in uniq])
    return {"ok": True, "scrubbed": len(items), "items": items, "output": output_path}


def _digit_shape(s, rng):
    """Same string with each digit replaced by a random digit (separators kept), so
    length and layout are preserved: '4242 4242' -> '7193 8025'."""
    return "".join(str(rng.randint(0, 9)) if c.isdigit() else c for c in s)


def _iban_shape(s, rng):
    """Keep the two-letter country code, randomize the rest (letters->letters,
    digits->digits), preserving length."""
    def sub(c):
        if c.isdigit():
            return str(rng.randint(0, 9))
        if c.isalpha():
            return rng.choice(string.ascii_uppercase)
        return c
    return s[:2] + "".join(sub(c) for c in s[2:])


def _fake_for(kind, value, faker, rng):
    if kind == "email":
        return faker.email()
    if kind == "name":
        return faker.name()
    if kind == "iban":
        return _iban_shape(value, rng)
    return _digit_shape(value, rng)         # card, phone, ssn, amount, date


def anonymize(input_path, output_path, types=None, names=None, seed=None, page=None):
    """Replace detected structured data with realistic, shape-preserving fakes, and
    (via `names`) replace the names you list with fake names. The same source value
    maps to the same fake throughout; pass `seed` for reproducible output. Returns a
    status dict."""
    from faker import Faker
    types = ANON_DEFAULT if types is None else types
    faker = Faker()
    if seed is not None:
        Faker.seed(seed)
    rng = random.Random(seed)

    uniq = _detect_unique(input_path, types, page)
    pairs = list(uniq) + [("name", nm) for nm in (names or [])]

    def replacer(fake):
        return lambda cur, nxt, s: process(cur, nxt, s, fake, page=page, scope="all")

    jobs = [(kind, s, replacer(_fake_for(kind, s, faker, rng))) for (kind, s) in pairs]
    items = _chain(input_path, output_path, jobs)
    return {"ok": True, "anonymized": len(items), "items": items, "output": output_path}


def load_data(path):
    """Load merge data rows from a .json (list of objects) or .csv file."""
    if path.lower().endswith(".json"):
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, list) else [data]
    with open(path, newline="", encoding="utf-8-sig") as f:
        return list(csv.DictReader(f))


def _safe_name(s):
    return re.sub(r"[^\w.\-]+", "_", str(s)).strip("_") or "row"


def merge(template_path, rows, out_dir, placeholder="{{FIELD}}", name_col=None):
    """Fill a template PDF once per data row: every `{{column}}` placeholder (the word
    FIELD in `placeholder` is swapped for the column name) is replaced by that row's
    value. Writes one PDF per row to `out_dir`. Returns a status dict."""
    os.makedirs(out_dir, exist_ok=True)
    outputs = []
    for i, row in enumerate(rows):
        rules = [{"find": placeholder.replace("FIELD", str(k)), "replace": str(v),
                  "scope": "all"} for k, v in row.items()]
        base = _safe_name(row.get(name_col)) if name_col else f"row_{i + 1:04d}"
        out = os.path.join(out_dir, base + ".pdf")
        rep = apply_rules(template_path, out, rules)
        outputs.append({"output": out, "applied": rep["applied"], "total": rep["total"]})
    return {"ok": True, "count": len(outputs), "outputs": outputs}
