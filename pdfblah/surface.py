"""The one map of pdfblah's command surface.

Help, `pdfblah verbs`, shell completions, and the pipeline DSL all read this
registry, so the surface can't drift from its own documentation. Every command
carries: a GROUP (how humans browse), a KIND (what the machine may do with it:
"transform" steps are PDF-in/PDF-out and may appear in pipelines), a one-line
summary in guide voice, and worked examples.

To add a command: write the handler as usual, then register it here with a
summary and at least one example. If it turns a PDF into a PDF, mark it a
transform and it becomes a pipeline verb for free. Sugar (dark, ink, shrink...)
lives in pipeline.py; keep it scarce, memorable, and documented.
"""

# kind: "transform" (PDF -> PDF; valid pipeline step) | "inspect" (reads, reports)
#       | "produce" (PDF -> other files) | "util" (everything else)
COMMANDS = {
    # -- edit the text ----------------------------------------------------------
    "replace":   dict(group="Edit text", kind="transform",
                      summary="find and replace the real text, byte-precise",
                      example='pdfblah in.pdf out.pdf --find 999.00 --replace 42.00'),
    "redact":    dict(group="Edit text", kind="transform",
                      summary="remove text for real, then mark the spot",
                      example='pdfblah redact in.pdf out.pdf --find "Account 12345"'),
    "scrub":     dict(group="Edit text", kind="transform",
                      summary="strip PII (emails, cards, IBANs, SSNs, phones)",
                      example='pdfblah scrub in.pdf out.pdf --types email,credit_card'),
    "anonymize": dict(group="Edit text", kind="transform",
                      summary="swap names and identifiers for realistic fakes",
                      example='pdfblah anonymize in.pdf out.pdf --names "Alison Cohen"'),

    # -- pages ------------------------------------------------------------------
    "pages":     dict(group="Pages", kind="transform",
                      summary="keep, drop, reorder, or insert-every-N pages",
                      example='pdfblah pages in.pdf out.pdf --insert notes.pdf --every 2'),
    "rotate":    dict(group="Pages", kind="transform",
                      summary="rotate pages; --auto fixes upside-down/sideways",
                      example='pdfblah rotate in.pdf out.pdf --auto'),
    "crop":      dict(group="Pages", kind="transform",
                      summary="trim margins or crop to a box",
                      example='pdfblah crop in.pdf out.pdf --margins 20,20,20,20'),
    "tidy":      dict(group="Pages", kind="transform",
                      summary="drop blank pages and exact duplicates",
                      example='pdfblah tidy in.pdf -o out.pdf --dry-run'),
    "split":     dict(group="Pages", kind="produce",
                      summary="split by count, ranges, content (--at), or spreads",
                      example='pdfblah split all.pdf -o parts/ --at "Invoice #(\\S+)" --name "{1}.pdf"'),
    "combine":   dict(group="Pages", kind="util",
                      summary="merge PDFs; --toc --tabs builds a binder",
                      example='pdfblah combine *.pdf -o binder.pdf --toc --tabs'),

    # -- scans ------------------------------------------------------------------
    "clean":     dict(group="Scans", kind="transform",
                      summary="pure white paper, crisp ink (made for sheet music)",
                      example='pdfblah clean scan.pdf out.pdf --strength strong'),
    "ocr":       dict(group="Scans", kind="transform",
                      summary="add a searchable text layer; languages are modular",
                      example='pdfblah ocr scan.pdf -o out.pdf --lang eng+deu'),
    "recolor":   dict(group="Scans", kind="transform",
                      summary="dark mode, sepia, or any ink color, baked in",
                      example='pdfblah recolor in.pdf -o dark.pdf --scheme ink=navy'),

    # -- size & health ----------------------------------------------------------
    "compress":  dict(group="Size & health", kind="transform",
                      summary="fit under a hard cap; text stays selectable",
                      example='pdfblah compress in.pdf -o out.pdf --target 200kb'),
    "optimize":  dict(group="Size & health", kind="transform",
                      summary="squeeze structure and downsample images",
                      example='pdfblah optimize in.pdf out.pdf'),
    "repair":    dict(group="Size & health", kind="transform",
                      summary="rebuild a PDF that won't open, verified",
                      example='pdfblah repair broken.pdf -o fixed.pdf'),

    # -- marks ------------------------------------------------------------------
    "watermark": dict(group="Marks", kind="transform",
                      summary="text or image over (or under) the pages",
                      example='pdfblah watermark in.pdf out.pdf --text DRAFT'),
    "stamp":     dict(group="Marks", kind="transform",
                      summary="place an image or PDF stamp",
                      example='pdfblah stamp in.pdf out.pdf --image logo.png --position top-right'),
    "number":    dict(group="Marks", kind="transform",
                      summary='"Page 1 of 10" wherever you want it',
                      example='pdfblah number in.pdf out.pdf --format "Page {n} of {total}"'),
    "bates":     dict(group="Marks", kind="transform",
                      summary="legal Bates numbering (start=auto crosses files in batch)",
                      example='pdfblah bates in.pdf out.pdf --prefix EXH- --digits 4'),

    # -- share & protect ---------------------------------------------------------
    "sanitize":  dict(group="Share & protect", kind="transform",
                      summary="strip metadata, comments, JS, attachments, revisions",
                      example='pdfblah sanitize in.pdf --dry-run'),
    "meta":      dict(group="Share & protect", kind="transform",
                      summary="view, set, or strip document metadata",
                      example='pdfblah meta in.pdf out.pdf --strip'),
    "protect":   dict(group="Share & protect", kind="transform",
                      summary="AES-256 password and permissions",
                      example='pdfblah protect in.pdf out.pdf --password s3cret'),
    "unlock":    dict(group="Share & protect", kind="transform",
                      summary="remove a password you know",
                      example='pdfblah unlock in.pdf out.pdf --password s3cret'),
    "pdfa":      dict(group="Share & protect", kind="transform",
                      summary="convert to PDF/A, the archival format, verified",
                      example='pdfblah pdfa in.pdf -o archive.pdf'),

    # -- find & check -------------------------------------------------------------
    "find":      dict(group="Find & check", kind="inspect",
                      summary="grep for PDFs: folders or trees, file+page+snippet",
                      example='pdfblah find "invoice 4471" ~/docs -r'),
    "links":     dict(group="Find & check", kind="inspect",
                      summary="check every link and bookmark; exit 1 gates a build",
                      example='pdfblah links manual.pdf --offline'),
    "compare":   dict(group="Find & check", kind="inspect",
                      summary="what changed between two PDFs, text or visual",
                      example='pdfblah compare a.pdf b.pdf --visual --out-dir diff/'),
    "signatures": dict(group="Find & check", kind="inspect",
                      summary="read (and validate) digital signatures",
                      example='pdfblah signatures in.pdf --validate'),
    "access":    dict(group="Find & check", kind="inspect",
                      summary="accessibility basics: tags, language, alt text, title",
                      example='pdfblah access report.pdf'),
    "doctor":    dict(group="Find & check", kind="util",
                      summary="check the system tools; --install fetches them",
                      example='pdfblah doctor --install'),

    # -- in & out -----------------------------------------------------------------
    "extract":   dict(group="In & out", kind="produce",
                      summary="text, tables (CSV), or embedded images, out",
                      example='pdfblah extract in.pdf --tables -o out.csv'),
    "render":    dict(group="In & out", kind="produce",
                      summary="pages to PNG/JPG at any dpi",
                      example='pdfblah render in.pdf -o pages/ --dpi 300'),
    "convert":   dict(group="In & out", kind="util",
                      summary="PDF to Word and office formats to PDF",
                      example='pdfblah convert in.pdf -o out.docx'),
    "attachments": dict(group="In & out", kind="util",
                      summary="list, add, extract, or remove embedded files",
                      example='pdfblah attachments in.pdf'),
    "form":      dict(group="In & out", kind="util",
                      summary="list/fill/flatten forms; --fill-from = one PDF per CSV row",
                      example='pdfblah form t.pdf --fill-from rows.csv -o filled/ --name "{Employee}.pdf"'),
    "merge":     dict(group="In & out", kind="util",
                      summary="mail-merge a text template PDF from CSV data",
                      example='pdfblah merge template.pdf data.csv --out out/'),

    # -- automate ------------------------------------------------------------------
    "batch":     dict(group="Automate", kind="util",
                      summary="a recipe (= a pipeline, one step per line) over a folder",
                      example='pdfblah batch discovery.recipe evidence/ -o stamped/'),
    "do":        dict(group="Automate", kind="util",
                      summary="run a pipeline: 'tidy | rotate auto | compress target=200kb'",
                      example="pdfblah do 'tidy | compress target=200kb' in.pdf -o out.pdf"),
}

GROUPS = ["Edit text", "Pages", "Scans", "Size & health", "Marks",
          "Share & protect", "Find & check", "In & out", "Automate"]


def transforms():
    """Verbs valid as pipeline steps (PDF in, PDF out)."""
    return sorted(k for k, v in COMMANDS.items() if v["kind"] == "transform")
