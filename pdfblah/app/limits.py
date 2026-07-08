"""Upload and rule limits. Defaults match the hosted service; pass a looser Limits
(e.g. LOCAL) when running on the user's own machine."""
import os
from dataclasses import dataclass

import pikepdf

META_FIELDS = {"Title", "Author", "Subject", "Keywords", "Creator", "Producer"}
DETECTOR_TYPES = {"email", "iban", "credit_card", "ssn", "phone", "date", "amount"}
# edit actions that don't take a "find" (marks, page ops, protect, optimize)
NOFIND_ACTIONS = {"watermark", "number", "bates", "stamp", "pages", "rotate", "crop",
                  "protect", "optimize"}


@dataclass(frozen=True)
class Limits:
    max_bytes: int = 20 * 1024 * 1024
    max_pages: int = 30
    max_rules: int = 50
    max_find_len: int = 500
    max_replace_len: int = 200


HOSTED = Limits()
LOCAL = Limits(max_bytes=1024 * 1024 * 1024, max_pages=5000, max_rules=1000)


def _err(code, msg):
    return {"ok": False, "code": code, "error": msg}


def validate_upload(path, n_rules, limits=HOSTED):
    """Return {"ok": True, "pages", "size"} or {"ok": False, "code", "error"}."""
    size = os.path.getsize(path)
    if size > limits.max_bytes:
        mb = max(1, round(size / 1048576))
        return _err("too_large", f"This PDF is about {mb} MB. The limit is "
                    f"{limits.max_bytes // 1048576} MB.")
    with open(path, "rb") as f:
        if f.read(5) != b"%PDF-":
            return _err("not_pdf", "That file isn't a PDF.")
    if n_rules < 1:
        return _err("no_rules", "no rules provided")
    if n_rules > limits.max_rules:
        return _err("too_many_rules", f"{n_rules} rules; limit is {limits.max_rules}")
    try:
        pdf = pikepdf.open(path)
    except pikepdf.PasswordError:
        return _err("encrypted", "PDF is password-protected, so it can't be edited")
    except Exception as e:
        return _err("corrupt", f"couldn't open PDF: {e}")
    with pdf:
        npages = len(pdf.pages)
    if npages > limits.max_pages:
        return _err("too_many_pages", f"This PDF has {npages} pages. The limit is "
                    f"{limits.max_pages}.")
    return {"ok": True, "pages": npages, "size": size}


def validate_rules(rules, limits=HOSTED):
    for r in rules:
        action = r.get("action") or "replace"
        if action == "meta":
            if r.get("metaField") not in META_FIELDS:
                return _err("bad_meta_field", "a metadata rule has an unknown field")
            if len(r.get("metaValue") or "") > limits.max_replace_len:
                return _err("meta_too_long", "a metadata value is too long")
            continue
        if action == "stripmeta":
            continue
        if action in ("scrub", "anonymize"):
            types = r.get("types")
            if types is not None and (not isinstance(types, list)
                                      or any(t not in DETECTOR_TYPES for t in types)):
                return _err("bad_types", "an unknown data type was requested")
            continue
        if action in NOFIND_ACTIONS:
            continue
        if not r.get("find"):
            return _err("blank_find", "a rule has an empty 'find'")
        if len(r.get("find", "")) > limits.max_find_len:
            return _err("find_too_long", f"a find value exceeds {limits.max_find_len} chars")
        if len(r.get("replace") or "") > limits.max_replace_len:
            return _err("replace_too_long", f"a replacement exceeds {limits.max_replace_len} chars")
    return {"ok": True}
