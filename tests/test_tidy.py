"""Tests for tidy: dropping blank pages and exact duplicate pages.

Rendering-based, so they need the app extra (pypdfium2 + numpy), which the test
extra installs; they skip cleanly without it.
"""
import importlib.util

import pytest
from reportlab.pdfgen import canvas

import pdfblah as pb

needs_render = pytest.mark.skipif(
    not (importlib.util.find_spec("pypdfium2") and importlib.util.find_spec("numpy")),
    reason="needs the app extra (pypdfium2 + numpy)")


def _messy_pdf(path):
    """Page 1: content. Page 2: blank. Page 3: exact repeat of page 1.
    Page 4: only a tiny footer (almost no ink, but real text)."""
    c = canvas.Canvas(str(path), pagesize=(400, 300))
    for _ in range(2):
        c.setFont("Helvetica", 16)
        c.drawString(40, 220, "Quarterly Report heading")
        c.drawString(40, 180, "some body text here")
        c.showPage()
        if _ == 0:
            c.showPage()  # the blank page between the original and its repeat
    c.setFont("Helvetica", 8)
    c.drawString(180, 20, "Page 4")
    c.showPage()
    c.save()
    return str(path)


def _pages(path):
    import pikepdf
    with pikepdf.open(str(path)) as pdf:
        return len(pdf.pages)


@needs_render
def test_tidy_drops_blank_and_duplicate(tmp_path):
    src = _messy_pdf(tmp_path / "in.pdf")
    out = tmp_path / "out.pdf"
    r = pb.tidy(src, str(out))
    assert r["ok"], r
    assert r["pages"] == 4 and r["kept"] == 2
    reasons = {d["page"]: d for d in r["dropped"]}
    assert reasons[2]["reason"] == "blank"
    assert reasons[3]["reason"] == "duplicate" and reasons[3]["of"] == 1
    assert _pages(out) == 2


@needs_render
def test_tidy_footer_only_page_is_not_blank(tmp_path):
    # "Page 4" carries almost no ink but it is real text: never dropped as blank
    src = _messy_pdf(tmp_path / "in.pdf")
    r = pb.tidy(src, dry_run=True)
    assert r["ok"] and all(d["page"] != 4 for d in r["dropped"])


@needs_render
def test_tidy_dry_run_writes_nothing(tmp_path):
    src = _messy_pdf(tmp_path / "in.pdf")
    r = pb.tidy(src, dry_run=True)
    assert r["ok"] and r["output"] is None and len(r["dropped"]) == 2
    assert list(tmp_path.iterdir()) == [tmp_path / "in.pdf"]


@needs_render
def test_tidy_keep_flags(tmp_path):
    src = _messy_pdf(tmp_path / "in.pdf")
    r = pb.tidy(src, str(tmp_path / "o1.pdf"), drop_blank=False)
    assert [d["reason"] for d in r["dropped"]] == ["duplicate"]
    r = pb.tidy(src, str(tmp_path / "o2.pdf"), drop_duplicates=False)
    assert [d["reason"] for d in r["dropped"]] == ["blank"]


@needs_render
def test_tidy_refuses_to_write_an_empty_pdf(tmp_path):
    c = canvas.Canvas(str(tmp_path / "blank.pdf"), pagesize=(400, 300))
    c.showPage()
    c.save()
    r = pb.tidy(str(tmp_path / "blank.pdf"), str(tmp_path / "out.pdf"))
    assert not r["ok"] and "every page" in r["error"]
    assert not (tmp_path / "out.pdf").exists()


@needs_render
def test_tidy_clean_file_passes_through(tmp_path):
    c = canvas.Canvas(str(tmp_path / "in.pdf"), pagesize=(400, 300))
    for i in range(3):
        c.setFont("Helvetica", 16)
        c.drawString(40, 150, f"unique page {i}")
        c.showPage()
    c.save()
    out = tmp_path / "out.pdf"
    r = pb.tidy(str(tmp_path / "in.pdf"), str(out))
    assert r["ok"] and r["dropped"] == [] and r["kept"] == 3
    assert _pages(out) == 3


@needs_render
def test_tidy_output_required_without_dry_run(tmp_path):
    src = _messy_pdf(tmp_path / "in.pdf")
    r = pb.tidy(src)
    assert not r["ok"] and "output_path" in r["error"]


def test_tidy_cli_dry_run(tmp_path, capsys):
    if not (importlib.util.find_spec("pypdfium2") and importlib.util.find_spec("numpy")):
        pytest.skip("needs the app extra")
    from pdfblah.cli import main
    src = _messy_pdf(tmp_path / "in.pdf")
    assert main(["tidy", src, "--dry-run"]) == 0
    out = capsys.readouterr().out
    assert "would drop 2" in out and "page 2: blank" in out and "duplicate of page 1" in out
