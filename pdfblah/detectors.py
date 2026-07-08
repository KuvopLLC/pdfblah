"""Detectors for structured personal / sensitive data.

Each detector is a regex plus an optional validator that rejects false positives
(Luhn for cards, checksum for IBAN, range checks for SSN). Used by the `scrub` and
`anonymize` commands. Names, which need real NLP to detect reliably, are handled
separately (passed in explicitly), not here.
"""
import re

try:
    from stdnum import luhn
    from stdnum import iban as _iban
except Exception:                      # pragma: no cover - dependency missing
    luhn = None
    _iban = None


def _digits(s):
    return re.sub(r"\D", "", s)


def _luhn_ok(s):
    d = _digits(s)
    if not (13 <= len(d) <= 19):
        return False
    return luhn.is_valid(d) if luhn else True


def _iban_ok(s):
    c = re.sub(r"\s", "", s).upper()
    return _iban.is_valid(c) if _iban else len(c) >= 15


def _ssn_ok(s):
    a, b, c = s.split("-")
    if a in ("000", "666") or a[0] == "9":
        return False
    return b != "00" and c != "0000"


def _phone_ok(s):
    return 7 <= len(_digits(s)) <= 15


# name -> {pattern, validate}. Order matters for scrub (more specific first).
DETECTORS = {
    "email":       {"pattern": r"[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}",
                    "validate": None},
    "iban":        {"pattern": r"\b[A-Z]{2}\d{2}(?:[ ]?[A-Za-z0-9]){11,30}\b",
                    "validate": _iban_ok},
    "credit_card": {"pattern": r"\b\d(?:[ \-]?\d){12,18}\b",
                    "validate": _luhn_ok},
    "ssn":         {"pattern": r"\b\d{3}-\d{2}-\d{4}\b",
                    "validate": _ssn_ok},
    "phone":       {"pattern": r"(?<!\d)(?:\+\d{1,3}[ .\-]?)?\(?\d{2,4}\)?[ .\-]"
                               r"\d{2,4}(?:[ .\-]\d{2,4}){1,3}(?!\d)",
                    "validate": _phone_ok},
    "date":        {"pattern": r"\b(?:\d{1,2}[/.\-]\d{1,2}[/.\-]\d{2,4}"
                               r"|\d{4}[/.\-]\d{1,2}[/.\-]\d{1,2})\b",
                    "validate": None},
    "amount":      {"pattern": r"[$€£]\s?\d{1,3}(?:[,.]\d{3})*(?:[.,]\d{2})?"
                               r"|\b\d{1,3}(?:\.\d{3})*,\d{2}\b",
                    "validate": None},
}

ALL_TYPES = list(DETECTORS)


def pattern(name):
    return DETECTORS[name]["pattern"]


def validator(name):
    return DETECTORS[name]["validate"]


def _valid_prefix(s, val):
    """Largest prefix of `s` (trimming trailing space-separated tokens) that the
    validator accepts. Handles greedy regexes that absorb a following word, e.g.
    'DE89 3704 0044 0532 0130 00 now' -> the IBAN without ' now'."""
    if val is None or val(s):
        return s
    toks = s.split(" ")
    while len(toks) > 1:
        toks.pop()
        cand = " ".join(toks)
        if val(cand):
            return cand
    return None


def find_all(text, types=None):
    """Return [(type, matched_text, start, end)] for each detected item in `text`,
    honoring validators (with trailing-token backtracking). Detections are sorted by
    position; the exact matched substrings are suitable for literal redaction."""
    out = []
    for name in (ALL_TYPES if types is None else types):
        d = DETECTORS[name]
        rx = re.compile(d["pattern"])
        val = d["validate"]
        for m in rx.finditer(text):
            s = _valid_prefix(m.group(0), val)
            if s:
                out.append((name, s, m.start(), m.start() + len(s)))
    out.sort(key=lambda t: t[2])
    return out
