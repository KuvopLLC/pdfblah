import re

import pdfplumber
import pikepdf
import pytest
from reportlab.pdfgen import canvas

from pdfblah import process, apply_rules, parse_rules_file, parse_flags, font_safe


def make_pdf(path, lines, font="Helvetica", size=16):
    c = canvas.Canvas(str(path), pagesize=(400, 300))
    c.setFont(font, size)
    for i, t in enumerate(lines):
        c.drawString(40, 250 - i * 40, t)
    c.save()
    return str(path)


def alltext(path):
    with pdfplumber.open(str(path)) as pdf:
        return "\n".join((p.extract_text() or "") for p in pdf.pages)


@pytest.fixture
def reps(tmp_path):
    return make_pdf(tmp_path / "in.pdf",
                    ["apple one", "apple two", "Apple three", "pineapple four", "apple five"])


def test_scope_first(reps, tmp_path):
    o = str(tmp_path / "o.pdf")
    r = process(reps, o, "apple", "APRICOT", scope="first")
    assert r["ok"] and r["count"] == 1
    assert alltext(o).count("APRICOT") == 1


def test_scope_all(reps, tmp_path):
    o = str(tmp_path / "o.pdf")
    r = process(reps, o, "apple", "APRICOT", scope="all")
    assert r["count"] == 4
    assert alltext(o).count("APRICOT") == 4


def test_scope_nth(reps, tmp_path):
    o = str(tmp_path / "o.pdf")
    r = process(reps, o, "apple", "APRICOT", scope=2)
    assert r["count"] == 1


def test_ignore_case(reps, tmp_path):
    o = str(tmp_path / "o.pdf")
    r = process(reps, o, "apple", "APRICOT", scope="all", ci=True)
    assert r["count"] == 5           # also catches "Apple three"


def test_whole_word(reps, tmp_path):
    o = str(tmp_path / "o.pdf")
    r = process(reps, o, "apple", "APRICOT", scope="all", word=True)
    assert r["count"] == 3           # spares "pineapple"
    assert "pineapple" in alltext(o)


def test_delete(reps, tmp_path):
    o = str(tmp_path / "o.pdf")
    r = process(reps, o, "apple", "", scope="all", word=True)
    assert r["ok"]
    assert "apple one" not in alltext(o)


def test_not_found(reps, tmp_path):
    r = process(reps, str(tmp_path / "o.pdf"), "banana", "x")
    assert not r["ok"] and "not found" in r["error"]


def test_no_overwrite(reps):
    r = process(reps, reps, "apple", "x")
    assert not r["ok"]


def test_metadata_preserved(reps, tmp_path):
    o = str(tmp_path / "o.pdf")
    with pikepdf.open(reps) as p:
        before = {str(k): str(v) for k, v in (p.docinfo or {}).items()}
    process(reps, o, "apple", "APRICOT", scope="all")
    with pikepdf.open(o) as p:
        after = {str(k): str(v) for k, v in (p.docinfo or {}).items()}
    for k in ("/Producer", "/CreationDate"):
        if k in before:
            assert before[k] == after[k]


def test_font_safe_refuses_exotic():
    exotic = {"/Resources": {"/Font": {"/F1": {"/BaseFont": "/C0A075B0.afm"}}}}
    safe, reason = font_safe(exotic, "C0A075B0.afm", "NEW", {})
    assert safe is False and "common font" in reason


def test_font_safe_accepts_common_nonembedded():
    for base in ("/Helvetica", "/TimesNewRomanPSMT", "/Arial,Bold", "/CourierNewPSMT"):
        page = {"/Resources": {"/Font": {"/F1": {"/BaseFont": base}}}}
        safe, _ = font_safe(page, base.lstrip("/"), "NEW", {})
        assert safe is True, base


def test_tj_cross_element_rewrite():
    from pdfblah.engine import _rewrite_one
    from pikepdf import String, Array
    # kerned "he"[5]"llo": replace the joined "hello"[0:5] with "HI"
    operands = [Array([String(b"he"), 5, String(b"llo")])]
    out, op = _rewrite_one(operands, "TJ", 0, 5, "HI", "left", 10, {}, 0.5)
    joined = "".join(bytes(e).decode("latin-1") for e in out[0] if isinstance(e, (String, bytes)))
    assert str(op) == "TJ" and joined == "HI"


