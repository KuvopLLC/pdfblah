"""Tests for sanitize: hidden data found, reported, and stripped."""
import pikepdf
from reportlab.pdfgen import canvas

import pdfblah as pb


def _dirty_pdf(path):
    """A PDF carrying: docinfo metadata, a highlight annotation, doc-level
    JavaScript, an embedded file, and a form field (which must SURVIVE)."""
    c = canvas.Canvas(str(path), pagesize=(400, 300))
    c.setAuthor("Jane Author")
    c.setTitle("Internal Draft v3")
    c.setFont("Helvetica", 12)
    c.drawString(30, 200, "page content stays")
    c.acroForm.textfield(name="who", x=30, y=100, width=200, height=20)
    c.showPage()
    c.save()
    with pikepdf.open(str(path), allow_overwriting_input=True) as pdf:
        page = pdf.pages[0]
        note = pdf.make_indirect(pikepdf.Dictionary(
            Type=pikepdf.Name("/Annot"), Subtype=pikepdf.Name("/Highlight"),
            Rect=[30, 190, 200, 215], Contents=pikepdf.String("do not ship this comment"),
        ))
        page.Annots.append(note)
        js = pdf.make_indirect(pikepdf.Dictionary(
            S=pikepdf.Name("/JavaScript"), JS=pikepdf.String("app.alert('hi')")))
        pdf.Root.Names = pdf.make_indirect(pikepdf.Dictionary(
            JavaScript=pikepdf.Dictionary(Names=pikepdf.Array(
                [pikepdf.String("init"), js]))))
        pdf.attachments["salaries.xlsx"] = b"very secret bytes"
        pdf.save(str(path))
    return str(path)


def test_dry_run_reports_everything_and_writes_nothing(tmp_path):
    src = _dirty_pdf(tmp_path / "in.pdf")
    r = pb.sanitize(src, dry_run=True)
    assert r["ok"] and r["output"] is None
    f = r["found"]
    assert "/Author" in f["metadata"] and "/Title" in f["metadata"]
    assert f["annotations"] == {"Highlight": 1}
    assert f["javascript"] >= 1
    assert f["embedded_files"] == ["salaries.xlsx"]
    assert list(tmp_path.iterdir()) == [tmp_path / "in.pdf"]


def test_sanitize_strips_but_keeps_content_and_form(tmp_path):
    src = _dirty_pdf(tmp_path / "in.pdf")
    out = tmp_path / "safe.pdf"
    r = pb.sanitize(src, str(out))
    assert r["ok"], r
    assert any("metadata" in x for x in r["removed"])
    assert any("annotations (1)" in x for x in r["removed"])
    assert any("javascript" in x for x in r["removed"])
    assert any("embedded files (1)" in x for x in r["removed"])
    with pikepdf.open(str(out)) as pdf:
        assert "/Author" not in pdf.docinfo and "/Title" not in pdf.docinfo
        names = pdf.Root.get("/Names")
        assert names is None or "/JavaScript" not in names
        assert len(pdf.attachments) == 0
        # the form widget survives; the highlight does not
        subs = [str(a.Subtype) for a in pdf.pages[0].get("/Annots", [])]
        assert subs == ["/Widget"]
    import pdfplumber
    with pdfplumber.open(str(out)) as pdf:
        assert "page content stays" in (pdf.pages[0].extract_text() or "")


def test_keep_annotations_flag(tmp_path):
    src = _dirty_pdf(tmp_path / "in.pdf")
    out = tmp_path / "safe.pdf"
    r = pb.sanitize(src, str(out), keep_annotations=True)
    assert r["ok"] and any("annotations" in x for x in r["kept"])
    with pikepdf.open(str(out)) as pdf:
        subs = sorted(str(a.Subtype) for a in pdf.pages[0].get("/Annots", []))
        assert "/Highlight" in subs


def test_clean_file_reports_clean(tmp_path):
    c = canvas.Canvas(str(tmp_path / "in.pdf"), pagesize=(400, 300))
    c.drawString(30, 150, "nothing to hide")
    c.showPage()
    c.save()
    # reportlab still writes producer/creator metadata; check the report is honest
    r = pb.sanitize(str(tmp_path / "in.pdf"), str(tmp_path / "out.pdf"))
    assert r["ok"] and r["found"]["embedded_files"] == [] and r["found"]["javascript"] == 0


def test_sanitize_cli_dry_run(tmp_path, capsys):
    from pdfblah.cli import main
    src = _dirty_pdf(tmp_path / "in.pdf")
    assert main(["sanitize", src, "--dry-run"]) == 0
    out = capsys.readouterr().out
    assert "this file is carrying" in out and "Highlight x1" in out
    assert "embedded files: 1" in out
