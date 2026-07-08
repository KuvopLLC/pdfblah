"""Protect and slim down PDFs: encrypt with a password and permissions, remove a
password, manage file attachments, and optimize (shrink) the file. All via pikepdf."""
import os

import pikepdf


def protect(input_path, output_path, user_password="", owner_password=None,
            allow_print=True, allow_copy=True, allow_modify=True):
    """Encrypt with AES-256. `user_password` is needed to open; `owner_password` (defaults
    to the user password) governs permissions. The allow_* flags set what a reader may do."""
    owner = owner_password if owner_password is not None else user_password
    if not user_password and not owner:
        return {"ok": False, "error": "set a user or owner password"}
    perms = pikepdf.Permissions(
        extract=allow_copy, accessibility=True,
        modify_annotation=allow_modify, modify_assembly=allow_modify,
        modify_form=allow_modify, modify_other=allow_modify,
        print_lowres=allow_print, print_highres=allow_print,
    )
    try:
        with pikepdf.open(input_path) as pdf:
            pdf.save(output_path, encryption=pikepdf.Encryption(
                user=user_password, owner=owner, R=6, allow=perms))
    except pikepdf.PasswordError:
        return {"ok": False, "error": "that PDF is already encrypted; unlock it first"}
    return {"ok": True, "output": output_path,
            "allow": {"print": allow_print, "copy": allow_copy, "modify": allow_modify}}


def unlock(input_path, output_path, password=""):
    """Remove encryption, writing an unprotected copy. Needs the current password."""
    try:
        with pikepdf.open(input_path, password=password) as pdf:
            was = bool(pdf.is_encrypted)
            pdf.save(output_path)
    except pikepdf.PasswordError:
        return {"ok": False, "error": "wrong password"}
    return {"ok": True, "was_encrypted": was, "output": output_path}


def attachments(input_path, output_path=None, add=None, extract_to=None, remove=None):
    """List, add, extract, or remove embedded file attachments. With no action, just lists
    them. `add` is a list of file paths; `remove` a list of names; `extract_to` a directory."""
    added, removed, extracted = [], [], []
    with pikepdf.open(input_path) as pdf:
        names = list(pdf.attachments)
        if extract_to:
            os.makedirs(extract_to, exist_ok=True)
            for name in names:
                data = pdf.attachments[name].get_file().read_bytes()
                dest = os.path.join(extract_to, os.path.basename(name))
                with open(dest, "wb") as f:
                    f.write(data)
                extracted.append(dest)
        for name in (remove or []):
            if name in pdf.attachments:
                del pdf.attachments[name]
                removed.append(name)
        for path in (add or []):
            key = os.path.basename(path)
            pdf.attachments[key] = pikepdf.AttachedFileSpec.from_filepath(pdf, path)
            added.append(key)
        if output_path and (added or removed):
            pdf.save(output_path)
        final = list(pdf.attachments)
    return {"ok": True, "attachments": final, "added": added,
            "removed": removed, "extracted": extracted, "output": output_path}


def optimize(input_path, output_path, downsample_dpi=None, jpeg_quality=None):
    """Shrink the file. Always does lossless structural compression (object streams and
    recompressed streams). With downsample_dpi or jpeg_quality it also recompresses large
    embedded raster images (lossy). Returns the before/after sizes."""
    before = os.path.getsize(input_path)
    with pikepdf.open(input_path) as pdf:
        recompressed = 0
        if downsample_dpi or jpeg_quality:
            recompressed = _recompress_images(pdf, downsample_dpi, jpeg_quality)
        pdf.save(output_path, compress_streams=True, recompress_flate=True,
                 object_stream_mode=pikepdf.ObjectStreamMode.generate)
    after = os.path.getsize(output_path)
    return {"ok": True, "before": before, "after": after,
            "saved": before - after, "images_recompressed": recompressed,
            "output": output_path}


def _recompress_images(pdf, downsample_dpi, jpeg_quality):
    """Best-effort: re-encode large raster image XObjects as JPEG, optionally downsampled.
    Skips anything it can't safely handle."""
    from io import BytesIO

    from PIL import Image

    quality = int(jpeg_quality or 75)
    count = 0
    seen = set()
    for page in pdf.pages:
        for name, raw in (getattr(page, "images", {}) or {}).items():
            if raw.objgen in seen:
                continue
            seen.add(raw.objgen)
            try:
                pil = pikepdf.PdfImage(raw).as_pil_image().convert("RGB")
            except Exception:
                continue
            if downsample_dpi:
                # cap the longest side so a full-page image is about downsample_dpi
                cap = int(downsample_dpi * 11)  # ~ letter height in inches
                if max(pil.size) > cap:
                    scale = cap / max(pil.size)
                    pil = pil.resize((max(1, int(pil.width * scale)),
                                      max(1, int(pil.height * scale))))
            buf = BytesIO()
            pil.save(buf, "JPEG", quality=quality, optimize=True)
            data = buf.getvalue()
            raw.write(data, filter=pikepdf.Name.DCTDecode)
            raw.Width, raw.Height = pil.width, pil.height
            raw.ColorSpace = pikepdf.Name.DeviceRGB
            raw.BitsPerComponent = 8
            count += 1
    return count