def make_glyph_pdf(path, text="3.100,00", tail="ZZ", size=10):
    """Build a PDF that draws `text` one glyph at a time (each character its own Tj
    with a Td advance between), then a big Td jump to `tail`. This mimics the payslip
    and bank PDFs that position every character individually."""
    pdf = pikepdf.Pdf.new()
    font = pdf.make_indirect(pikepdf.Dictionary(
        Type=pikepdf.Name.Font, Subtype=pikepdf.Name.Type1,
        BaseFont=pikepdf.Name.Helvetica))
    parts = ["BT", "/F1 %d Tf" % size, "60 150 Td"]
    for i, ch in enumerate(text):
        parts.append("(%s) Tj" % ch)
        if i < len(text) - 1:
            parts.append("%d 0 Td" % (3 if ch in ".," else 6))
    parts.append("80 0 Td")                 # big jump -> a separate field
    parts.append("(%s) Tj" % tail)          # tail as one ordinary multi-char op
    parts.append("ET")
    page = pdf.add_blank_page(page_size=(300, 200))
    page.Contents = pdf.make_stream("\n".join(parts).encode("latin-1"))
    page.Resources = pikepdf.Dictionary(Font=pikepdf.Dictionary(F1=font))
    pdf.save(str(path))
    return str(path)


def _word_x0(path, needle):
    with pdfplumber.open(str(path)) as pdf:
        return next(w["x0"] for w in pdf.pages[0].extract_words() if needle in w["text"])


def test_glyph_fields_splits_on_column_jump(tmp_path):
    from pdfblah.engine import glyph_fields
    src = make_glyph_pdf(tmp_path / "g.pdf", text="3.100,00", tail="ZZ")
    with pikepdf.open(src) as pdf:
        fields = glyph_fields(list(pikepdf.parse_content_stream(pdf.pages[0])))
    assert len(fields) == 1 and fields[0]["text"] == "3.100,00"   # ZZ is not a field


def test_glyph_by_glyph_replace(tmp_path):
    src = make_glyph_pdf(tmp_path / "g.pdf", text="3.100,00", tail="ZZ")
    o = str(tmp_path / "o.pdf")
    r = process(src, o, "3.100,00", "36.857,16", scope="all")
    assert r["ok"] and r["count"] == 1
    t = alltext(o)
    assert "36.857,16" in t and "3.100,00" not in t and "ZZ" in t   # neighbor intact


def test_glyph_replace_preserves_downstream(tmp_path):
    src = make_glyph_pdf(tmp_path / "g.pdf", text="3.100,00", tail="ZZ")
    before = _word_x0(src, "ZZ")
    o = str(tmp_path / "o.pdf")
    # a much LONGER replacement must not shift the following field (compensating Td)
    process(src, o, "3.100,00", "123.456.789,00", scope="all")
    assert abs(_word_x0(o, "ZZ") - before) < 1.0


def op_counts(path):
    from collections import Counter
    with pikepdf.open(str(path)) as pdf:
        c = Counter()
        for _, op in pikepdf.parse_content_stream(pdf.pages[0]):
            c[str(op)] += 1
        return c


def test_redact_removes_text(tmp_path):
    from pdfblah import redact
    src = make_pdf(tmp_path / "in.pdf", ["Secret ABC-123 end"])
    o = str(tmp_path / "o.pdf")
    r = redact(src, o, "ABC-123")
    assert r["ok"] and r["redacted"] == 1
    assert "ABC-123" not in alltext(o) and "Secret" in alltext(o)


def test_redact_draws_bar_only_when_asked(tmp_path):
    from pdfblah import redact
    src = make_pdf(tmp_path / "in.pdf", ["Secret ABC end"])
    withbar = str(tmp_path / "b.pdf"); nobar = str(tmp_path / "n.pdf")
    rb = redact(src, withbar, "ABC", bar=True)
    rn = redact(src, nobar, "ABC", bar=False)
    assert rb["bars"] == 1 and rn["bars"] == 0
    assert op_counts(withbar)["re"] > op_counts(nobar).get("re", 0)
    assert op_counts(withbar)["f"] >= 1
    assert "ABC" not in alltext(withbar) and "ABC" not in alltext(nobar)


def test_redact_regex_spaced(tmp_path):
    from pdfblah import redact
    src = make_pdf(tmp_path / "in.pdf", ["cards 4242 4242 and 1111 2222 x"])
    o = str(tmp_path / "o.pdf")
    r = redact(src, o, r"\d{4} \d{4}", regex=True)
    assert r["ok"] and r["redacted"] == 2
    t = alltext(o)
    assert "4242 4242" not in t and "1111 2222" not in t


