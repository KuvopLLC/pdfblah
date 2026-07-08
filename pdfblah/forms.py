"""Work with PDF form fields (AcroForm): list what's there, fill values in, and flatten
so the values become part of the page. Via pikepdf."""
import pikepdf

_FT = {"/Tx": "text", "/Btn": "button", "/Ch": "choice", "/Sig": "signature"}


def _fields(pdf):
    af = pdf.Root.get("/AcroForm")
    if af is None or "/Fields" not in af:
        return []
    return list(af.Fields)


def list_fields(input_path):
    """Return the form fields as {name, type, value}."""
    out = []
    with pikepdf.open(input_path) as pdf:
        for f in _fields(pdf):
            name = str(f.T) if "/T" in f else ""
            ft = _FT.get(str(f.get("/FT")), str(f.get("/FT")) or "?")
            val = f.get("/V")
            out.append({"name": name, "type": ft, "value": "" if val is None else str(val)})
    return {"ok": True, "fields": out, "count": len(out)}


def fill(input_path, output_path, data, flatten=False):
    """Set field values from `data` (a {name: value} dict), bake their appearance, and
    optionally flatten (values become static, the form is no longer interactive)."""
    data = {str(k): v for k, v in (data or {}).items()}
    filled = []
    with pikepdf.open(input_path) as pdf:
        fields = _fields(pdf)
        for f in fields:
            name = str(f.T) if "/T" in f else ""
            if name not in data:
                continue
            v = data[name]
            ft = str(f.get("/FT"))
            if ft == "/Btn":
                on = pikepdf.Name("/Yes") if v not in (False, 0, "", "Off", "off") else pikepdf.Name("/Off")
                f.V = on
                f.AS = on
            else:
                f.V = pikepdf.String(str(v))
            filled.append(name)
        try:
            pdf.generate_appearance_streams()
        except Exception:
            if "/AcroForm" in pdf.Root:
                pdf.Root.AcroForm.NeedAppearances = True
        if flatten and "/AcroForm" in pdf.Root:
            # appearances are baked into the widget annotations; dropping AcroForm makes
            # the values static (viewers still render the annotation appearance streams)
            del pdf.Root.AcroForm
        pdf.save(output_path)
    return {"ok": True, "filled": filled, "count": len(filled),
            "flattened": bool(flatten), "output": output_path}
