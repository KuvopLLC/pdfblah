import os

import pikepdf
import pdfplumber
import pytest
from PIL import Image
from reportlab.pdfgen import canvas

import pdfblah as pb
from pdfblah import organize


# ---------- fixtures ----------
def make_doc(path, pages=3, meta=True):
    c = canvas.Canvas(str(path), pagesize=(400, 300))
    for i in range(pages):
        c.setFont("Helvetica", 16)
        c.drawString(40, 250, f"Page {i + 1} heading")
        c.drawString(40, 200, f"body text {i + 1}")
        c.showPage()
    if meta:
        c.setTitle("My Title")
        c.setAuthor("Alice")
    c.save()
    return str(path)


def make_image(path, size=(1200, 900), color=(200, 80, 40)):
    Image.new("RGB", size, color).save(str(path))
    return str(path)


def make_image_pdf(path, img):
    c = canvas.Canvas(str(path), pagesize=(400, 300))
    c.drawImage(img, 20, 20, 360, 260)
    c.save()
    return str(path)


def text_of(path):
    with pdfplumber.open(str(path)) as pdf:
        return "\n".join((p.extract_text() or "") for p in pdf.pages)


def npages(path):
    with pikepdf.open(str(path)) as pdf:
        return len(pdf.pages)


# ---------- ranges ----------
def test_parse_ranges():
    assert organize.parse_ranges("all", 5) == [0, 1, 2, 3, 4]
    assert organize.parse_ranges("1-3,5", 5) == [0, 1, 2, 4]
    assert organize.parse_ranges("-2", 5) == [0, 1]
    assert organize.parse_ranges("4-", 5) == [3, 4]
    assert organize.parse_ranges("3,1,2", 5) == [2, 0, 1]  # order preserved
    with pytest.raises(ValueError):
        organize.parse_ranges("9", 5)


# ---------- structure ----------
def test_combine_keeps_first_metadata(tmp_path):
    a = make_doc(tmp_path / "a.pdf", 2)
    b = make_doc(tmp_path / "b.pdf", 3, meta=False)
    out = str(tmp_path / "c.pdf")
    r = pb.combine([a, b], out)
    assert r["ok"] and r["pages"] == 5 and npages(out) == 5
    assert pb.read_metadata(out)["docinfo"].get("Title") == "My Title"


def test_split_every_and_ranges(tmp_path):
    src = make_doc(tmp_path / "s.pdf", 5)
    r = pb.split(src, str(tmp_path / "each"), every=2)
    assert r["ok"] and r["parts"] == 3 and all(os.path.exists(p) for p in r["outputs"])
    r2 = pb.split(src, str(tmp_path / "ranges"), ranges=["1-2", "5"])
    assert r2["parts"] == 2 and npages(r2["outputs"][0]) == 2 and npages(r2["outputs"][1]) == 1


def test_select_reorder_and_drop(tmp_path):
    src = make_doc(tmp_path / "s.pdf", 3)
    keep = str(tmp_path / "k.pdf")
    pb.select_pages(src, keep, keep="3,1")
    t = text_of(keep)
    assert npages(keep) == 2 and t.index("Page 3") < t.index("Page 1")
    drop = str(tmp_path / "d.pdf")
    pb.select_pages(src, drop, drop="2")
    assert npages(drop) == 2 and "body text 2" not in text_of(drop)


def test_rotate_and_crop(tmp_path):
    src = make_doc(tmp_path / "s.pdf", 2)
    rot = str(tmp_path / "r.pdf")
    pb.rotate(src, rot, 90)
    with pikepdf.open(rot) as pdf:
        assert int(pdf.pages[0].get("/Rotate", 0)) == 90
    cr = str(tmp_path / "c.pdf")
    pb.crop(src, cr, margins=(20, 20, 20, 20))
    with pikepdf.open(cr) as pdf:
        cb = [float(v) for v in pdf.pages[0].CropBox]
        assert cb == [20, 20, 380, 280]


# ---------- render / extract ----------
def test_render_and_extract_text(tmp_path):
    src = make_doc(tmp_path / "s.pdf", 2)
    r = pb.render_pages(src, str(tmp_path / "img"), dpi=72)
    assert r["ok"] and r["pages"] == 2 and all(os.path.getsize(p) > 100 for p in r["outputs"])
    t = pb.extract_text(src, str(tmp_path / "out.txt"))
    assert "Page 1 heading" in t["text"] and os.path.exists(t["output"])


def test_extract_images(tmp_path):
    img = make_image(tmp_path / "pic.png")
    src = make_image_pdf(tmp_path / "img.pdf", img)
    r = pb.extract_images(src, str(tmp_path / "out"))
    assert r["ok"] and r["images"] >= 1 and all(os.path.exists(p) for p in r["outputs"])


# ---------- security ----------
def test_protect_unlock_roundtrip(tmp_path):
    src = make_doc(tmp_path / "s.pdf", 1)
    enc = str(tmp_path / "enc.pdf")
    pb.protect(src, enc, user_password="secret", allow_copy=False)
    with pytest.raises(pikepdf.PasswordError):
        pikepdf.open(enc)
    with pikepdf.open(enc, password="secret") as pdf:
        assert pdf.is_encrypted
    dec = str(tmp_path / "dec.pdf")
    r = pb.unlock(enc, dec, password="secret")
    assert r["ok"] and r["was_encrypted"]
    with pikepdf.open(dec) as pdf:
        assert not pdf.is_encrypted
    assert pb.unlock(enc, str(tmp_path / "x.pdf"), password="wrong")["ok"] is False


