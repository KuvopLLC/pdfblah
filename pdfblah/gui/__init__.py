"""pdfblah desktop app: the hosted tool, running locally and free, from the shared code
base. `from pdfblah.gui import launch; launch()` or `pdfblah gui`."""
from .app import launch

__all__ = ["launch"]
