"""Tests for repair: rebuilding structurally damaged PDFs."""
import pikepdf
from reportlab.pdfgen import canvas

import pdfblah as pb


def _pdf(path, pages=3):
    c = canvas.Canvas(str(path), pagesize=(400, 300))
    for i in range(pages):
        c.setFont("Helvetica", 12)
        c.drawString(30, 150, f"important content page {i + 1}")
        c.showPage()
    c.save()
    return str(path)


def test_repair_truncated_xref(tmp_path):
    # chop off the tail: xref table and trailer gone, content intact
    src = _pdf(tmp_path / "in.pdf")
    data = open(src, "rb").read()
    broken = tmp_path / "broken.pdf"
    broken.write_bytes(data[:-250])
    out = tmp_path / "fixed.pdf"
    r = pb.repair(str(broken), str(out))
    assert r["ok"], r
    assert r["pages"] == 3 and r["recovered"] is True
    import pdfplumber
    with pdfplumber.open(str(out)) as pdf:
        assert "important content page 3" in (pdf.pages[2].extract_text() or "")


def test_repair_healthy_file_is_honest_about_it(tmp_path):
    src = _pdf(tmp_path / "in.pdf")
    r = pb.repair(src, str(tmp_path / "out.pdf"))
    assert r["ok"] and r["recovered"] is False and r["pages"] == 3


def test_repair_garbage_is_a_clear_failure(tmp_path):
    junk = tmp_path / "junk.pdf"
    junk.write_bytes(b"this was never a PDF " * 100)
    r = pb.repair(str(junk), str(tmp_path / "out.pdf"))
    assert not r["ok"] and r["code"] == "unrecoverable"


def test_repair_encrypted_points_at_unlock(tmp_path):
    src = _pdf(tmp_path / "in.pdf")
    locked = tmp_path / "locked.pdf"
    with pikepdf.open(src) as pdf:
        pdf.save(str(locked), encryption=pikepdf.Encryption(owner="pw", user="pw"))
    r = pb.repair(str(locked), str(tmp_path / "out.pdf"))
    assert not r["ok"] and r["code"] == "encrypted" and "unlock" in r["error"]


def test_repair_cli(tmp_path, capsys):
    from pdfblah.cli import main
    src = _pdf(tmp_path / "in.pdf")
    data = open(src, "rb").read()
    (tmp_path / "b.pdf").write_bytes(data[:-250])
    assert main(["repair", str(tmp_path / "b.pdf"), "-o", str(tmp_path / "f.pdf")]) == 0
    assert "structure reconstructed" in capsys.readouterr().out
