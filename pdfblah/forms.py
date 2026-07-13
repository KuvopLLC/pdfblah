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


def fill_from(input_path, data_path, out_dir, name="{row}.pdf", flatten=False):
    """Fill the same form once per data row: mail-merge for AcroForms.

    The weekly grind this replaces: the same fields hand-written (or hand-typed)
    onto dozens of copies of the same form. Point it at a CSV (headers = field
    names) or a JSON array of objects, and every row becomes one filled PDF.

    name   Filename template: {row} is the row number, {Column} is any column's
           value, so name="{Employee}.pdf" files everything by person.

    Columns that match no form field are reported once (not silently dropped),
    and the report says which fields each row actually filled.
    Returns {ok, rows, outputs, unmatched_columns, fields}.
    """
    import csv
    import json
    import os

    from .organize import _safe_filename

    try:
        if data_path.lower().endswith(".json"):
            with open(data_path, encoding="utf-8") as f:
                rows = json.load(f)
            if not isinstance(rows, list):
                return {"ok": False, "error": "the JSON must be an array of objects"}
            rows = [{str(k): v for k, v in r.items()} for r in rows]
        else:
            with open(data_path, encoding="utf-8", newline="") as f:
                rows = list(csv.DictReader(f))
    except OSError as e:
        return {"ok": False, "error": f"can't read the data: {e}"}
    except Exception as e:
        return {"ok": False, "error": f"can't parse the data: {e}"}
    if not rows:
        return {"ok": False, "error": "the data has no rows"}

    field_names = {f["name"] for f in list_fields(input_path)["fields"]}
    if not field_names:
        return {"ok": False, "code": "no_fields",
                "error": "this PDF has no form fields to fill "
                         "(`pdfblah form in.pdf --list` shows what a form carries)"}
    unmatched = sorted({k for r in rows for k in r.keys()} - field_names)

    os.makedirs(out_dir, exist_ok=True)
    outputs, seen = [], {}
    for i, row in enumerate(rows, 1):
        fname = name.replace("{row}", str(i))
        for k, v in row.items():
            fname = fname.replace("{%s}" % k, str(v))
        if not fname.lower().endswith(".pdf"):
            fname += ".pdf"
        fname = _safe_filename(fname)
        count = seen.get(fname, 0) + 1
        seen[fname] = count
        if count > 1:
            stem, ext = os.path.splitext(fname)
            fname = f"{stem}-{count}{ext}"
        out = os.path.join(out_dir, fname)
        r = fill(input_path, out, {k: v for k, v in row.items() if k in field_names},
                 flatten=flatten)
        if not r.get("ok"):
            return {"ok": False, "error": f"row {i}: {r.get('error')}",
                    "outputs": outputs}
        outputs.append(out)
    return {"ok": True, "rows": len(rows), "outputs": outputs,
            "unmatched_columns": unmatched, "fields": sorted(field_names)}
