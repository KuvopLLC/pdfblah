"""pdfblah.app: the shared PDF app (analyze a PDF, apply action-rules, render a
preview). Used by BOTH the hosted service and the desktop app so they never drift.
Pure and commercial-free; hosting/payment/watermark-until-paid live in the host."""
from .limits import (
    Limits, HOSTED, LOCAL, validate_upload, validate_rules, META_FIELDS, DETECTOR_TYPES,
)
from .analyze import analyze_pdf
from .actions import apply_actions
from .render import render_preview
from .assets import web_dir, read_asset, WEB_FILES

__all__ = [
    "Limits", "HOSTED", "LOCAL", "validate_upload", "validate_rules",
    "META_FIELDS", "DETECTOR_TYPES", "analyze_pdf", "apply_actions", "render_preview",
    "web_dir", "read_asset", "WEB_FILES",
]
