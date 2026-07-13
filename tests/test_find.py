"""Tests for find: text search across many PDFs."""
import pytest
from reportlab.pdfgen import canvas

import pdfblah as pb


def _pdf(path, *page_texts):
    c = canvas.Canvas(str(path), pagesize=(400, 300))
    for t in page_texts:
        if t:
            c.setFont("Helvetica", 12)
            c.drawString(30, 150, t)
        c.showPage()
    c.save()
    return str(path)


@pytest.fixture
def tree(tmp_path):
    _pdf(tmp_path / "a.pdf", "the quarterly invoice INV-100", "nothing here")
    sub = tmp_path / "sub"
    sub.mkdir()
    _pdf(sub / "b.pdf", "final invoice INV-200 attached")
    _pdf(sub / "c.pdf", "no matches on this one")
    _pdf(tmp_path / "scan.pdf", None)  # a page with no text at all
    return tmp_path


def test_find_recursive_with_pages(tree):
    r = pb.find("invoice", [str(tree)], recursive=True)
    assert r["ok"] and r["files_searched"] == 4
    got = {(m["file"].split("/")[-1], m["page"]) for m in r["matches"]}
    assert got == {("a.pdf", 1), ("b.pdf", 1)}
    assert "invoice" in r["matches"][0]["snippet"]


def test_find_non_recursive_skips_subdirs(tree):
    r = pb.find("invoice", [str(tree)])
    assert {m["file"].split("/")[-1] for m in r["matches"]} == {"a.pdf"}


def test_find_flags_text_free_files(tree):
    r = pb.find("anything", [str(tree)], recursive=True)
    assert [f.split("/")[-1] for f in r["no_text"]] == ["scan.pdf"]


def test_find_regex_and_ci(tree):
    r = pb.find(r"INV-\d+", [str(tree)], recursive=True, regex=True)
    assert len(r["matches"]) == 2
    r = pb.find("INVOICE", [str(tree)], recursive=True)
    assert not r["matches"]
    r = pb.find("INVOICE", [str(tree)], recursive=True, ci=True)
    assert len(r["matches"]) == 2


def test_find_word_boundary(tmp_path):
    _pdf(tmp_path / "x.pdf", "reinvoiced versus invoice proper")
    assert len(pb.find("invoice", [str(tmp_path)], word=True)["matches"]) == 1


def test_find_max_matches_truncates(tmp_path):
    _pdf(tmp_path / "many.pdf", *["invoice " * 3 for _ in range(4)])
    r = pb.find("invoice", [str(tmp_path)], max_matches=5)
    assert r["truncated"] and len(r["matches"]) == 5


def test_find_bad_regex_and_empty_dir(tmp_path):
    assert not pb.find("(", [str(tmp_path)], regex=True)["ok"]
    empty = tmp_path / "void"
    empty.mkdir()
    r = pb.find("x", [str(empty)])
    assert not r["ok"] and "no PDFs" in r["error"]


def test_find_cli_output(tree, capsys):
    from pdfblah.cli import main
    assert main(["find", "invoice", str(tree), "-r"]) == 0
    out, err = capsys.readouterr()
    assert "a.pdf p.1" in out and "b.pdf p.1" in out
    assert "scan.pdf has no text layer" in err
    assert main(["find", "zzznope", str(tree), "-r"]) == 1
