"""The workbench session endpoints of the local gui server, end to end over real HTTP
with real PDFs: upload-once sessions, live preview with the stack applied (and its
cache), outputs in every format, inspect/extract, and file-backed recipes.

The shared workbench UI (pdfblah/web/workbench.js) is exercised by the browser E2E in
tests/e2e/; this file pins the server contract it runs against."""
import base64
import io
import json
import os
import urllib.error
import urllib.request
import zipfile

import pikepdf
import pytest
from PIL import Image
from reportlab.pdfgen import canvas

from pdfblah.gui import server as gui_server
from pdfblah.gui.server import start_server


def make_pdf(path, pages=3, needle="Acme Corp"):
    c = canvas.Canvas(str(path), pagesize=(420, 300))
    for p in range(1, pages + 1):
        c.setFont("Helvetica", 14)
        c.drawString(30, 250, f"Quarterly Report page {p}")
        c.drawString(30, 210, f"Client: {needle}")
        c.showPage()
    c.save()
    return str(path)


def make_scan_pdf(path):
    """Image-only pages: no selectable text, so it must be flagged as a scan."""
    img = Image.new("RGB", (420, 300), "white")
    img.save(str(path), "PDF")
    return str(path)


def make_inspect_pdf(path, tmp_path, base_pdf):
    att = tmp_path / "terms.csv"
    att.write_bytes(b"a,b\n1,2\n" * 30)
    with pikepdf.open(base_pdf) as pdf:
        pdf.docinfo["/Title"] = "Master Services Agreement"
        pdf.docinfo["/Author"] = "K. Osei"
        pdf.attachments["terms.csv"] = pikepdf.AttachedFileSpec.from_filepath(pdf, str(att))
        pdf.save(str(path))
    return str(path), att.read_bytes()


def b64(path):
    with open(path, "rb") as f:
        return base64.b64encode(f.read()).decode()


def _post(base, path, obj):
    req = urllib.request.Request(
        base + "/" + path, data=json.dumps(obj).encode(),
        headers={"Content-Type": "application/json"},
    )
    try:
        return json.loads(urllib.request.urlopen(req, timeout=30).read())
    except urllib.error.HTTPError as e:  # the API 404s carry a JSON body
        return json.loads(e.read())


def _download(base, fid):
    return urllib.request.urlopen(base + "/download/" + fid, timeout=30).read()


REPLACE = [{"action": "replace", "find": "Acme Corp", "replace": "Blahco Ltd", "scope": "all"}]


@pytest.fixture()
def wb(tmp_path, monkeypatch):
    """A running server + a session with report.pdf (3 pages of text) added."""
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "xdg"))  # hermetic recipes store
    url, httpd = start_server()
    base = url.rstrip("/")
    sid = _post(base, "wbsession", {})["session"]
    add = _post(base, "wbadd", {"session": sid, "pdf": b64(make_pdf(tmp_path / "report.pdf")),
                                "name": "report.pdf"})
    assert add["ok"] and add["pages"] == 3 and add["isScan"] is False
    yield base, sid, add["fileId"], tmp_path
    httpd.shutdown()


def test_session_formats_and_scan_badge(wb, tmp_path):
    base, sid, _fid, _ = wb
    assert "word" in _post(base, "wbsession", {})["formats"]
    scan = _post(base, "wbadd", {"session": sid, "pdf": b64(make_scan_pdf(tmp_path / "scan.pdf")),
                                 "name": "scan.pdf"})
    assert scan["ok"] and scan["isScan"] is True


def test_render_and_preview_lifecycle(wb):
    base, sid, fid, _ = wb
    r = _post(base, "wbrender", {"session": sid, "file": fid, "page": 2, "dpi": 40})
    assert r["ok"] and r["image"].startswith("data:image/png") and r["pages"] == 3

    before = _post(base, "wbpreview", {"session": sid, "file": fid, "page": 1, "dpi": 60, "rules": []})
    assert before["ok"] and before["report"] is None

    after = _post(base, "wbpreview", {"session": sid, "file": fid, "page": 1, "dpi": 60, "rules": REPLACE})
    assert after["ok"] and after["report"]["applied"] == 1
    assert after["report"]["rules"][0]["count"] == 3          # one hit per page
    assert after["report"]["boxes"], "highlight boxes for the preview"
    assert after["image"] != before["image"]

    # same stack again: served from the stack-hash cache (same after-PDF on disk)
    sdir = gui_server._SESSIONS[sid]["dir"]
    afters = [f for f in os.listdir(sdir) if f.endswith(".after.pdf")]
    assert len(afters) == 1
    again = _post(base, "wbpreview", {"session": sid, "file": fid, "page": 2, "dpi": 60, "rules": REPLACE})
    assert again["ok"] and [f for f in os.listdir(sdir) if f.endswith(".after.pdf")] == afters

    # a `pages` step shrinks the doc and out-of-range pages clamp
    shrunk = _post(base, "wbpreview", {"session": sid, "file": fid, "page": 3, "dpi": 60,
                                       "rules": REPLACE + [{"action": "pages", "keep": "1-2"}]})
    assert shrunk["ok"] and shrunk["pages"] == 2 and shrunk["page"] == 2

    # a broken rule surfaces as a clean error, never a 500
    bad = _post(base, "wbpreview", {"session": sid, "file": fid, "page": 1,
                                    "rules": [{"action": "replace", "find": "(", "regex": True, "scope": "all"}]})
    assert bad["ok"] is False and bad["error"]

    # validation errors pass through with their code
    blank = _post(base, "wbpreview", {"session": sid, "file": fid, "page": 1,
                                      "rules": [{"action": "replace", "find": ""}]})
    assert blank["ok"] is False and blank.get("code") == "blank_find"


