"""Locate the shared web assets (the tool UI: gate.mjs, tool.js, tool.css) that ship
inside the wheel. The desktop server serves them and the hosted build vendors them, so
both consume ONE pinned copy and never drift."""
from importlib.resources import files
from pathlib import Path

#: names of the files that make up the shared tool UI
WEB_FILES = ("gate.mjs", "tool.js", "tool.css", "sample-rules.txt")


def web_dir() -> Path:
    """Filesystem path to the packaged ``pdfblah/web`` directory."""
    return Path(str(files("pdfblah").joinpath("web")))


def read_asset(name: str) -> bytes:
    """Return the bytes of a named shared web asset (e.g. ``"tool.js"``)."""
    return files("pdfblah").joinpath("web", name).read_bytes()
