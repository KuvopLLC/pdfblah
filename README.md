# pdfblah

[![PyPI](https://img.shields.io/pypi/v/pdfblah)](https://pypi.org/project/pdfblah/)
[![Python](https://img.shields.io/pypi/pyversions/pdfblah)](https://pypi.org/project/pdfblah/)
[![CI](https://github.com/KuvopLLC/pdfblah/actions/workflows/ci.yml/badge.svg)](https://github.com/KuvopLLC/pdfblah/actions/workflows/ci.yml)
[![License: MIT](https://img.shields.io/badge/license-MIT-blue.svg)](LICENSE)

A precise, non-destructive Swiss army knife for PDFs, from your terminal or a local
app in your browser. Replace, redact, and remove text, scrub or anonymize personal
data, edit metadata, watermark, stamp, number, split, merge, rotate, crop, encrypt,
render, compare, fill forms, convert to and from Word, and OCR scans. Fonts, spacing,
and alignment stay perfect, and nothing you didn't ask for is touched.

Most tools "edit" a PDF by painting a box over the old text and drawing new text
on top, which leaves the original underneath (copy and paste still reveals it) and
often adds a watermark. `pdfblah` rewrites the real text in the content stream, so:

- the old text is genuinely gone (`pdftotext`, Ctrl-F, and copy show only the new value)
- no overlay, no watermark
- your metadata (dates, Producer, XMP) is kept byte for byte, unless you choose to edit it
- alignment is auto-detected and kept, so right-aligned numbers stay flush
- fonts it cannot reproduce are refused instead of garbled

Pure Python; the core needs no system tools.

## Install

One line installs the CLI and the local app:

```sh
# macOS / Linux
curl -fsSL https://pdfblah.com/install.sh | sh

# Windows (PowerShell)
irm https://pdfblah.com/install.ps1 | iex
```

Or straight from PyPI:

```sh
pipx install pdfblah      # recommended, isolated; or:  pip install pdfblah
uv tool install pdfblah   # or with uv
```

Python 3.9+, nothing to compile or sign.

## Run it in your browser

Prefer a web interface? One command opens the app in your browser, fully local,
with no upload, no watermark, and no account:

```sh
pdfblah gui
```

Drop in a PDF, add your rules, and save the edited file to your computer. Nothing
leaves your machine. Or keep going for the command line.

## Use

Replace the first match:

```sh
pdfblah in.pdf out.pdf --find "Old Name" --replace "New Name"
```

Options:

```sh
--scope all         change every match           (default: first)
--scope 3           change the 3rd match
--ci                ignore case
--word              whole word only ("cat" will not match "category")
--regex             treat --find as a regex (\1 backrefs work in --replace)
--page 2            only page 2
--replace ""        delete the text
```

Many rules from a file (`FIND | REPLACE | FLAGS` per line):

```sh
pdfblah in.pdf out.pdf --rules rules.txt
```

```
# rules.txt
Old Company Name | New Company Name | all
CONFIDENTIAL DRAFT | FINAL | ci
Jane Doe | John Smith | all word
Total | Sum | 2
delete this phrase |
```

## Commands

Presets on the same engine: locate the real text, rewrite it.

**redact** removes the matched text for real (gone from `pdftotext`, Ctrl-F and copy)
and draws a bar over each spot. `--no-bar` removes the text with no mark.

```sh
pdfblah redact in.pdf out.pdf --find "Account 12345"
pdfblah redact in.pdf out.pdf --find "\d{3}-\d{2}-\d{4}" --regex   # every SSN
```

**scrub** finds structured personal data (email, IBAN, credit card, SSN, phone) and
removes it, or masks it. Cards and IBANs are checksum-validated, so ordinary numbers
are left alone.

```sh
pdfblah scrub in.pdf out.pdf
pdfblah scrub in.pdf out.pdf --types email,credit_card --mask "[redacted]"
```

**anonymize** replaces detected data with realistic, shape-preserving fakes so a
document is safe to share. The same value maps to the same fake; `--seed` makes it
reproducible. Names are swapped only when you list them.

```sh
pdfblah anonymize in.pdf out.pdf --names "Alison Cohen,Matthew Reider" --seed 7
```

**merge** fills a template once per data row: every `{{column}}` placeholder becomes
that row's value, one output PDF per row.

```sh
pdfblah merge template.pdf people.csv --out ./letters --name-col name
```

**meta** reports everything metadata-ish (DocInfo, XMP, pages, version, encryption),
which often reveals more than you expect (author, software, timestamps). With an
output file it can strip or set fields. Every other command keeps metadata intact.

```sh
pdfblah meta in.pdf                                  # report what's in there
pdfblah meta in.pdf clean.pdf --strip                # remove all metadata
pdfblah meta in.pdf out.pdf --set author="Jane Roe"  # set a field
```

Metadata edits can also ride along with any other command, or live in a rules file:

```sh
pdfblah redact in.pdf out.pdf --find "Acme Corp" --strip-metadata
pdfblah in.pdf out.pdf --find OLD --replace NEW --set-metadata author="Ops"
```

```
# rules.txt
@strip-metadata
@set-metadata author = Redacted Dept
CONFIDENTIAL | PUBLIC | all
```

## Toolkit

Beyond editing text, pdfblah does the page-level and document-level jobs you usually
reach for several tools to do. All keep your metadata intact.

**Pages**

```sh
pdfblah combine a.pdf b.pdf c.pdf -o all.pdf     # concatenate
pdfblah combine *.pdf -o binder.pdf --toc --tabs # a meeting binder: clickable Contents
                                                 # page, bookmarks, numbered edge tabs
                                                 # (--titles "A,B,C" --toc-font serif)
pdfblah split in.pdf -o parts/ --every 1         # or --ranges 1-3 4-6
pdfblah pages in.pdf out.pdf --keep 3,1,2        # keep / reorder (or --drop 4)
pdfblah rotate in.pdf out.pdf --degrees 90 --pages 1-2
pdfblah crop in.pdf out.pdf --margins 20,20,20,20
```

**Marks** (honest overlays, drawn on top; your text underneath is untouched)

```sh
pdfblah watermark in.pdf out.pdf --text DRAFT --tile --opacity 0.2 --rotation 45
pdfblah stamp in.pdf out.pdf --image logo.png --position top-right
pdfblah number in.pdf out.pdf --format "Page {n} of {total}"
pdfblah bates in.pdf out.pdf --prefix ACME --digits 6 --start 1
```

**Security & size**

```sh
pdfblah protect in.pdf out.pdf --password secret --no-copy   # AES-256 + permissions
pdfblah unlock in.pdf out.pdf --password secret
pdfblah optimize in.pdf out.pdf --downsample-dpi 150         # shrink the file
pdfblah attachments in.pdf -o out.pdf --add report.csv       # list/add/extract files
```

**Render, extract, inspect**

```sh
pdfblah render in.pdf -o images/ --dpi 150 --format png
pdfblah extract in.pdf --text                # or --images -o out/
pdfblah form in.pdf --list                   # or --fill data.json out.pdf [--flatten]
pdfblah compare a.pdf b.pdf --visual --out-dir diff/
pdfblah signatures in.pdf --validate         # read/validate (needs pdfblah[sign])
```

**Clean scans** (white paper, crisp ink — made for sheet music, works on any scan;
needs the render extra: `pip install "pdfblah[app]"`)

```sh
pdfblah clean scan.pdf out.pdf                   # background -> pure #FFFFFF
pdfblah clean a.pdf b.pdf c.pdf -o cleaned/      # batch: one cleaned PDF per input
pdfblah clean scan.pdf out.pdf --strength strong # dark or stained pages
pdfblah clean scan.pdf out.pdf --bilevel         # pure 1-bit black & white
```

Estimates each page's background (paper tone, lighting, stains), divides it out, and
remaps levels so paper becomes exactly white while ink keeps its anti-aliased edges.
`--dpi` sets output resolution (default 300). A 10-page scan takes a few seconds.
Output pages are re-rendered images; run `ocr` after if you want selectable text.
On a Mac, `curl -fsSL https://pdfblah.com/clean-scan-mac.sh | sh` installs a Finder
Quick Action: select PDFs, right-click, Quick Actions > Clean Scan.

**Convert and OCR** (need system tools; run `pdfblah doctor` to check/install them)

```sh
pip install "pdfblah[convert]"               # PDF <-> Word (also needs LibreOffice for -> PDF)
pdfblah convert in.pdf  -o out.docx          # PDF to editable Word
pdfblah convert in.docx -o out.pdf           # office (DOCX/ODT/PPTX/XLSX) to PDF

pip install "pdfblah[ocr]"                   # searchable scans (needs Tesseract + Ghostscript)
pdfblah ocr scan.pdf -o searchable.pdf --lang eng      # add a real text layer to a scan

pdfblah doctor                               # check the system tools; --install to fetch them
```

## Library

```python
from pdfblah import process, redact, scrub, anonymize, merge, apply_rules

process("in.pdf", "out.pdf", "999.00", "42.00", scope="all", ci=True)
process("in.pdf", "out.pdf", r"\d{4}-\d{4}", "REDACTED", scope="all", regex=True)
redact("in.pdf", "out.pdf", "Account 12345")
scrub("in.pdf", "out.pdf", types=["email", "credit_card"])
anonymize("in.pdf", "out.pdf", names=["Alison Cohen"], seed=7)
merge("template.pdf", [{"name": "Alice"}], "./out")
```

Each call returns a report dict (`ok`, `count`, `refused`, `reason`, ...).

## What it does not do

Scanned PDFs (image only, no text layer) can't be edited directly; run
`pdfblah ocr` first to add a real text layer. Fonts that can't be reproduced are
refused rather than rendered wrong; the app can substitute a similar standard font,
but only when you ask, and the report records it.

## Guides

Step by step, with pictures, at [pdfblah.com/guides](https://pdfblah.com/guides/):

- [Find and replace text in a PDF](https://pdfblah.com/guides/find-and-replace-text-in-a-pdf)
- [Redact a PDF (really remove the text)](https://pdfblah.com/guides/redact-a-pdf)
- [Remove personal data (PII) from a PDF](https://pdfblah.com/guides/remove-personal-data-from-a-pdf)
- [View, strip, or edit PDF metadata](https://pdfblah.com/guides/edit-pdf-metadata)
- [Bulk find and replace in a PDF](https://pdfblah.com/guides/bulk-find-and-replace-in-a-pdf)
- [Run pdfblah on your own machine](https://pdfblah.com/guides/run-pdfblah-on-your-own-machine)

## Two ways to use it

- **On your machine** (free, this package): the command line, or the local app in
  your browser with `pdfblah gui`. Nothing is uploaded.
- **Online** at **[pdfblah.com](https://pdfblah.com)**: nothing to install, handy
  for a quick one-off. Upload, preview free, download.

## License

MIT, (c) 2026 Kuvop LLC.