def test_output_formats(wb, tmp_path):
    base, sid, fid, _ = wb
    import pdfplumber

    single = _post(base, "wboutput", {"session": sid, "files": [fid], "rules": REPLACE, "format": "pdf"})
    assert single["ok"] and single["name"] == "report-edited.pdf" and single["count"] == 1
    data = _download(base, single["fileId"])
    assert data.startswith(b"%PDF")
    p = tmp_path / "out.pdf"; p.write_bytes(data)
    with pdfplumber.open(str(p)) as pdf:
        text = "\n".join((pg.extract_text() or "") for pg in pdf.pages)
    assert "Blahco Ltd" in text and "Acme Corp" not in text

    fid2 = _post(base, "wbadd", {"session": sid, "pdf": b64(make_pdf(tmp_path / "b.pdf", pages=1)),
                                 "name": "b.pdf"})["fileId"]
    merged = _post(base, "wboutput", {"session": sid, "files": [fid, fid2], "rules": [],
                                      "format": "pdf", "merge": True})
    assert merged["ok"] and merged["name"] == "pdfblah-merged.pdf"
    mp = tmp_path / "m.pdf"; mp.write_bytes(_download(base, merged["fileId"]))
    with pikepdf.open(str(mp)) as pdf:
        assert len(pdf.pages) == 4

    # merge as a BINDER: a Contents page with clickable rows, bookmarks, edge tabs,
    # and section titles from the files' display names (never the temp paths)
    binder = _post(base, "wboutput", {"session": sid, "files": [fid, fid2], "rules": [],
                                      "format": "pdf", "merge": True,
                                      "options": {"toc": True, "tabs": True}})
    assert binder["ok"] and binder["name"] == "pdfblah-binder.pdf"
    bp = tmp_path / "binder.pdf"; bp.write_bytes(_download(base, binder["fileId"]))
    with pikepdf.open(str(bp)) as pdf:
        assert len(pdf.pages) == 5  # 1 ToC + 3 + 1
        assert len(pdf.pages[0].get("/Annots", [])) == 2
        with pdf.open_outline() as ol:
            titles = [it.title for it in ol.root]
        assert titles == ["Contents", "1. report", "2. b"]

    both = _post(base, "wboutput", {"session": sid, "files": [fid, fid2], "rules": REPLACE, "format": "pdf"})
    assert both["ok"] and both["name"] == "pdfblah-output.zip" and both["count"] == 2
    z = zipfile.ZipFile(io.BytesIO(_download(base, both["fileId"])))
    assert sorted(z.namelist()) == ["b-edited.pdf", "report-edited.pdf"]

    txt = _post(base, "wboutput", {"session": sid, "files": [fid], "rules": REPLACE, "format": "text"})
    assert "Blahco Ltd" in _download(base, txt["fileId"]).decode()

    png = _post(base, "wboutput", {"session": sid, "files": [fid], "rules": [], "format": "png"})
    assert png["ok"] and png["count"] == 3
    z = zipfile.ZipFile(io.BytesIO(_download(base, png["fileId"])))
    assert len(z.namelist()) == 3
    assert z.read(z.namelist()[0]).startswith(b"\x89PNG")

    prot = _post(base, "wboutput", {"session": sid, "files": [fid], "rules": [],
                                    "format": "pdf", "options": {"password": "s3cret"}})
    ep = tmp_path / "enc.pdf"; ep.write_bytes(_download(base, prot["fileId"]))
    with pytest.raises(pikepdf.PasswordError):
        pikepdf.open(str(ep))
    with pikepdf.open(str(ep), password="s3cret") as pdf:
        assert len(pdf.pages) == 3


