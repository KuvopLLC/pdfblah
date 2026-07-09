#!/usr/bin/env python3
"""Fixture PDFs for the workbench browser E2E (tests/e2e/workbench.e2e.mjs).

  report.pdf   3 letter pages of real text; "Acme Corp" appears once per page
  scanned.pdf  image-only page (no text layer) -> must get the scan badge
  inspect.pdf  report.pdf + docinfo (Title/Author) + an embedded CSV attachment
  terms-schedule.csv  the attachment's bytes, for byte-for-byte extract checks

Usage: make_fixtures.py OUTDIR   (needs the package's base+test deps: reportlab, PIL, pikepdf)
"""
import os
import shutil
import sys

import pikepdf
import reportlab
from PIL import Image, ImageDraw
from reportlab.lib.pagesizes import letter
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.pdfgen import canvas


def main(outdir):
    # report.pdf
    c = canvas.Canvas(f"{outdir}/report.pdf", pagesize=letter)
    for i in range(1, 4):
        c.setFont("Helvetica-Bold", 28)
        c.drawString(72, 720, f"Quarterly Report — Page {i}")
        c.setFont("Helvetica", 13)
        for j, line in enumerate([
            "This is a real text PDF with selectable text.",
            "The workbench should NOT flag it as a scan.",
            f"Confidential figures for section {i}. Revenue up 12%.",
            "Invoice total: $4,200.00   Client: Acme Corp",
        ]):
            c.drawString(72, 670 - j * 22, line)
        c.setFont("Helvetica-Oblique", 10)
        c.drawString(72, 60, f"pdfblah workbench test · page {i} of 3")
        c.showPage()
    c.save()

    # scanned.pdf (image-only)
    img = Image.new("RGB", (1000, 1400), "white")
    dr = ImageDraw.Draw(img)
    dr.rectangle([40, 40, 960, 1360], outline="black", width=3)
    dr.text((90, 120), "SCANNED DOCUMENT", fill="black")
    for y in range(300, 1300, 60):
        dr.line([90, y, 910, y], fill=(180, 180, 180), width=1)
    img.save(f"{outdir}/scan_src.png")
    img.save(f"{outdir}/scanned.pdf", "PDF")

    # fontrefuse.pdf: text in an EMBEDDED SUBSET font (reportlab's bundled Vera), so any
    # replacement using glyphs outside the subset (e.g. "Ωmega Ltd") is detect-and-refused
    pdfmetrics.registerFont(TTFont("Vera", os.path.join(os.path.dirname(reportlab.__file__), "fonts", "Vera.ttf")))
    c = canvas.Canvas(f"{outdir}/fontrefuse.pdf", pagesize=letter)
    c.setFont("Vera", 14)
    c.drawString(72, 700, "Client: Acme Corp")
    c.save()

    # cidfont.pdf: a committed Identity-H (CID) fixture; its text can't be rewritten,
    # so refusal must be honest and the substitute offer must not appear
    shutil.copy(os.path.join(os.path.dirname(__file__), "..", "data", "cidfont.pdf"),
                f"{outdir}/cidfont.pdf")

    # inspect.pdf + the attachment bytes
    att = b"col_a,col_b\n1,2\n3,4\n" * 40
    with open(f"{outdir}/terms-schedule.csv", "wb") as f:
        f.write(att)
    with pikepdf.open(f"{outdir}/report.pdf") as pdf:
        pdf.docinfo["/Title"] = "Master Services Agreement"
        pdf.docinfo["/Author"] = "K. Osei"
        pdf.docinfo["/Producer"] = "Word 16.89"
        pdf.attachments["terms-schedule.csv"] = pikepdf.AttachedFileSpec.from_filepath(
            pdf, f"{outdir}/terms-schedule.csv")
        pdf.save(f"{outdir}/inspect.pdf")
    print(f"fixtures written to {outdir}")


if __name__ == "__main__":
    main(sys.argv[1])
