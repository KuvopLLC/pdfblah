"""Tests for extract_tables: PDF tables out as CSV."""
import csv

from reportlab.lib import colors
from reportlab.lib.pagesizes import letter
from reportlab.platypus import SimpleDocTemplate, Spacer, Table, TableStyle

import pdfblah as pb

GRID = TableStyle([("GRID", (0, 0), (-1, -1), 0.8, colors.black),
                   ("FONTSIZE", (0, 0), (-1, -1), 9)])


def _invoice_pdf(path, two_tables=False):
    rows1 = [["Item", "Qty", "Price"],
             ["Widget, blue", "4", "12.50"],
             ["Gadget", "1", "99.00"]]
    rows2 = [["Vendor", "Invoice"],
             ["ACME", "INV-100"]]
    parts = [Table(rows1, style=GRID)]
    if two_tables:
        parts += [Spacer(0, 24), Table(rows2, style=GRID)]
    SimpleDocTemplate(str(path), pagesize=letter).build(parts)
    return str(path)


def test_tables_to_single_csv(tmp_path):
    src = _invoice_pdf(tmp_path / "in.pdf")
    out = tmp_path / "out.csv"
    r = pb.extract_tables(src, str(out))
    assert r["ok"] and r["tables"] == 1 and r["found"][0]["page"] == 1
    rows = list(csv.reader(open(out)))
    assert rows[0] == ["Item", "Qty", "Price"]
    assert rows[1][0] == "Widget, blue"  # commas survive CSV quoting
    assert rows[2] == ["Gadget", "1", "99.00"]


def test_two_tables_one_file_blank_row_separator(tmp_path):
    src = _invoice_pdf(tmp_path / "in.pdf", two_tables=True)
    out = tmp_path / "out.csv"
    r = pb.extract_tables(src, str(out))
    assert r["ok"] and r["tables"] == 2
    rows = list(csv.reader(open(out)))
    assert [] in rows  # the separator
    assert ["Vendor", "Invoice"] in rows


def test_two_tables_into_directory(tmp_path):
    src = _invoice_pdf(tmp_path / "in.pdf", two_tables=True)
    outdir = tmp_path / "csvs"
    r = pb.extract_tables(src, str(outdir))
    assert r["ok"] and len(r["outputs"]) == 2
    names = sorted(p.name for p in outdir.iterdir())
    assert names == ["table-p1-1.csv", "table-p1-2.csv"]


def test_no_output_returns_data(tmp_path):
    src = _invoice_pdf(tmp_path / "in.pdf")
    r = pb.extract_tables(src)
    assert r["ok"] and "Gadget" in r["data"] and r["outputs"] == []


def test_no_tables_is_a_clear_no(tmp_path):
    from reportlab.pdfgen import canvas
    c = canvas.Canvas(str(tmp_path / "plain.pdf"), pagesize=letter)
    c.drawString(50, 700, "just a paragraph, no table anywhere")
    c.showPage()
    c.save()
    r = pb.extract_tables(str(tmp_path / "plain.pdf"), str(tmp_path / "out.csv"))
    assert not r["ok"] and r["code"] == "no_tables" and "ocr" in r["error"]


def test_tables_cli(tmp_path, capsys):
    from pdfblah.cli import main
    src = _invoice_pdf(tmp_path / "in.pdf")
    assert main(["extract", src, "--tables"]) == 0
    out, err = capsys.readouterr()
    assert "Widget" in out and "1 table(s)" in err
