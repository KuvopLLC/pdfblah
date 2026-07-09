"""pdfblah: real find and replace on the actual text in a PDF.

Rewrites the real text in the content stream (no overlay, no watermark), preserves
all original metadata, auto-detects alignment, and refuses fonts it cannot
reproduce instead of garbling them.
"""
from .engine import (
    process,
    redact,
    apply_rules,
    parse_rules,
    parse_rules_file,
    parse_flags,
    font_safe,
    detect_alignment,
)
from .commands import scrub, anonymize, merge, load_data
from .metadata import read_metadata, edit_metadata, apply_to_file
from .organize import combine, split, select_pages, rotate, crop, parse_ranges
from .extract import render as render_pages, extract_text, extract_images
from .security import protect, unlock, attachments, optimize
from .stamp import watermark, stamp, number, bates
from .forms import list_fields as form_list, fill as form_fill
from .clean import clean as clean_scan
from .compare import compare as compare_pdfs
from .signatures import list_signatures, validate as validate_signatures
from .convert import convert, pdf_to_word, office_to_pdf
from .ocr import ocr

__version__ = "0.9.0"
__all__ = [
    "process", "redact", "apply_rules", "parse_rules", "parse_rules_file",
    "parse_flags", "font_safe", "detect_alignment", "scrub", "anonymize", "merge",
    "load_data", "read_metadata", "edit_metadata", "apply_to_file",
    # page ops
    "combine", "split", "select_pages", "rotate", "crop", "parse_ranges",
    # render / extract
    "render_pages", "extract_text", "extract_images",
    # security
    "protect", "unlock", "attachments", "optimize",
    # marks
    "watermark", "stamp", "number", "bates",
    # forms / compare / signatures
    "clean_scan",
    "form_list", "form_fill", "compare_pdfs", "list_signatures", "validate_signatures",
    # convert / ocr
    "convert", "pdf_to_word", "office_to_pdf", "ocr",
]