def test_attachments(tmp_path):
    src = make_doc(tmp_path / "s.pdf", 1)
    note = tmp_path / "note.txt"; note.write_text("hello")
    out = str(tmp_path / "att.pdf")
    r = pb.attachments(src, out, add=[str(note)])
    assert r["ok"] and "note.txt" in r["attachments"]
    ex = pb.attachments(out, extract_to=str(tmp_path / "got"))
    assert any(os.path.basename(p) == "note.txt" for p in ex["extracted"])


def test_optimize_downsamples_image(tmp_path):
    img = make_image(tmp_path / "big.png", size=(3000, 2400))
    src = make_image_pdf(tmp_path / "img.pdf", img)
    out = str(tmp_path / "small.pdf")
    r = pb.optimize(src, out, downsample_dpi=72)
    assert r["ok"] and r["images_recompressed"] >= 1 and r["after"] < r["before"]


# ---------- marks ----------
def test_watermark_text_keeps_original(tmp_path):
    src = make_doc(tmp_path / "s.pdf", 2)
    out = str(tmp_path / "wm.pdf")
    r = pb.watermark(src, out, text="CONFIDENTIAL", rotation=0, opacity=0.5)
    assert r["ok"] and npages(out) == 2
    t = text_of(out)
    assert "CONFIDENTIAL" in t and "Page 1 heading" in t  # mark added, original intact


def test_watermark_image_and_stamp_pdf(tmp_path):
    img = make_image(tmp_path / "logo.png", size=(300, 300))
    src = make_doc(tmp_path / "s.pdf", 1)
    out = str(tmp_path / "wmi.pdf")
    assert pb.watermark(src, out, image=img, opacity=0.4)["ok"] and npages(out) == 1
    stamp_pdf = make_doc(tmp_path / "logo.pdf", 1)
    out2 = str(tmp_path / "st.pdf")
    assert pb.stamp(src, out2, stamp_pdf=stamp_pdf, position="top-right")["ok"]


def test_number_and_bates(tmp_path):
    src = make_doc(tmp_path / "s.pdf", 3)
    out = str(tmp_path / "n.pdf")
    pb.number(src, out, fmt="Page {n} of {total}")
    assert "Page 1 of 3" in text_of(out)
    bout = str(tmp_path / "b.pdf")
    pb.bates(bout if False else src, bout, prefix="ACME", digits=5, start=100)
    assert "ACME00100" in text_of(bout)


# ---------- forms ----------
def make_form(path):
    c = canvas.Canvas(str(path), pagesize=(400, 300))
    c.drawString(40, 250, "Name:")
    c.acroForm.textfield(name="fullname", x=120, y=240, width=200, height=20, value="")
    c.save()
    return str(path)


def test_form_list_fill_flatten(tmp_path):
    src = make_form(tmp_path / "f.pdf")
    lst = pb.form_list(src)
    assert lst["count"] == 1 and lst["fields"][0]["name"] == "fullname"
    out = str(tmp_path / "filled.pdf")
    r = pb.form_fill(src, out, {"fullname": "Jane Roe"}, flatten=True)
    assert r["ok"] and "fullname" in r["filled"] and r["flattened"]
    with pikepdf.open(out) as pdf:
        assert "/AcroForm" not in pdf.Root


# ---------- compare ----------
def test_compare_text_and_visual(tmp_path):
    a = make_doc(tmp_path / "a.pdf", 2)
    c = canvas.Canvas(str(tmp_path / "b.pdf"), pagesize=(400, 300))
    c.setFont("Helvetica", 16)
    c.drawString(40, 250, "Page 1 heading")
    c.drawString(40, 200, "CHANGED body")
    c.rect(40, 60, 300, 80, fill=1)  # an unmistakable visual change on page 1
    c.showPage()
    c.drawString(40, 250, "Page 2 heading")
    c.drawString(40, 200, "body text 2")
    c.showPage()
    c.save()
    r = pb.compare_pdfs(a, str(tmp_path / "b.pdf"), visual=True, out_dir=str(tmp_path / "diff"), dpi=72)
    assert r["ok"] and r["changed"] == 1
    p1 = r["detail"][0]
    assert not p1["identical"] and any("CHANGED" in x for x in p1["added"])
    # page 1 has a big filled box, page 2 is unchanged: clearly more diff on page 1
    assert r["visual"][0]["diff"] > 0.05 and r["visual"][0]["diff"] > r["visual"][1]["diff"]


# ---------- signatures ----------
def test_signatures_list_and_validate_hint(tmp_path):
    src = make_doc(tmp_path / "s.pdf", 1)
    assert pb.list_signatures(src)["count"] == 0
    v = pb.validate_signatures(src)
    # pyHanko not installed in the base test env -> friendly hint
    if not v["ok"]:
        assert v["code"] == "needs_sign"
    else:
        assert "signatures" in v
