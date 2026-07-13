"""Tests for interleave: a page inserted after every N pages."""
import pdfblah as pb
from reportlab.pdfgen import canvas


def _pdf(path, labels):
    c = canvas.Canvas(str(path), pagesize=(400, 300))
    for t in labels:
        c.setFont("Helvetica", 12)
        c.drawString(30, 150, t)
        c.showPage()
    c.save()
    return str(path)


def _labels(path):
    import pdfplumber
    with pdfplumber.open(str(path)) as pdf:
        return [(p.extract_text() or "").strip() for p in pdf.pages]


def test_interleave_every_two(tmp_path):
    src = _pdf(tmp_path / "weeks.pdf", ["w1a", "w1b", "w2a", "w2b"])
    notes = _pdf(tmp_path / "notes.pdf", ["NOTES"])
    out = tmp_path / "planner.pdf"
    r = pb.interleave(src, str(out), notes, every=2)
    assert r["ok"] and r["inserted"] == 2 and r["pages"] == 6
    assert _labels(out) == ["w1a", "w1b", "NOTES", "w2a", "w2b", "NOTES"]


def test_interleave_trailing_partial_group_left_alone(tmp_path):
    src = _pdf(tmp_path / "in.pdf", ["p1", "p2", "p3", "p4", "p5"])
    notes = _pdf(tmp_path / "n.pdf", ["N"])
    r = pb.interleave(src, str(tmp_path / "o.pdf"), notes, every=2)
    assert r["ok"] and r["inserted"] == 2
    assert _labels(tmp_path / "o.pdf") == ["p1", "p2", "N", "p3", "p4", "N", "p5"]


def test_interleave_duplicate_own_page(tmp_path):
    src = _pdf(tmp_path / "in.pdf", ["cover", "form"])
    r = pb.interleave(src, str(tmp_path / "o.pdf"), src, every=1, insert_page=2)
    assert r["ok"]
    assert _labels(tmp_path / "o.pdf") == ["cover", "form", "form", "form"]


def test_interleave_errors(tmp_path):
    src = _pdf(tmp_path / "in.pdf", ["p1"])
    notes = _pdf(tmp_path / "n.pdf", ["N"])
    assert not pb.interleave(src, str(tmp_path / "o.pdf"), notes, every=0)["ok"]
    assert not pb.interleave(src, str(tmp_path / "o.pdf"), notes, insert_page=9)["ok"]
    r = pb.interleave(src, str(tmp_path / "o.pdf"), notes, every=5)
    assert not r["ok"] and "fewer than 5" in r["error"]


def test_interleave_cli(tmp_path, capsys):
    from pdfblah.cli import main
    src = _pdf(tmp_path / "in.pdf", ["a", "b"])
    notes = _pdf(tmp_path / "n.pdf", ["N"])
    assert main(["pages", src, str(tmp_path / "o.pdf"), "--insert", notes, "--every", "1"]) == 0
    assert "inserted 2 page(s), 4 total" in capsys.readouterr().out
