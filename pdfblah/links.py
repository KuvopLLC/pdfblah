"""Check every link in a PDF: the clickable kind that quietly rot.

A 1200-page manual gets hyperlinked once and edited forever after; pages move,
sections vanish, external sites die, and nobody clicks all the links again.
check_links() does exactly that: it inventories every link annotation and every
bookmark, resolves the internal ones against the pages that actually exist, and
(unless told to stay offline) knocks on each external URL.

Three honest verdicts for the outside world: reachable, broken (the server said
4xx/5xx), and unreachable (no answer at all; maybe you are offline, maybe the
site is gone). Internal targets are binary: the destination page exists or it
does not.
"""
import re


def check_links(input_path, external=True, timeout=10):
    """Inventory and verify links and bookmarks.

    external  Also contact external URLs (HEAD, then GET on a 405). False = the
              structural check only, fully offline.

    Returns {ok, pages, internal: {ok, broken: [...]}, external_urls: {ok,
             broken: [...], unreachable: [...]}, bookmarks: {ok, broken: [...]},
             checked_urls}.
    """
    import pikepdf

    with pikepdf.open(input_path) as pdf:
        page_ids = {pdf.pages[i].obj.objgen: i + 1 for i in range(len(pdf.pages))}
        named = _named_destinations(pdf)
        int_ok, int_broken, urls = 0, [], []
        for pno in range(len(pdf.pages)):
            for a in pdf.pages[pno].get("/Annots", []):
                if str(a.get("/Subtype", "")) != "/Link":
                    continue
                where = {"page": pno + 1}
                action = a.get("/A")
                if action is not None and str(action.get("/S", "")) == "/URI":
                    urls.append({**where, "url": str(action.get("/URI", ""))})
                    continue
                target = None
                if action is not None and str(action.get("/S", "")) == "/GoTo":
                    target = action.get("/D")
                elif a.get("/Dest") is not None:
                    target = a.get("/Dest")
                if target is None:
                    continue
                problem = _resolve(target, named, page_ids)
                if problem:
                    int_broken.append({**where, "problem": problem})
                else:
                    int_ok += 1
        bm_ok, bm_broken = _check_outlines(pdf, named, page_ids)

    ext = {"ok": 0, "broken": [], "unreachable": []}
    unique = {}
    for u in urls:
        unique.setdefault(u["url"], []).append(u["page"])
    if external:
        for url, pages in unique.items():
            verdict, detail = _probe(url, timeout)
            if verdict == "ok":
                ext["ok"] += 1
            else:
                ext[verdict].append({"url": url, "pages": pages, "detail": detail})

    return {"ok": True, "pages": len(page_ids),
            "internal": {"ok": int_ok, "broken": int_broken},
            "bookmarks": {"ok": bm_ok, "broken": bm_broken},
            "external_urls": ext, "checked_urls": len(unique) if external else 0,
            "unchecked_urls": 0 if external else len(unique)}


def _resolve(target, named, page_ids):
    """None when the destination resolves to a real page, else a problem string."""
    import pikepdf
    if isinstance(target, (pikepdf.String, pikepdf.Name)) or isinstance(target, str):
        name = str(target).lstrip("/")
        dest = named.get(name)
        if dest is None:
            return f"named destination '{name}' does not exist"
        target = dest
    try:
        first = target[0]
        if getattr(first, "objgen", None) in page_ids:
            return None
        return "destination page is not in the document"
    except Exception:
        return "destination is malformed"


def _named_destinations(pdf):
    """All named destinations: the /Names tree and the old-style /Dests dict."""
    out = {}

    def walk(node):
        if node is None:
            return
        kids = node.get("/Kids")
        if kids is not None:
            for k in kids:
                walk(k)
        names = node.get("/Names")
        if names is not None:
            for i in range(0, len(names) - 1, 2):
                out[str(names[i])] = _dest_array(names[i + 1])

    names_root = pdf.Root.get("/Names")
    if names_root is not None:
        walk(names_root.get("/Dests"))
    old = pdf.Root.get("/Dests")
    if old is not None:
        for k in old.keys():
            out[str(k).lstrip("/")] = _dest_array(old[k])
    return out


def _safe_get(obj, key, default=None):
    """dict-style get that tolerates arrays and scalars (old pikepdf raises
    ValueError instead of lacking .get on non-dictionaries)."""
    try:
        v = obj.get(key)
    except (AttributeError, TypeError, ValueError):
        return default
    return default if v is None else v


def _dest_array(obj):
    d = _safe_get(obj, "/D")
    return d if d is not None else obj


def _check_outlines(pdf, named, page_ids):
    ok, broken = 0, []
    seen = set()

    def walk(item):
        nonlocal ok
        while item is not None:
            if item.objgen in seen:  # cycles exist in the wild
                return
            seen.add(item.objgen)
            title = str(item.get("/Title", "?"))
            target = item.get("/Dest")
            action = item.get("/A")
            if target is None and action is not None and \
                    str(action.get("/S", "")) == "/GoTo":
                target = action.get("/D")
            if target is not None:
                problem = _resolve(target, named, page_ids)
                if problem:
                    broken.append({"bookmark": title, "problem": problem})
                else:
                    ok += 1
            first = item.get("/First")
            if first is not None:
                walk(first)
            item = item.get("/Next")

    outlines = pdf.Root.get("/Outlines")
    if outlines is not None:
        walk(outlines.get("/First"))
    return ok, broken


def _probe(url, timeout):
    """('ok'|'broken'|'unreachable', detail) for one URL."""
    import urllib.error
    import urllib.request
    if not re.match(r"^https?://", url or ""):
        return "broken", "not an http(s) URL"
    for method in ("HEAD", "GET"):
        req = urllib.request.Request(url, method=method,
                                     headers={"User-Agent": "pdfblah-linkcheck"})
        try:
            with urllib.request.urlopen(req, timeout=timeout) as r:
                return "ok", f"HTTP {r.status}"
        except urllib.error.HTTPError as e:
            if e.code in (405, 403) and method == "HEAD":
                continue  # some servers refuse HEAD; try GET before judging
            return "broken", f"HTTP {e.code}"
        except Exception as e:
            return "unreachable", str(e)[:120]
    return "broken", "HTTP 405"
