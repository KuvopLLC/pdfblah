"""Local web server for the pdfblah app. `pdfblah gui` starts this on 127.0.0.1 and opens
your browser at it: the same Edit + Tools app as the website, running entirely on your
machine. It serves the shared app UI and does all the PDF work over local HTTP (PDFs travel
as base64 JSON, so there's no dependency on the stdlib `cgi` module, gone in Python 3.13).
Outputs come back as normal browser downloads. Nothing is uploaded; nothing leaves the box."""
import base64
import io
import json
import os
import secrets
import shutil
import tempfile
import threading
import time
import uuid
import zipfile
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from importlib.resources import files

from ..app import (
    analyze_pdf, apply_actions, render_preview, validate_rules, validate_upload, LOCAL,
    web_dir,
)
from .. import (
    combine, split, render_pages, extract_text, extract_images, compare_pdfs,
    list_signatures, validate_signatures, form_list, form_fill, attachments,
)
import pikepdf

_TYPES = {
    ".html": "text/html; charset=utf-8", ".js": "text/javascript; charset=utf-8",
    ".mjs": "text/javascript; charset=utf-8", ".css": "text/css; charset=utf-8",
    ".txt": "text/plain; charset=utf-8",
}
_ASSETS = ("gate.mjs", "app.js", "app.css", "tool.js", "tool.css", "sample-rules.txt",
           "workbench.js", "workbench.css")
_PENDING = {}  # download id -> (bytes, filename, content-type); one-shot

# ---- workbench sessions (multi-file, upload-once-operate-by-reference) ----
# sid -> {"dir": tempdir, "files": {fid: {name, path, pages, analysis, isScan, after?}},
#         "assets": {aid: path}, "ts": epoch}
# after (the stack applied to this file, cached by stack-hash so page nav / repeat previews
# of an unchanged stack never re-apply) -> {"hash", "path", "pages", "report"}
_SESSIONS = {}
_SESSION_TTL = 3600

# PDFium is NOT thread-safe: two renders on different ThreadingHTTPServer threads segfault
# the whole process (seen in the wild — the workbench overlaps thumbnail, preview, and add
# requests by design). Every pdfium-touching call goes through this lock; renders are fast
# (~0.1-0.4s) so serializing them is invisible to a single local user.
_PDFIUM_LOCK = threading.Lock()


def _drop_session(sid):
    s = _SESSIONS.pop(sid, None)
    if s:
        shutil.rmtree(s["dir"], ignore_errors=True)


def _reap_sessions():
    now = time.time()
    for sid in [k for k, v in _SESSIONS.items() if now - v["ts"] > _SESSION_TTL]:
        _drop_session(sid)


def _stack_hash(rules):
    import hashlib
    return hashlib.sha1(json.dumps(rules, sort_keys=True).encode()).hexdigest()[:16]


def _page_count(path):
    with pikepdf.open(path) as pdf:
        return len(pdf.pages)


def _recipes_path():
    base = os.environ.get("XDG_CONFIG_HOME") or os.path.expanduser("~/.config")
    return os.path.join(base, "pdfblah", "recipes.json")


def _load_recipes():
    try:
        with open(_recipes_path(), encoding="utf-8") as f:
            data = json.load(f)
        return data.get("recipes", [])
    except (OSError, ValueError):
        return []


def _save_recipes(recipes):
    path = _recipes_path()
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump({"recipes": recipes}, f, indent=1)
    os.replace(tmp, path)  # atomic: a crash mid-write never corrupts the store


def _looks_like_scan(path):
    """A scan = image pages with no selectable text. Cheap check on the first few pages."""
    try:
        import pdfplumber
        with pdfplumber.open(path) as pl:
            for pg in pl.pages[:3]:
                if (pg.extract_text() or "").strip():
                    return False
        return True
    except Exception:
        return False


def _desktop_index():
    return files("pdfblah.gui").joinpath("index.html").read_bytes()


