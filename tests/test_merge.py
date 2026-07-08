import os

import pdfplumber
from reportlab.pdfgen import canvas

from pdfblah.commands import merge, load_data


def make_pdf(path, lines, font="Helvetica", size=14):
    c = canvas.Canvas(str(path), pagesize=(500, 300))
    c.setFont(font, size)
    for i, t in enumerate(lines):
        c.drawString(30, 250 - i * 30, t)
    c.save()
    return str(path)


def alltext(path):
    with pdfplumber.open(str(path)) as pdf:
        return "\n".join((p.extract_text() or "") for p in pdf.pages)


def test_merge_basic(tmp_path):
    tpl = make_pdf(tmp_path / "tpl.pdf", ["Invoice for {{name}}", "Amount due {{amount}}"])
    rows = [{"name": "Alice", "amount": "100"}, {"name": "Bob", "amount": "200"}]
    outdir = str(tmp_path / "out")
    r = merge(tpl, rows, outdir)
    assert r["count"] == 2
    files = sorted(os.listdir(outdir))
    assert files == ["row_0001.pdf", "row_0002.pdf"]
    texts = "\n".join(alltext(os.path.join(outdir, f)) for f in files)
    assert "Alice" in texts and "Bob" in texts and "100" in texts and "200" in texts
    assert "{{name}}" not in texts and "{{amount}}" not in texts


def test_merge_name_col(tmp_path):
    tpl = make_pdf(tmp_path / "tpl.pdf", ["Hello {{name}}"])
    rows = [{"name": "Alice"}, {"name": "Bob Jones"}]
    outdir = str(tmp_path / "out")
    merge(tpl, rows, outdir, name_col="name")
    files = set(os.listdir(outdir))
    assert files == {"Alice.pdf", "Bob_Jones.pdf"}       # sanitized


def test_merge_custom_placeholder(tmp_path):
    tpl = make_pdf(tmp_path / "tpl.pdf", ["Dear <<name>>"])
    outdir = str(tmp_path / "out")
    merge(tpl, [{"name": "Zoe"}], outdir, placeholder="<<FIELD>>")
    assert "Zoe" in alltext(os.path.join(outdir, "row_0001.pdf"))


def test_load_data_csv(tmp_path):
    p = tmp_path / "d.csv"
    p.write_text("name,amount\nAlice,100\nBob,200\n")
    assert load_data(str(p)) == [{"name": "Alice", "amount": "100"},
                                 {"name": "Bob", "amount": "200"}]


def test_load_data_json(tmp_path):
    p = tmp_path / "d.json"
    p.write_text('[{"name": "Al"}, {"name": "Bo"}]')
    assert load_data(str(p)) == [{"name": "Al"}, {"name": "Bo"}]
