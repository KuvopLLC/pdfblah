"""Open the pdfblah app locally: start a small web server on your machine and open it in
your browser. Same tool as the website, running fully on your computer, free, with no
upload, no watermark, no account, and nothing to install but Python. `pdfblah gui` calls
launch(); run it with --selftest (or call selftest()) for a headless check."""
import time
import webbrowser

from .server import start_server


def launch():
    url, httpd = start_server()
    print(f"pdfblah is running at {url}")
    print("Your browser should open it. If it doesn't, open that address yourself.")
    print("Everything stays on your machine. Press Ctrl+C here to stop.")
    try:
        webbrowser.open(url)
    except Exception:
        pass
    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        print("\nStopping pdfblah.")
    finally:
        httpd.shutdown()


def selftest():
    """Headless check that the local app is complete and works end to end, with no browser:
    the shared tool UI is served, and a PDF round-trips through analyze -> apply -> download.
    Exits non-zero on failure. Uses only base dependencies (no preview rendering)."""
    import base64
    import io
    import json
    import urllib.request

    import pikepdf

    url, httpd = start_server()
    base = url.rstrip("/")

    def post(path, obj):
        req = urllib.request.Request(
            base + path, data=json.dumps(obj).encode(),
            headers={"Content-Type": "application/json"},
        )
        return json.loads(urllib.request.urlopen(req, timeout=15).read())

    try:
        idx = urllib.request.urlopen(url, timeout=15).read()
        assert b"workbench" in idx, "local index is not the workbench"
        single = urllib.request.urlopen(base + "/single", timeout=15).read()
        assert b'id="app"' in single, "single-file page is missing its mount point"
        for name in ("app.js", "gate.mjs", "app.css", "workbench.js", "workbench.css"):
            assert urllib.request.urlopen(base + "/" + name, timeout=15).read(), name

        pdf = pikepdf.new()
        pdf.add_blank_page(page_size=(200, 200))
        buf = io.BytesIO()
        pdf.save(buf)
        pdf.close()
        b64 = base64.b64encode(buf.getvalue()).decode()

        assert "ok" in post("/analyze", {"pdf": b64}), "analyze returned no result"
        r = post("/apply", {"pdf": b64, "name": "t.pdf",
                            "rules": [{"action": "meta", "metaField": "Title", "metaValue": "x"}]})
        assert r.get("ok") and r["report"]["applied"] == 1, "apply did not land the edit"
        out = urllib.request.urlopen(base + "/download/" + r["fileId"], timeout=15).read()
        assert out[:5] == b"%PDF-", "download did not return a PDF"
    finally:
        httpd.shutdown()

    print("pdfblah selftest OK")