def _desktop_workbench():
    return files("pdfblah.gui").joinpath("workbench.html").read_bytes()


def _stash(data, filename, ctype="application/pdf"):
    fid = secrets.token_urlsafe(8)
    _PENDING[fid] = (data, filename, ctype)
    return fid


def _read_bytes(path):
    with open(path, "rb") as f:
        return f.read()


def _zip(files_):
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        for name, path in files_:
            z.write(path, arcname=name)
    return buf.getvalue()


class _Handler(BaseHTTPRequestHandler):
    # keep-alive: every response sets Content-Length (_send/_json; send_error does its own),
    # so HTTP/1.1 is safe — and it spares a TCP handshake per request, which matters once the
    # workbench's live preview starts making many small calls
    protocol_version = "HTTP/1.1"

    def log_message(self, *a):
        pass

    def _send(self, body, ctype, extra=None):
        self.send_response(200)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        for k, v in (extra or {}).items():
            self.send_header(k, v)
        self.end_headers()
        self.wfile.write(body)

    def _json(self, obj, code=200):
        body = json.dumps(obj).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _body(self):
        n = int(self.headers.get("Content-Length", 0) or 0)
        return self.rfile.read(n) if n else b""

    @staticmethod
    def _tmp(data_b64, suffix=".pdf"):
        fd, path = tempfile.mkstemp(suffix=suffix)
        with os.fdopen(fd, "wb") as f:
            f.write(base64.b64decode(data_b64 or ""))
        return path

    @staticmethod
    def _stem(name):
        base = os.path.basename(name or "document.pdf")
        return base[:-4] if base.lower().endswith(".pdf") else base

    # ---- GET ----
    def do_GET(self):
        path = self.path.split("?", 1)[0]
        if path in ("/", "/index.html"):
            self._send(_desktop_index(), _TYPES[".html"]); return
        if path in ("/workbench", "/workbench.html"):
            self._send(_desktop_workbench(), _TYPES[".html"]); return
        name = path.lstrip("/")
        if name in _ASSETS:
            ext = "." + name.rsplit(".", 1)[-1]
            self._send((web_dir() / name).read_bytes(), _TYPES.get(ext, "application/octet-stream")); return
        if path.startswith("/download/"):
            item = _PENDING.pop(path[len("/download/"):], None)
            if not item:
                self.send_error(404); return
            data, fname, ctype = item
            self._send(data, ctype, {"Content-Disposition": f'attachment; filename="{fname}"'}); return
        self.send_error(404)

    # ---- POST dispatch ----
    def do_POST(self):
        path = self.path.split("?", 1)[0].lstrip("/")
        try:
            p = json.loads(self._body() or b"{}")
        except ValueError:
            self._json({"ok": False, "error": "bad request"}, 400); return
        fn = getattr(self, "post_" + path, None)
        if fn is None:
            self.send_error(404); return
        try:
            fn(p)
        except Exception as e:  # noqa: BLE001 - surface engine errors to the UI
            self._json({"ok": False, "error": str(e)})

    def post_analyze(self, p):
        src = self._tmp(p.get("pdf"))
        try:
            self._json(analyze_pdf(src, LOCAL))
        finally:
            os.remove(src)

    def post_apply(self, p):  # the Edit pipeline
        rules = p.get("rules") or []
        vr = validate_rules(rules, LOCAL)
        if not vr.get("ok"):
            self._json({"ok": False, "error": vr.get("error", "invalid changes")}); return
        src = self._tmp(p.get("pdf"))
        vu = validate_upload(src, max(1, len(rules)), LOCAL)
        if not vu.get("ok"):
            os.remove(src); self._json({"ok": False, "error": vu.get("error", "can't edit this PDF")}); return
        # write any image assets (watermark/stamp) referenced by index
        assets = self._write_assets(p.get("assets") or {})
        for r in rules:
            if r.get("assetImage") is not None and r["assetImage"] in assets:
                r["image"] = assets[r["assetImage"]]
            if r.get("assetPdf") is not None and r["assetPdf"] in assets:
                r["stampPdf"] = assets[r["assetPdf"]]
        out = tempfile.mktemp(suffix=".pdf")
        try:
            report = apply_actions(src, out, rules)
            fid = _stash(_read_bytes(out), f"{self._stem(p.get('name'))}-edited.pdf")
        finally:
            for x in [src, out] + list(assets.values()):
                if os.path.exists(x):
                    os.remove(x)
        self._json({"ok": True, "report": report, "fileId": fid})

    # ---- workbench sessions ----
    def post_wbsession(self, p):
        _reap_sessions()
        sid = uuid.uuid4().hex
        _SESSIONS[sid] = {"dir": tempfile.mkdtemp(prefix="pdfblah_wb_"), "files": {}, "ts": time.time()}
        import importlib.util
        formats = {"word": importlib.util.find_spec("pdf2docx") is not None}
        self._json({"ok": True, "session": sid, "formats": formats})

    def post_wbadd(self, p):
        s = _SESSIONS.get(p.get("session"))
        if not s:
            self._json({"ok": False, "code": "no_session", "error": "session expired, reload the page"}, 404); return
        s["ts"] = time.time()
        fid = uuid.uuid4().hex
        path = os.path.join(s["dir"], fid + ".pdf")
        with open(path, "wb") as f:
            f.write(base64.b64decode(p.get("pdf") or ""))
        try:
            analysis = analyze_pdf(path, LOCAL)
        except Exception as e:  # noqa: BLE001
            os.remove(path); self._json({"ok": False, "error": str(e)}); return
        if not analysis.get("ok"):
            os.remove(path); self._json(analysis); return
        name = os.path.basename(p.get("name") or "document.pdf")
        is_scan = _looks_like_scan(path)
        s["files"][fid] = {"name": name, "path": path, "pages": analysis.get("pages"),
                           "analysis": analysis, "isScan": is_scan}
        self._json({"ok": True, "fileId": fid, "name": name, "pages": analysis.get("pages"),
                    "isScan": is_scan, "analysis": analysis})

    def post_wbrender(self, p):
        s = _SESSIONS.get(p.get("session"))
        if not s:
            self._json({"ok": False, "code": "no_session", "error": "session expired"}, 404); return
        f = s["files"].get(p.get("file"))
        if not f:
            self._json({"ok": False, "error": "file not found"}, 404); return
        s["ts"] = time.time()
        page = max(1, int(p.get("page", 1)))
        dpi = int(p.get("dpi", 110))
        outdir = tempfile.mkdtemp()
        try:
            with _PDFIUM_LOCK:
                r = render_pages(f["path"], outdir, pages=str(page), dpi=dpi, fmt="png")
            if not r.get("ok") or not r.get("outputs"):
                self._json({"ok": False, "error": "could not render that page"}); return
            img = "data:image/png;base64," + base64.b64encode(_read_bytes(r["outputs"][0])).decode()
            self._json({"ok": True, "image": img, "page": page, "pages": f["pages"]})
        finally:
            _cleanup(outdir)

    def post_wbasset(self, p):
        """Store a stack asset (watermark/stamp image or PDF) once per session; rules then
        reference it by id — the same upload-once model as the files themselves."""
        s = _SESSIONS.get(p.get("session"))
        if not s:
            self._json({"ok": False, "code": "no_session", "error": "session expired"}, 404); return
        s["ts"] = time.time()
        aid = uuid.uuid4().hex
        ext = os.path.splitext(os.path.basename(p.get("name") or ""))[1].lower() or ".bin"
        path = os.path.join(s["dir"], "asset-" + aid + ext)
        with open(path, "wb") as f:
            f.write(base64.b64decode(p.get("data") or ""))
        s.setdefault("assets", {})[aid] = path
        self._json({"ok": True, "assetId": aid})

    @staticmethod
    def _wb_after(s, f, rules):
        """The stack applied to a stored file, cached by stack-hash — page nav, repeat
        previews, and downloads of an unchanged stack never re-apply. Hashes the rules as
        sent, then maps session assets into COPIES (mutating the caller's rules would change
        the hash between files of the same download). Returns (path, pages, report)."""
        if not rules:
            return f["path"], f["pages"], None
        h = _stack_hash(rules)
        assets = s.get("assets", {})
        rules = [dict(r) for r in rules]
        for r in rules:
            if r.get("assetImage") in assets:
                r["image"] = assets[r["assetImage"]]
            if r.get("assetPdf") in assets:
                r["stampPdf"] = assets[r["assetPdf"]]
        after = f.get("after")
        if not after or after["hash"] != h:
            out = f["path"][:-4] + ".after.pdf"
            tmp = out + ".tmp"
            try:
                report = apply_actions(f["path"], tmp, rules)
                os.replace(tmp, out)  # atomic: concurrent previews never see a partial PDF
            finally:
                _cleanup(tmp)
            after = {"hash": h, "path": out, "pages": _page_count(out), "report": report}
            f["after"] = after
        return after["path"], after["pages"], after["report"]

    def post_wbpreview(self, p):
        """Render page N of a file with the stack applied — the live After view."""
        s = _SESSIONS.get(p.get("session"))
        if not s:
            self._json({"ok": False, "code": "no_session", "error": "session expired"}, 404); return
        f = s["files"].get(p.get("file"))
        if not f:
            self._json({"ok": False, "error": "file not found"}, 404); return
        s["ts"] = time.time()
        rules = p.get("rules") or []
        dpi = int(p.get("dpi", 110))
        if rules:
            vr = validate_rules(rules, LOCAL)
            if not vr.get("ok"):
                self._json(vr); return
        src, pages, report = self._wb_after(s, f, rules)
        page = min(max(1, int(p.get("page", 1))), max(1, pages))
        outdir = tempfile.mkdtemp()
        try:
            png = os.path.join(outdir, "page.png")
            with _PDFIUM_LOCK:
                render_preview(src, png, pages=(page,), dpi=dpi, max_h=2600,
                               highlights=(report or {}).get("boxes"), watermark=False)
            img = "data:image/png;base64," + base64.b64encode(_read_bytes(png)).decode()
        finally:
            _cleanup(outdir)
        self._json({"ok": True, "image": img, "page": page, "pages": pages, "report": report})

    def post_wboutput(self, p):
        """Produce the session's output: the stack applied to every chosen file, delivered
        as PDF / Word / PNG / Text, per-file or merged. One download always: the file itself
        when there's a single output, a zip otherwise."""
        s = _SESSIONS.get(p.get("session"))
        if not s:
            self._json({"ok": False, "code": "no_session", "error": "session expired"}, 404); return
        s["ts"] = time.time()
        recs = [s["files"][fid] for fid in (p.get("files") or []) if fid in s["files"]]
        if not recs:
            self._json({"ok": False, "error": "no files selected"}); return
        rules = p.get("rules") or []
        if rules:
            vr = validate_rules(rules, LOCAL)
            if not vr.get("ok"):
                self._json(vr); return
        fmt = (p.get("format") or "pdf").lower()
        opts = p.get("options") or {}
        # protect/optimize are output-stage: applied on top of the stack, never previewed
        extras = []
        if opts.get("downsampleDpi"):
            extras.append({"action": "optimize", "downsampleDpi": int(opts["downsampleDpi"])})
        if opts.get("password"):
            extras.append({"action": "protect", "password": str(opts["password"])})
        merge = bool(p.get("merge")) and fmt == "pdf" and len(recs) > 1
        stem = self._stem
        outdir = tempfile.mkdtemp()
        outputs = []  # (zip name, path)
        try:
            afters = [(f["name"], self._wb_after(s, f, rules)[0]) for f in recs]
            edited = bool(rules) or bool(extras)
            if merge:
                m = os.path.join(outdir, "pdfblah-merged.pdf")
                r = combine([path for _, path in afters], m)
                if not r.get("ok"):
                    self._json(r); return
                afters = [("pdfblah-merged.pdf", m)]
            if fmt == "pdf":
                for name, path in afters:
                    out_name = stem(name) + ("-edited.pdf" if edited and not merge else ".pdf")
                    if extras:
                        out = os.path.join(outdir, uuid.uuid4().hex + ".pdf")
                        apply_actions(path, out, [dict(r) for r in extras])
                        path = out
                    outputs.append((out_name, path))
            elif fmt == "png":
                for name, path in afters:
                    sub = os.path.join(outdir, uuid.uuid4().hex)
                    with _PDFIUM_LOCK:
                        r = render_pages(path, sub, dpi=int(p.get("dpi", 150)), fmt="png", prefix=stem(name))
                    if not r.get("ok"):
                        self._json(r); return
                    outputs += [(os.path.basename(x), x) for x in r["outputs"]]
            elif fmt == "text":
                for name, path in afters:
                    out = os.path.join(outdir, stem(name) + ".txt")
                    r = extract_text(path, out)
                    if not r.get("ok"):
                        self._json(r); return
                    outputs.append((stem(name) + ".txt", out))
            elif fmt == "word":
                from ..convert import pdf_to_word
                for name, path in afters:
                    out = os.path.join(outdir, stem(name) + ".docx")
                    r = pdf_to_word(path, out)
                    if not r.get("ok"):
                        self._json(r); return
                    outputs.append((stem(name) + ".docx", out))
            else:
                self._json({"ok": False, "error": f"unsupported format {fmt!r}"}); return
            if len(outputs) == 1:
                name, path = outputs[0]
                ctype = {"pdf": "application/pdf", "png": "image/png", "text": "text/plain; charset=utf-8",
                         "word": "application/vnd.openxmlformats-officedocument.wordprocessingml.document"}[fmt]
                did = _stash(_read_bytes(path), name, ctype)
            else:
                name = "pdfblah-output.zip"
                did = _stash(_zip(outputs), name, "application/zip")
            self._json({"ok": True, "fileId": did, "name": name, "count": len(outputs)})
        finally:
            _cleanup(outdir)

    def post_wbinspect(self, p):
        """The read-only Inspect tab's lazy half: form fields, signatures, and attachments
        of the ORIGINAL file (metadata/fonts/security ship with the wbadd analysis). Each
        section fails independently so one odd PDF structure doesn't blank the panel."""
        s = _SESSIONS.get(p.get("session"))
        if not s:
            self._json({"ok": False, "code": "no_session", "error": "session expired"}, 404); return
        f = s["files"].get(p.get("file"))
        if not f:
            self._json({"ok": False, "error": "file not found"}, 404); return
        s["ts"] = time.time()
        out = {"ok": True}
        for key, fn in (("forms", form_list), ("signatures", list_signatures)):
            try:
                out[key] = fn(f["path"])
            except Exception as e:  # noqa: BLE001 - per-section, keep the rest of the panel alive
                out[key] = {"ok": False, "error": str(e)}
        try:
            with pikepdf.open(f["path"]) as pdf:
                atts = []
                for name in pdf.attachments:
                    try:
                        size = len(pdf.attachments[name].get_file().read_bytes())
                    except Exception:  # noqa: BLE001
                        size = None
                    atts.append({"name": name, "size": size})
            out["attachments"] = {"ok": True, "attachments": atts}
        except Exception as e:  # noqa: BLE001
            out["attachments"] = {"ok": False, "error": str(e)}
        self._json(out)

    def post_wbextract(self, p):
        """Pull one embedded attachment out of a session file as a download."""
        s = _SESSIONS.get(p.get("session"))
        if not s:
            self._json({"ok": False, "code": "no_session", "error": "session expired"}, 404); return
        f = s["files"].get(p.get("file"))
        if not f:
            self._json({"ok": False, "error": "file not found"}, 404); return
        s["ts"] = time.time()
        name = p.get("name") or ""
        with pikepdf.open(f["path"]) as pdf:
            if name not in pdf.attachments:
                self._json({"ok": False, "error": "no such attachment"}, 404); return
            data = pdf.attachments[name].get_file().read_bytes()
        safe = os.path.basename(name) or "attachment"
        self._json({"ok": True, "fileId": _stash(data, safe, "application/octet-stream"), "name": safe})

    def post_wbrecipes(self, p):
        """Recipes = named stacks. Locally a JSON file under the user config dir; the hosted
        host adapter maps this same call onto /api/recipes (account-bound KV) in the P6
        wiring — the client never knows which. Ops: list / save (upsert by name) / delete."""
        op = p.get("op") or "list"
        recipes = _load_recipes()
        if op == "save":
            name = (p.get("name") or "").strip()
            steps = p.get("steps") or []
            if not name:
                self._json({"ok": False, "error": "give the recipe a name"}); return
            if not isinstance(steps, list) or not steps:
                self._json({"ok": False, "error": "nothing to save — the stack is empty"}); return
            recipes = [r for r in recipes if r.get("name") != name]
            recipes.insert(0, {"name": name[:80], "steps": steps, "updated": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())})
            _save_recipes(recipes)
        elif op == "delete":
            recipes = [r for r in recipes if r.get("name") != p.get("name")]
            _save_recipes(recipes)
        elif op != "list":
            self._json({"ok": False, "error": f"unknown op {op!r}"}); return
        self._json({"ok": True, "recipes": [{"name": r["name"], "steps": r["steps"], "updated": r.get("updated")}
                                            for r in recipes]})

    def post_wbremove(self, p):
        s = _SESSIONS.get(p.get("session"))
        if s:
            f = s["files"].pop(p.get("file"), None)
            if f:
                _cleanup(f["path"], f["path"][:-4] + ".after.pdf")
        self._json({"ok": True})

    def post_wbend(self, p):
        _drop_session(p.get("session"))
        self._json({"ok": True})

    def _write_assets(self, assets):
        out = {}
        for k, b64 in assets.items():
            fd, path = tempfile.mkstemp(suffix="")
            with os.fdopen(fd, "wb") as f:
                f.write(base64.b64decode(b64 or ""))
            out[k] = path
        return out

    # ---- tools ----
    def post_combine(self, p):
        paths = [self._tmp(b) for b in (p.get("pdfs") or [])]
        out = tempfile.mktemp(suffix=".pdf")
        try:
            r = combine(paths, out)
            if not r.get("ok"):
                self._json(r); return
            fid = _stash(_read_bytes(out), "combined.pdf")
        finally:
            for x in paths + [out]:
                if os.path.exists(x):
                    os.remove(x)
        self._json({"ok": True, "pages": r["pages"], "files": r["files"], "fileId": fid})

    def post_split(self, p):
        src = self._tmp(p.get("pdf"))
        outdir = tempfile.mkdtemp()
        try:
            ranges = p.get("ranges")
            r = split(src, outdir, every=int(p.get("every", 1)), ranges=ranges)
            if not r.get("ok"):
                self._json(r); return
            data = _zip([(os.path.basename(x), x) for x in r["outputs"]])
            fid = _stash(data, "split.zip", "application/zip")
        finally:
            _cleanup(src, outdir)
        self._json({"ok": True, "parts": r["parts"], "fileId": fid})

    def post_render(self, p):
        src = self._tmp(p.get("pdf"))
        outdir = tempfile.mkdtemp()
        try:
            with _PDFIUM_LOCK:
                r = render_pages(src, outdir, pages=p.get("pages"), dpi=int(p.get("dpi", 150)),
                                 fmt=p.get("format", "png"))
            if not r.get("ok"):
                self._json(r); return
            imgs = r["outputs"]
            thumbs = ["data:image/{};base64,{}".format(
                "png" if x.endswith("png") else "jpeg",
                base64.b64encode(_read_bytes(x)).decode()) for x in imgs[:8]]
            data = _zip([(os.path.basename(x), x) for x in imgs])
            fid = _stash(data, "images.zip", "application/zip")
        finally:
            _cleanup(src, outdir)
        self._json({"ok": True, "count": r["pages"], "images": thumbs, "fileId": fid})

    def post_extract(self, p):
        src = self._tmp(p.get("pdf"))
        try:
            if p.get("what") == "images":
                outdir = tempfile.mkdtemp()
                try:
                    r = extract_images(src, outdir, pages=p.get("pages"))
                    if not r["outputs"]:
                        self._json({"ok": True, "count": 0, "images": []}); return
                    thumbs = ["data:image/png;base64," + base64.b64encode(_read_bytes(x)).decode()
                              for x in r["outputs"][:8]]
                    fid = _stash(_zip([(os.path.basename(x), x) for x in r["outputs"]]),
                                 "images.zip", "application/zip")
                    self._json({"ok": True, "count": r["images"], "images": thumbs, "fileId": fid})
                finally:
                    _cleanup(outdir)
            else:
                r = extract_text(src, None, pages=p.get("pages"))
                self._json({"ok": True, "text": r["text"], "chars": r["chars"]})
        finally:
            if os.path.exists(src):
                os.remove(src)

    def post_compare(self, p):
        a = self._tmp(p.get("pdfA"))
        b = self._tmp(p.get("pdfB"))
        try:
            with _PDFIUM_LOCK:  # visual compare renders via pdfium
                self._json(compare_pdfs(a, b, visual=bool(p.get("visual"))))
        finally:
            _cleanup(a, b)

    def post_signatures(self, p):
        src = self._tmp(p.get("pdf"))
        try:
            self._json(validate_signatures(src) if p.get("validate") else list_signatures(src))
        finally:
            os.remove(src)

    def post_form(self, p):
        src = self._tmp(p.get("pdf"))
        try:
            if p.get("action") == "fill":
                out = tempfile.mktemp(suffix=".pdf")
                try:
                    r = form_fill(src, out, p.get("data") or {}, flatten=bool(p.get("flatten")))
                    r["fileId"] = _stash(_read_bytes(out), "filled.pdf")
                finally:
                    if os.path.exists(out):
                        os.remove(out)
                self._json(r)
            else:
                self._json(form_list(src))
        finally:
            os.remove(src)

    def post_attachments(self, p):
        src = self._tmp(p.get("pdf"))
        addpaths = []
        try:
            for a in (p.get("add") or []):
                fd, ap = tempfile.mkstemp(suffix="_" + os.path.basename(a.get("name", "file")))
                with os.fdopen(fd, "wb") as f:
                    f.write(base64.b64decode(a.get("data") or ""))
                addpaths.append(ap)
            if addpaths:
                out = tempfile.mktemp(suffix=".pdf")
                r = attachments(src, out, add=addpaths)
                r["fileId"] = _stash(_read_bytes(out), "with-attachments.pdf")
                if os.path.exists(out):
                    os.remove(out)
                self._json(r)
            else:
                self._json(attachments(src))
        finally:
            _cleanup(src, *addpaths)


def _cleanup(*paths):
    import shutil
    for p in paths:
        if not p or not os.path.exists(p):
            continue
        shutil.rmtree(p) if os.path.isdir(p) else os.remove(p)


def start_server():
    """Start the local server on a background thread. Returns (url, httpd)."""
    httpd = ThreadingHTTPServer(("127.0.0.1", 0), _Handler)
    port = httpd.server_address[1]
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    return f"http://127.0.0.1:{port}/", httpd
