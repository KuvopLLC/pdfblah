import os

import pdfplumber
from reportlab.pdfgen import canvas

from pdfblah import read_metadata
from pdfblah.app import (
    analyze_pdf, apply_actions, render_preview, validate_upload, LOCAL, HOSTED,
)


def make_invoice(path):
    c = canvas.Canvas(str(path), pagesize=(420, 300))
    c.setFont("Helvetica", 14)
    for i, t in enumerate(["Status: DRAFT", "Bill To: Jordan Rivera", "Total: $999.00"]):
        c.drawString(30, 250 - i * 30, t)
    c.save()
    return str(path)


def text_of(path):
    with pdfplumber.open(str(path)) as pdf:
        return "\n".join((p.extract_text() or "") for p in pdf.pages)


def test_analyze_ready(tmp_path):
    a = analyze_pdf(make_invoice(tmp_path / "in.pdf"))
    assert a["ok"] and a["ready"] and a["hasText"] and len(a["checks"]) == 4
    assert "DRAFT" in a["text"]
    assert a["fonts"]["problems"] == []


def test_analyze_not_pdf(tmp_path):
    p = tmp_path / "x.txt"; p.write_text("hello")
    a = analyze_pdf(str(p))
    assert a["ok"] is False and a["code"] == "not_pdf"


def test_apply_actions(tmp_path):
    src = make_invoice(tmp_path / "in.pdf"); o = str(tmp_path / "o.pdf")
    rep = apply_actions(src, o, [
        {"action": "replace", "find": "DRAFT", "replace": "PAID", "scope": "all"},
        {"action": "redact", "find": "Jordan Rivera", "scope": "all"},
        {"action": "stripmeta"},
    ])
    assert rep["applied"] == 3 and rep["total"] == 3
    t = text_of(o)
    assert "PAID" in t and "DRAFT" not in t and "Jordan" not in t
    assert read_metadata(o)["docinfo"] == {}
    assert len(rep["boxes"]) >= 1


def test_render_clean_and_watermarked(tmp_path):
    from PIL import Image
    src = make_invoice(tmp_path / "in.pdf"); o = str(tmp_path / "o.pdf")
    rep = apply_actions(src, o, [{"action": "replace", "find": "DRAFT", "replace": "PAID", "scope": "all"}])
    c = str(tmp_path / "c.png"); w = str(tmp_path / "w.png")
    render_preview(o, c, pages=(1,), highlights=rep["boxes"], watermark=False)
    render_preview(o, w, pages=(1,), highlights=rep["boxes"], watermark=True)
    assert os.path.getsize(c) > 1000 and os.path.getsize(w) > 1000
    assert Image.open(c).size == Image.open(w).size
    # the watermark makes the two images differ
    assert Image.open(c).tobytes() != Image.open(w).tobytes()


def test_limits_local_vs_hosted(tmp_path):
    src = make_invoice(tmp_path / "in.pdf")
    # 60 rules is over the hosted cap of 50 but fine on the local cap of 1000
    assert validate_upload(src, 60, HOSTED)["code"] == "too_many_rules"
    assert validate_upload(src, 60, LOCAL)["ok"]
    assert LOCAL.max_bytes > HOSTED.max_bytes and LOCAL.max_pages > HOSTED.max_pages


def test_web_assets_present():
    from pdfblah.app import web_dir, read_asset, WEB_FILES
    d = web_dir()
    for name in WEB_FILES:
        assert (d / name).exists(), f"missing shared web asset {name}"
    # the tool controller and pure logic are non-trivial and export their entry points
    assert b"mountTool" in read_asset("tool.js")
    assert b"export function countMatches" in read_asset("gate.mjs")
    assert b".pb-tool" in read_asset("tool.css")


def test_apply_actions_full_pipeline(tmp_path):
    import pikepdf
    from reportlab.pdfgen import canvas
    src = str(tmp_path / "in.pdf"); out = str(tmp_path / "out.pdf")
    c = canvas.Canvas(src, pagesize=(400, 300))
    for i in range(3):
        c.drawString(40, 250, f"DRAFT page {i+1}"); c.showPage()
    c.save()
    rep = apply_actions(src, out, [
        {"action": "replace", "find": "DRAFT", "replace": "PAID", "scope": "all"},
        {"action": "watermark", "text": "SECRET", "tile": True, "opacity": 0.15},
        {"action": "number", "format": "Page {n} of {total}"},
        {"action": "rotate", "degrees": 90, "pages": "1"},
        {"action": "protect", "password": "pw", "allowCopy": False},
        {"action": "optimize"},
    ])
    assert rep["applied"] == 6 and rep["all_applied"]
    # protect ran last -> encrypted; optimize before it
    import pytest
    with pytest.raises(pikepdf.PasswordError):
        pikepdf.open(out)
    with pikepdf.open(out, password="pw") as p:
        assert len(p.pages) == 3 and int(p.pages[0].get("/Rotate", 0)) == 90
