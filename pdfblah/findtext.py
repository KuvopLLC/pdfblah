"""Search for text across many PDFs at once: a folder, a tree, your whole archive.

Ctrl+F works on one open document; this is grep for the rest of them. Each match
comes back as file, page, and a snippet of the surrounding line, so the answer is
"contract-2024.pdf, page 7" rather than "somewhere in that folder".

Scanned files with no text layer can't match anything, so instead of failing
silently they are reported separately with the fix (`pdfblah ocr`). Encrypted and
unreadable files are listed too; a search that skips things quietly is worse than
no search.
"""
import os
import re


def find(pattern, paths, recursive=False, regex=False, ci=False, word=False,
         context=40, max_matches=500):
    """Search PDFs for a pattern.

    pattern      Text to find (or a regular expression with regex=True).
    paths        Files and/or directories to search.
    recursive    Descend into subdirectories of the given directories.
    ci           Ignore case.
    word         Whole words only.
    context      Characters of surrounding text in each snippet.
    max_matches  Stop after this many matches (the report says so).

    Returns {ok, files_searched, matches: [{file, page, snippet}], truncated,
             no_text: [file], errors: [{file, error}]}.
    """
    import pdfplumber

    rx = pattern if regex else re.escape(pattern)
    if word:
        rx = r"\b(?:" + rx + r")\b"
    try:
        needle = re.compile(rx, re.IGNORECASE if ci else 0)
    except re.error as e:
        return {"ok": False, "error": f"bad pattern: {e}"}

    files = _collect(paths, recursive)
    if not files:
        return {"ok": False, "error": "no PDFs found at the given paths"}

    matches, no_text, errors = [], [], []
    truncated = False
    for f in files:
        try:
            with pdfplumber.open(f) as pdf:
                any_text = False
                for i, page in enumerate(pdf.pages):
                    text = page.extract_text() or ""
                    if text.strip():
                        any_text = True
                    for m in needle.finditer(text):
                        lo = max(0, m.start() - context)
                        hi = min(len(text), m.end() + context)
                        snippet = " ".join(text[lo:hi].split())
                        matches.append({"file": f, "page": i + 1, "snippet": snippet})
                        if len(matches) >= max_matches:
                            truncated = True
                            break
                    if truncated:
                        break
                if not any_text:
                    no_text.append(f)
        except Exception as e:
            errors.append({"file": f, "error": str(e)[:200]})
        if truncated:
            break

    return {"ok": True, "files_searched": len(files), "matches": matches,
            "truncated": truncated, "no_text": no_text, "errors": errors}


def _collect(paths, recursive):
    out = []
    for p in paths:
        if os.path.isdir(p):
            if recursive:
                for root, _, names in os.walk(p):
                    out.extend(os.path.join(root, n) for n in sorted(names)
                               if n.lower().endswith(".pdf"))
            else:
                out.extend(os.path.join(p, n) for n in sorted(os.listdir(p))
                           if n.lower().endswith(".pdf"))
        elif p.lower().endswith(".pdf"):
            out.append(p)
    return out
