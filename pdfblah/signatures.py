"""Inspect digital signatures: list who signed and when (pikepdf, always available), and
validate that a signature is cryptographically intact (via pyHanko, the optional [sign]
extra). pdfblah reads and checks signatures; it does not create them."""
import pikepdf


def list_signatures(input_path):
    """List signature fields and what they claim: signer name, reason, location, time."""
    sigs = []
    with pikepdf.open(input_path) as pdf:
        af = pdf.Root.get("/AcroForm")
        fields = list(af.Fields) if af is not None and "/Fields" in af else []
        for f in fields:
            if str(f.get("/FT")) != "/Sig" or "/V" not in f:
                continue
            v = f.V
            g = lambda k: (str(v.get(k)) if v.get(k) is not None else None)
            sigs.append({
                "field": str(f.get("/T")) if "/T" in f else None,
                "name": g("/Name"),
                "reason": g("/Reason"),
                "location": g("/Location"),
                "time": g("/M"),
            })
    return {"ok": True, "signatures": sigs, "count": len(sigs)}


def validate(input_path):
    """Validate each signature's integrity. Requires the sign extra. Reports, per signature,
    whether it is intact (bytes unchanged since signing) and cryptographically valid, and
    whether the signer chains to a trusted root (false without a configured trust store)."""
    try:
        from pyhanko.pdf_utils.reader import PdfFileReader
        from pyhanko.sign.validation import validate_pdf_signature
        from pyhanko_certvalidator import ValidationContext
    except ImportError:
        return {"ok": False, "code": "needs_sign",
                "error": 'signature validation needs the sign extra: pip install "pdfblah[sign]"'}

    results = []
    with open(input_path, "rb") as fh:
        reader = PdfFileReader(fh)
        vc = ValidationContext(allow_fetching=False)
        for sig in reader.embedded_signatures:
            try:
                st = validate_pdf_signature(sig, vc)
                results.append({
                    "field": getattr(sig, "field_name", None),
                    "signer": str(st.signer_reported_dn) if getattr(st, "signer_reported_dn", None) else None,
                    "intact": bool(getattr(st, "intact", False)),
                    "valid": bool(getattr(st, "valid", False)),
                    "trusted": bool(getattr(st, "trusted", False)),
                    "coverage": str(getattr(sig, "coverage", "")) or None,
                })
            except Exception as e:  # noqa: BLE001 - report per-signature failures
                results.append({"field": getattr(sig, "field_name", None), "error": str(e)})
    return {"ok": True, "signatures": results, "count": len(results)}