def test_asset_watermark_by_reference(wb, tmp_path):
    base, sid, fid, _ = wb
    img = tmp_path / "logo.png"
    Image.new("RGBA", (40, 40), (255, 0, 0, 200)).save(str(img), "PNG")
    a = _post(base, "wbasset", {"session": sid, "data": b64(img), "name": "logo.png"})
    assert a["ok"]
    r = _post(base, "wbpreview", {"session": sid, "file": fid, "page": 1, "dpi": 60,
                                  "rules": [{"action": "watermark", "assetImage": a["assetId"], "opacity": 0.3}]})
    assert r["ok"] and r["report"]["applied"] == 1


def test_inspect_and_extract(wb, tmp_path):
    base, sid, _fid, _ = wb
    path, att_bytes = make_inspect_pdf(tmp_path / "insp.pdf", tmp_path, make_pdf(tmp_path / "base.pdf", pages=1))
    fid = _post(base, "wbadd", {"session": sid, "pdf": b64(path), "name": "insp.pdf"})["fileId"]
    d = _post(base, "wbinspect", {"session": sid, "file": fid})
    assert d["ok"] and d["forms"]["count"] == 0 and d["signatures"]["count"] == 0
    assert d["attachments"]["attachments"] == [{"name": "terms.csv", "size": len(att_bytes)}]
    x = _post(base, "wbextract", {"session": sid, "file": fid, "name": "terms.csv"})
    assert _download(base, x["fileId"]) == att_bytes
    assert _post(base, "wbextract", {"session": sid, "file": fid, "name": "nope"})["ok"] is False


def test_clean_scan_step(wb, tmp_path):
    """A {"action": "clean"} rule previews and downloads like any other edit."""
    base, sid, _fid, _ = wb
    dirty = tmp_path / "dirty.pdf"
    Image.new("RGB", (300, 400), (205, 200, 190)).save(str(dirty), "PDF")  # gray "paper"
    add = _post(base, "wbadd", {"session": sid, "pdf": b64(str(dirty)), "name": "dirty.pdf"})
    assert add["ok"] and add["isScan"] is True
    rules = [{"action": "clean", "strength": "strong"}]
    before = _post(base, "wbpreview", {"session": sid, "file": add["fileId"], "page": 1, "dpi": 60, "rules": []})
    after = _post(base, "wbpreview", {"session": sid, "file": add["fileId"], "page": 1, "dpi": 60, "rules": rules})
    assert after["ok"] and after["report"]["applied"] == 1
    assert after["image"] != before["image"]
    out = _post(base, "wboutput", {"session": sid, "files": [add["fileId"]], "rules": rules, "format": "pdf"})
    assert out["ok"] and _download(base, out["fileId"])[:5] == b"%PDF-"


def test_remove_cleans_after_file_and_end_drops_session(wb):
    base, sid, fid, _ = wb
    _post(base, "wbpreview", {"session": sid, "file": fid, "page": 1, "dpi": 60, "rules": REPLACE})
    sdir = gui_server._SESSIONS[sid]["dir"]
    assert any(f.endswith(".after.pdf") for f in os.listdir(sdir))
    _post(base, "wbremove", {"session": sid, "file": fid})
    assert not any(f.endswith(".after.pdf") for f in os.listdir(sdir))
    assert _post(base, "wbrender", {"session": sid, "file": fid, "page": 1})["ok"] is False
    _post(base, "wbend", {"session": sid})
    assert sid not in gui_server._SESSIONS
    assert _post(base, "wbadd", {"session": sid, "pdf": "", "name": "x.pdf"}).get("code") == "no_session"


def test_recipes_file_store(wb):
    base, _sid, _fid, tmp_path = wb
    steps = [{"type": "replace", "on": True, "cfg": {"find": "Acme", "replace": "Blahco", "scope": "all"}}]
    assert _post(base, "wbrecipes", {"op": "list"})["recipes"] == []
    r = _post(base, "wbrecipes", {"op": "save", "name": "client handoff", "steps": steps})
    assert [x["name"] for x in r["recipes"]] == ["client handoff"]
    r = _post(base, "wbrecipes", {"op": "save", "name": "redact pass", "steps": steps})
    assert [x["name"] for x in r["recipes"]] == ["redact pass", "client handoff"]
    r = _post(base, "wbrecipes", {"op": "save", "name": "client handoff", "steps": steps})
    assert [x["name"] for x in r["recipes"]][0] == "client handoff"  # upsert bumps to top
    store = tmp_path / "xdg" / "pdfblah" / "recipes.json"
    assert store.exists() and not store.with_suffix(".json.tmp").exists()
    r = _post(base, "wbrecipes", {"op": "delete", "name": "redact pass"})
    assert [x["name"] for x in r["recipes"]] == ["client handoff"]
    assert _post(base, "wbrecipes", {"op": "save", "name": "", "steps": steps})["ok"] is False
    assert _post(base, "wbrecipes", {"op": "save", "name": "x", "steps": []})["ok"] is False
