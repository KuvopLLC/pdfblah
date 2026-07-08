import base64
import json
import urllib.request

import pdfplumber
from reportlab.pdfgen import canvas

from pdfblah.gui.server import start_server
from pdfblah.gui.app import selftest


def make_pdf(path):
    c = canvas.Canvas(str(path), pagesize=(420, 300))
    c.setFont("Helvetica", 14)
    for i, t in enumerate(["Status: DRAFT", "Bill To: Jordan Rivera", "Total: $999.00"]):
        c.drawString(30, 250 - i * 30, t)
    c.save()
    return str(path)


def b64(path):
    with open(path, "rb") as f:
        return base64.b64encode(f.read()).decode()


def text_of_bytes(data, tmp_path):
    p = tmp_path / "dl.pdf"
    p.write_bytes(data)
    with pdfplumber.open(str(p)) as pdf:
        return "\n".join((pg.extract_text() or "") for pg in pdf.pages)


def _post(base, path, obj):
    req = urllib.request.Request(
        base + path, data=json.dumps(obj).encode(),
        headers={"Content-Type": "application/json"},
    )
    return json.loads(urllib.request.urlopen(req, timeout=15).read())


def test_local_flow_analyze_apply_download(tmp_path):
    src = make_pdf(tmp_path / "in.pdf")
    url, httpd = start_server()
    base = url.rstrip("/")
    try:
        a = _post(base, "/analyze", {"pdf": b64(src)})
        assert a["ok"] and a["ready"] and "DRAFT" in a["text"]

        r = _post(base, "/apply", {"pdf": b64(src), "name": "in.pdf", "rules": [
            {"action": "replace", "find": "DRAFT", "replace": "PAID", "scope": "all"},
            {"action": "redact", "find": "Jordan Rivera", "scope": "all"},
        ]})
        assert r["ok"] and r["report"]["applied"] == 2 and r["fileId"]

        data = urllib.request.urlopen(base + "/download/" + r["fileId"], timeout=15).read()
        assert data[:5] == b"%PDF-"
        t = text_of_bytes(data, tmp_path)
        assert "PAID" in t and "DRAFT" not in t and "Jordan" not in t

        # download id is one-shot
        try:
            urllib.request.urlopen(base + "/download/" + r["fileId"], timeout=15)
            assert False, "download id should be single-use"
        except urllib.error.HTTPError as e:
            assert e.code == 404
    finally:
        httpd.shutdown()


def test_local_rejects_bad_rules(tmp_path):
    src = make_pdf(tmp_path / "in.pdf")
    url, httpd = start_server()
    try:
        r = _post(url.rstrip("/"), "/apply",
                  {"pdf": b64(src), "rules": [{"action": "meta", "metaField": "Nope"}]})
        assert r["ok"] is False
    finally:
        httpd.shutdown()


def test_static_server_serves_assets():
    url, httpd = start_server()
    try:
        idx = urllib.request.urlopen(url).read()
        assert b'id="app"' in idx and b"/app.js" in idx
        for name, needle in [("app.js", b"mountApp"), ("gate.mjs", b"countMatches"),
                             ("app.css", b".pb-app"), ("sample-rules.txt", b"pdfblah rules")]:
            assert needle in urllib.request.urlopen(url + name).read(), name
        try:
            urllib.request.urlopen(url + "secret.txt")
            assert False, "should 404"
        except urllib.error.HTTPError as e:
            assert e.code == 404
    finally:
        httpd.shutdown()


def test_selftest_passes():
    selftest()  # raises / exits non-zero on failure


def test_tool_endpoints(tmp_path):
    a = make_pdf(tmp_path / "a.pdf")
    url, httpd = start_server()
    base = url.rstrip("/")
    try:
        # edit pipeline with new actions
        e = _post(base, "/apply", {"pdf": b64(a), "name": "a.pdf", "rules": [
            {"action": "replace", "find": "DRAFT", "replace": "PAID", "scope": "all"},
            {"action": "watermark", "text": "WM", "tile": True},
            {"action": "number"}, {"action": "rotate", "degrees": 90, "pages": "1"},
        ]})
        assert e["ok"] and e["report"]["applied"] == 4
        c = _post(base, "/combine", {"pdfs": [b64(a), b64(a)]})
        assert c["ok"] and c["pages"] == 2 and c["fileId"]
        s = _post(base, "/split", {"pdf": b64(a), "every": 1})
        assert s["ok"] and s["parts"] == 1
        r = _post(base, "/render", {"pdf": b64(a), "dpi": 72})
        assert r["ok"] and r["count"] == 1 and len(r["images"]) == 1
        t = _post(base, "/extract", {"pdf": b64(a), "what": "text"})
        assert t["ok"] and "DRAFT" in t["text"]
        cmp = _post(base, "/compare", {"pdfA": b64(a), "pdfB": b64(a)})
        assert cmp["ok"] and cmp["changed"] == 0
        sig = _post(base, "/signatures", {"pdf": b64(a)})
        assert sig["ok"] and sig["count"] == 0
        # a zip download works
        z = urllib.request.urlopen(base + "/download/" + s["fileId"]).read()
        assert z[:2] == b"PK"
    finally:
        httpd.shutdown()
