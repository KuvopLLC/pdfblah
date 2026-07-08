import pdfplumber
from reportlab.pdfgen import canvas

from pdfblah import process
from pdfblah.metadata import read_metadata, edit_metadata


def make_pdf_with_meta(path):
    c = canvas.Canvas(str(path), pagesize=(220, 200))
    c.setTitle("Secret Title")
    c.setAuthor("Jane Doe")
    c.setSubject("Confidential Subject")
    c.drawString(20, 100, "hello world")
    c.save()
    return str(path)


def alltext(path):
    with pdfplumber.open(str(path)) as pdf:
        return "\n".join((p.extract_text() or "") for p in pdf.pages)


def _blob(path):
    r = read_metadata(path)
    return str(r["docinfo"]) + str(r["xmp"])


def test_read_metadata_reports_fields(tmp_path):
    src = make_pdf_with_meta(tmp_path / "in.pdf")
    r = read_metadata(src)
    assert r["pages"] == 1 and r["encrypted"] is False
    assert r["pdf_version"]
    assert "Jane Doe" in _blob(src) and "Secret Title" in _blob(src)


def test_strip_metadata(tmp_path):
    src = make_pdf_with_meta(tmp_path / "in.pdf")
    o = str(tmp_path / "o.pdf")
    r = edit_metadata(src, o, strip=True)
    assert r["ok"]
    blob = _blob(o)
    assert "Jane Doe" not in blob and "Secret Title" not in blob
    assert "hello world" in alltext(o)                # page content untouched


def test_set_metadata(tmp_path):
    src = make_pdf_with_meta(tmp_path / "in.pdf")
    o = str(tmp_path / "o.pdf")
    edit_metadata(src, o, sets={"author": "New Person", "title": "New Title"})
    blob = _blob(o)
    assert "New Person" in blob and "New Title" in blob and "Jane Doe" not in blob


def test_strip_then_set(tmp_path):
    src = make_pdf_with_meta(tmp_path / "in.pdf")
    o = str(tmp_path / "o.pdf")
    edit_metadata(src, o, strip=True, sets={"author": "Clean Author"})
    blob = _blob(o)
    assert "Clean Author" in blob and "Jane Doe" not in blob


def test_meta_refuses_overwrite(tmp_path):
    src = make_pdf_with_meta(tmp_path / "in.pdf")
    r = edit_metadata(src, src, strip=True)
    assert not r["ok"]


def test_other_commands_keep_metadata(tmp_path):
    # the website relies on this: a normal edit must NOT touch metadata
    src = make_pdf_with_meta(tmp_path / "in.pdf")
    o = str(tmp_path / "o.pdf")
    process(src, o, "hello", "howdy")
    assert "howdy" in alltext(o)
    assert "Jane Doe" in _blob(o)                     # author preserved


def test_apply_rules_strip_meta(tmp_path):
    from pdfblah import apply_rules
    src = make_pdf_with_meta(tmp_path / "in.pdf")
    o = str(tmp_path / "o.pdf")
    rep = apply_rules(src, o, [{"find": "hello", "replace": "hi"}], strip_meta=True)
    assert "stripped" in rep["meta_changed"]
    assert "Jane Doe" not in _blob(o) and "hi" in alltext(o)


def test_apply_rules_set_meta(tmp_path):
    from pdfblah import apply_rules
    src = make_pdf_with_meta(tmp_path / "in.pdf")
    o = str(tmp_path / "o.pdf")
    apply_rules(src, o, [{"find": "hello", "replace": "hi"}], set_meta={"author": "New A"})
    assert "New A" in _blob(o) and "Jane Doe" not in _blob(o)


def test_parse_rules_meta_directives():
    from pdfblah import parse_rules
    pr = parse_rules("@strip-metadata\n@set-metadata author = Jane Roe\nOld | New | all\n")
    assert pr["strip_meta"] is True
    assert pr["set_meta"] == {"author": "Jane Roe"}
    assert len(pr["rules"]) == 1 and pr["rules"][0]["find"] == "Old"


def test_rules_file_meta_directive_applied(tmp_path):
    from pdfblah import parse_rules, apply_rules
    src = make_pdf_with_meta(tmp_path / "in.pdf")
    o = str(tmp_path / "o.pdf")
    pr = parse_rules("@strip-metadata\nhello | hi | all\n")
    apply_rules(src, o, pr["rules"], pr["strip_meta"], pr["set_meta"])
    assert "Jane Doe" not in _blob(o) and "hi" in alltext(o)


def test_cli_replace_with_strip_metadata(tmp_path):
    from pdfblah.cli import main
    src = make_pdf_with_meta(tmp_path / "in.pdf")
    o = str(tmp_path / "o.pdf")
    rc = main([src, o, "--find", "hello", "--replace", "hi", "--strip-metadata"])
    assert rc == 0
    assert "Jane Doe" not in _blob(o) and "hi" in alltext(o)


def test_cli_redact_with_set_metadata(tmp_path):
    from pdfblah.cli import main
    src = make_pdf_with_meta(tmp_path / "in.pdf")
    o = str(tmp_path / "o.pdf")
    rc = main(["redact", src, o, "--find", "hello", "--set-metadata", "author=New Auth"])
    assert rc == 0
    assert "New Auth" in _blob(o) and "hello" not in alltext(o)
