"""Tests for form --fill-from: one filled PDF per data row."""
from reportlab.pdfgen import canvas

import pdfblah as pb


def _form_pdf(path):
    c = canvas.Canvas(str(path), pagesize=(400, 300))
    c.setFont("Helvetica", 12)
    c.drawString(30, 250, "Expense form")
    c.acroForm.textfield(name="Employee", x=30, y=180, width=200, height=20)
    c.acroForm.textfield(name="Amount", x=30, y=140, width=200, height=20)
    c.showPage()
    c.save()
    return str(path)


def _values(path):
    return {f["name"]: f["value"] for f in pb.form_list(str(path))["fields"]}


def test_fill_from_csv_named_by_column(tmp_path):
    src = _form_pdf(tmp_path / "form.pdf")
    data = tmp_path / "rows.csv"
    data.write_text("Employee,Amount,Department\n"
                    "Ada Lovelace,120.50,Engineering\n"
                    "Grace Hopper,99.00,Navy\n")
    out = tmp_path / "filled"
    r = pb.form_fill_from(src, str(data), str(out), name="{Employee}.pdf")
    assert r["ok"] and r["rows"] == 2, r
    assert sorted(p.name for p in out.iterdir()) == ["Ada Lovelace.pdf", "Grace Hopper.pdf"]
    assert r["unmatched_columns"] == ["Department"]
    vals = _values(out / "Ada Lovelace.pdf")
    assert vals["Employee"] == "Ada Lovelace" and vals["Amount"] == "120.50"


def test_fill_from_json_and_row_template(tmp_path):
    src = _form_pdf(tmp_path / "form.pdf")
    data = tmp_path / "rows.json"
    data.write_text('[{"Employee": "A"}, {"Employee": "B"}]')
    out = tmp_path / "filled"
    r = pb.form_fill_from(src, str(data), str(out))
    assert r["ok"]
    assert sorted(p.name for p in out.iterdir()) == ["1.pdf", "2.pdf"]
    assert _values(out / "2.pdf")["Employee"] == "B"


def test_fill_from_duplicate_names_get_suffixes(tmp_path):
    src = _form_pdf(tmp_path / "form.pdf")
    data = tmp_path / "rows.csv"
    data.write_text("Employee\nSam\nSam\n")
    out = tmp_path / "filled"
    r = pb.form_fill_from(src, str(data), str(out), name="{Employee}.pdf")
    assert r["ok"]
    assert sorted(p.name for p in out.iterdir()) == ["Sam-2.pdf", "Sam.pdf"]


def test_fill_from_errors(tmp_path):
    src = _form_pdf(tmp_path / "form.pdf")
    empty = tmp_path / "e.csv"
    empty.write_text("Employee\n")
    assert "no rows" in pb.form_fill_from(src, str(empty), str(tmp_path / "o"))["error"]
    # a formless PDF is a clear no
    c = canvas.Canvas(str(tmp_path / "plain.pdf"), pagesize=(400, 300))
    c.drawString(30, 150, "not a form")
    c.showPage()
    c.save()
    data = tmp_path / "d.csv"
    data.write_text("Employee\nA\n")
    r = pb.form_fill_from(str(tmp_path / "plain.pdf"), str(data), str(tmp_path / "o"))
    assert not r["ok"] and r["code"] == "no_fields"


def test_fill_from_cli(tmp_path, capsys):
    from pdfblah.cli import main
    src = _form_pdf(tmp_path / "form.pdf")
    data = tmp_path / "rows.csv"
    data.write_text("Employee,Extra\nA,x\n")
    assert main(["form", src, "--fill-from", str(data),
                 "-o", str(tmp_path / "out")]) == 0
    out = capsys.readouterr().out
    assert "filled 1 cop(ies)" in out and "match no form field: Extra" in out
