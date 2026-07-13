"""Tests for batch: one recipe over many files, counters across files."""
import pytest
from reportlab.pdfgen import canvas

import pdfblah as pb


def _pdf(path, pages=2, text="hello world"):
    c = canvas.Canvas(str(path), pagesize=(400, 300))
    for i in range(pages):
        c.setFont("Helvetica", 12)
        c.drawString(30, 150, f"{text} page {i + 1}")
        c.showPage()
    c.save()
    return str(path)


def _text(path):
    import pdfplumber
    with pdfplumber.open(str(path)) as pdf:
        return "\n".join(p.extract_text() or "" for p in pdf.pages)


@pytest.fixture
def folder(tmp_path):
    src = tmp_path / "in"
    src.mkdir()
    _pdf(src / "a.pdf", pages=2)
    _pdf(src / "b.pdf", pages=3)
    return src


def test_batch_two_steps_all_files(folder, tmp_path):
    recipe = tmp_path / "r.txt"
    recipe.write_text("# mark and stamp\n"
                      'replace --find hello --replace goodbye --scope all\n'
                      'watermark --text DRAFT\n')
    out = tmp_path / "out"
    r = pb.run_batch(str(recipe), [str(folder)], str(out))
    assert r["ok"] and r["processed"] == 2 and r["failed"] == 0, r
    assert sorted(p.name for p in out.iterdir()) == ["a.pdf", "b.pdf"]
    assert "goodbye" in _text(out / "a.pdf") and "hello" not in _text(out / "a.pdf")


def test_batch_bates_continues_across_files(folder, tmp_path):
    recipe = ["bates --prefix EXH- --digits 4 --start auto"]
    out = tmp_path / "out"
    r = pb.run_batch(recipe, [str(folder)], str(out))
    assert r["ok"], r
    # a.pdf has 2 pages -> EXH-0001..0002; b.pdf has 3 -> EXH-0003..0005
    assert "EXH-0001" in _text(out / "a.pdf") and "EXH-0002" in _text(out / "a.pdf")
    b = _text(out / "b.pdf")
    assert "EXH-0003" in b and "EXH-0005" in b and "EXH-0001" not in b
    assert r["counter_end"] == 6
    assert [f["counter"] for f in r["files"]] == [1, 3]


def test_batch_dry_run_writes_nothing(folder, tmp_path):
    recipe = ["bates --start auto"]
    out = tmp_path / "out"
    r = pb.run_batch(recipe, [str(folder)], str(out), dry_run=True)
    assert r["ok"] and not out.exists()
    assert [f["counter"] for f in r["files"]] == [1, 3]


def test_batch_failure_is_reported_and_run_continues(folder, tmp_path):
    # --keep 99 fails on 2-3 page files only when out of range; use a step that
    # fails just for a.pdf: drop page 3 (a.pdf has 2 pages, b.pdf has 3)
    recipe = ["pages --keep 3"]
    out = tmp_path / "out"
    r = pb.run_batch(recipe, [str(folder)], str(out))
    assert not r["ok"] and r["failed"] == 1 and r["processed"] == 1
    bad = [f for f in r["files"] if not f["ok"]][0]
    assert bad["input"].endswith("a.pdf") and "step 1" in bad["error"]
    assert (out / "b.pdf").exists() and not (out / "a.pdf").exists()


def test_batch_rejects_unknown_step(folder, tmp_path):
    r = pb.run_batch(["frobnicate --hard"], [str(folder)], str(tmp_path / "o"))
    assert not r["ok"] and "line 1" in r["error"] and "frobnicate" in r["error"]


def test_batch_name_collisions_get_suffixes(tmp_path):
    d1, d2 = tmp_path / "d1", tmp_path / "d2"
    d1.mkdir(); d2.mkdir()
    _pdf(d1 / "same.pdf"); _pdf(d2 / "same.pdf")
    out = tmp_path / "out"
    r = pb.run_batch(["watermark --text X"], [str(d1), str(d2)], str(out))
    assert r["ok"]
    assert sorted(p.name for p in out.iterdir()) == ["same-2.pdf", "same.pdf"]


def test_batch_cli(folder, tmp_path, capsys):
    from pdfblah.cli import main
    recipe = tmp_path / "r.txt"
    recipe.write_text("bates --prefix D- --digits 3 --start auto\n")
    assert main(["batch", str(recipe), str(folder), "-o", str(tmp_path / "out")]) == 0
    out, err = capsys.readouterr()
    assert "a.pdf" in out and "(bates from 1)" in out and "(bates from 3)" in out
    assert "next bates number would be 6" in err