def test_redact_glyph_field(tmp_path):
    from pdfblah import redact
    src = make_glyph_pdf(tmp_path / "g.pdf", text="3.100,00", tail="ZZ")
    o = str(tmp_path / "o.pdf")
    r = redact(src, o, "3.100,00")
    assert r["ok"] and "3.100,00" not in alltext(o) and "ZZ" in alltext(o)


def test_redact_metadata_preserved(tmp_path):
    from pdfblah import redact
    src = make_pdf(tmp_path / "in.pdf", ["remove ME please"])
    with pikepdf.open(src) as p:
        before = {str(k): str(v) for k, v in (p.docinfo or {}).items()}
    o = str(tmp_path / "o.pdf")
    redact(src, o, "ME")
    with pikepdf.open(o) as p:
        after = {str(k): str(v) for k, v in (p.docinfo or {}).items()}
    for k in ("/Producer", "/CreationDate"):
        if k in before:
            assert before[k] == after[k]


def test_parse_flags():
    assert parse_flags("all ci") == {"scope": "all", "ci": True, "word": False, "regex": False}
    assert parse_flags("3 word") == {"scope": 3, "ci": False, "word": True, "regex": False}
    assert parse_flags("p2")["page"] == 2
    assert parse_flags("re")["regex"] is True
    assert parse_flags("regex")["regex"] is True
    assert parse_flags("") == {"scope": "first", "ci": False, "word": False, "regex": False}


def test_regex_replace(tmp_path):
    src = make_pdf(tmp_path / "in.pdf", ["Invoice 2021-0042", "Ref 2021-0043"])
    o = str(tmp_path / "o.pdf")
    r = process(src, o, r"\d{4}-\d{4}", "REDACTED", scope="all", regex=True)
    assert r["ok"] and r["count"] == 2
    t = alltext(o)
    assert "REDACTED" in t and "2021-0042" not in t and "2021-0043" not in t


def test_regex_backref(tmp_path):
    src = make_pdf(tmp_path / "in.pdf", ["date 2021-07"])
    o = str(tmp_path / "o.pdf")
    r = process(src, o, r"(\d{4})-(\d{2})", r"\2/\1", scope="all", regex=True)
    assert r["ok"]
    assert "07/2021" in alltext(o) and "2021-07" not in alltext(o)


def test_regex_delete(tmp_path):
    src = make_pdf(tmp_path / "in.pdf", ["call 555-1234 now"])
    o = str(tmp_path / "o.pdf")
    r = process(src, o, r"\d{3}-\d{4}", "", scope="all", regex=True)
    assert r["ok"] and "555-1234" not in alltext(o)


def test_regex_repl_callable(tmp_path):
    src = make_pdf(tmp_path / "in.pdf", ["a 10 b 20 c 30"])
    o = str(tmp_path / "o.pdf")
    # double every number via a per-match callable
    r = process(src, o, r"\d+", "", scope="all", regex=True,
                repl=lambda m: str(int(m.group(0)) * 2))
    assert r["ok"] and r["count"] == 3
    t = alltext(o)
    assert "20" in t and "40" in t and "60" in t


def test_regex_invalid_pattern(tmp_path):
    src = make_pdf(tmp_path / "in.pdf", ["hello"])
    with pytest.raises(re.error):
        process(src, str(tmp_path / "o.pdf"), r"(unclosed", "x", regex=True)


def test_regex_on_glyph_field(tmp_path):
    src = make_glyph_pdf(tmp_path / "g.pdf", text="3.100,00", tail="ZZ")
    o = str(tmp_path / "o.pdf")
    r = process(src, o, r"\d\.\d{3},\d{2}", "9.999,99", scope="all", regex=True)
    assert r["ok"] and r["count"] == 1
    t = alltext(o)
    assert "9.999,99" in t and "3.100,00" not in t and "ZZ" in t


def test_rules_file(reps, tmp_path):
    rules = parse_rules_file("# comment\napple | APRICOT | all\nApple | X | ci\nbanana | y")
    assert len(rules) == 3
    assert rules[0]["scope"] == "all"
    o = str(tmp_path / "o.pdf")
    rep = apply_rules(reps, o, rules)
    assert rep["applied"] == 2 and rep["total"] == 3   # banana not found -> skipped
