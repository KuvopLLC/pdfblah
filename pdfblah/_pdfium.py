"""One process-wide lock for every pypdfium2 call. PDFium is not thread-safe: two
renders on different threads can segfault the whole process (observed in both the
local gui server and the hosted container before their call sites were serialized).
Taking the lock here, inside the package, protects library users too."""
import threading

PDFIUM_LOCK = threading.Lock()
