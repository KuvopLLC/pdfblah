"""Recover a PDF that won't open: truncated downloads, FTP mangling, bad exports.

Most "corrupt" PDFs are structurally damaged (a chopped-off cross-reference table,
a missing trailer) while the page content is still sitting in the file. qpdf, the
library under pikepdf, reconstructs that structure when it can; repair() runs the
recovery, rewrites the file cleanly, and then re-opens its own output to prove the
result is genuinely readable before calling it fixed.

Honest by design: the report says whether recovery had to kick in and what came
back; a file that is encrypted rather than broken gets told to unlock instead; and
a file with nothing recoverable in it is a clear failure, not a zero-page "success".
"""
import os


def repair(input_path, output_path):
    """Rebuild a damaged PDF. Returns
    {ok, pages, recovered (bool: qpdf had to reconstruct), bytes_in, bytes_out,
     output} or {ok: False, code, error}."""
    import pikepdf

    bytes_in = os.path.getsize(input_path) if os.path.exists(input_path) else 0
    try:
        pdf = pikepdf.open(input_path)
    except pikepdf.PasswordError:
        return {"ok": False, "code": "encrypted",
                "error": "this file is password-protected, not broken; "
                         "`pdfblah unlock` opens it with the password you know"}
    except pikepdf.PdfError as e:
        return {"ok": False, "code": "unrecoverable",
                "error": f"nothing recoverable in this file ({str(e)[:150]})"}

    with pdf:
        recovered = _had_warnings(pdf)
        pages = len(pdf.pages)
        if pages == 0:
            return {"ok": False, "code": "unrecoverable",
                    "error": "recovery found no pages to save"}
        try:
            # copy the recovered pages into a FRESH file: re-saving the damaged
            # object can carry its broken trailer along; a new one cannot
            fresh = pikepdf.new()
            for page in pdf.pages:
                fresh.pages.append(page)
            try:
                with fresh.open_metadata(set_pikepdf_as_editor=False) as meta:
                    meta.load_from_docinfo(pdf.docinfo)
            except Exception:
                pass  # metadata may be part of what was lost
            fresh.save(output_path)
            fresh.close()
        except Exception as e:
            return {"ok": False, "code": "write_failed",
                    "error": f"recovered the structure but couldn't rewrite it: {e}"}

    # prove the result opens cleanly before calling it fixed
    try:
        with pikepdf.open(output_path) as check:
            if _had_warnings(check) or len(check.pages) != pages:
                raise ValueError("rewritten file is still unhealthy")
    except Exception as e:
        return {"ok": False, "code": "still_broken",
                "error": f"rewrote the file but it still doesn't open cleanly: {e}"}

    return {"ok": True, "pages": pages, "recovered": recovered,
            "bytes_in": bytes_in, "bytes_out": os.path.getsize(output_path),
            "output": output_path}


def _had_warnings(pdf):
    """Whether qpdf raised recovery warnings while parsing this file."""
    getter = getattr(pdf, "get_warnings", None)
    try:
        return bool(getter()) if getter else False
    except Exception:
        return False
