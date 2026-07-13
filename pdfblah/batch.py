"""Run the same sequence of pdfblah steps over a whole folder of PDFs.

A recipe is a plain text file, one step per line, in exactly the words the CLI
already speaks:

    # discovery.recipe — Bates-stamp everything, drop the fax cover, mark it
    bates --prefix EXH- --digits 4 --start auto
    pages --drop 1
    stamp --text CONFIDENTIAL --position top-right

Each input file flows through the steps in order (via temp files; the original is
never touched) and lands in the output directory under its own name. Files are
processed in sorted name order, which matters for the one thing shell loops cannot
do: `--start auto` hands Bates a counter that CONTINUES ACROSS FILES, so dozens of
separate PDFs come out numbered EXH-0001 through EXH-5000 like one exhibit set.

A step that fails stops that file (its partial work is discarded) and the run moves
on; the report names every failure. Deterministic throughout: same folder, same
recipe, same result.
"""
import io
import os
import shlex
import tempfile
from contextlib import redirect_stderr, redirect_stdout

# commands a recipe may use, and how each takes its input/output
_POSITIONAL = {"redact", "scrub", "anonymize", "meta", "pages", "rotate", "crop",
               "protect", "unlock", "watermark", "stamp", "number", "bates",
               "optimize", "clean"}
_FLAGGED = {"tidy", "compress", "ocr"}
ALLOWED = sorted(_POSITIONAL | _FLAGGED | {"replace"})


def parse_recipe(lines):
    """Recipe text -> [(command, [args])]. Raises ValueError with the line number
    on anything the batch runner can't drive."""
    steps = []
    for n, raw in enumerate(lines, 1):
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        try:
            words = shlex.split(line)
        except ValueError as e:
            raise ValueError(f"line {n}: {e}")
        cmd, args = words[0], words[1:]
        if cmd not in ALLOWED:
            raise ValueError(f"line {n}: '{cmd}' is not a batchable step "
                             f"(one of: {', '.join(ALLOWED)})")
        steps.append((cmd, args))
    if not steps:
        raise ValueError("the recipe has no steps")
    return steps


def run_batch(recipe, inputs, out_dir, dry_run=False):
    """Apply a recipe to every input PDF.

    recipe   Path to a recipe file, or a list of recipe lines.
    inputs   PDF paths and/or directories (a directory means its *.pdf, sorted).
    out_dir  Where the finished files go, under their original names.
    dry_run  Report the plan (files, steps, numbering) without writing anything.

    Returns {ok, steps, files: [{input, output, ok, error?}], processed, failed,
             counter_end?}.
    """
    from .cli import main as cli_main

    if isinstance(recipe, str):
        try:
            with open(recipe, encoding="utf-8") as f:
                lines = f.read().splitlines()
        except OSError as e:
            return {"ok": False, "error": f"can't read the recipe: {e}"}
    else:
        lines = list(recipe)
    try:
        steps = parse_recipe(lines)
    except ValueError as e:
        return {"ok": False, "error": str(e)}

    files = _collect(inputs)
    if not files:
        return {"ok": False, "error": "no PDFs found in the given inputs"}

    plan = {"ok": True, "steps": [f"{c} {' '.join(a)}".strip() for c, a in steps],
            "files": [], "processed": 0, "failed": 0}
    counter = _auto_start(steps)
    outputs_seen = {}
    if not dry_run:
        os.makedirs(out_dir, exist_ok=True)

    for src in files:
        base = os.path.basename(src)
        if base in outputs_seen:
            outputs_seen[base] += 1
            stem, ext = os.path.splitext(base)
            base = f"{stem}-{outputs_seen[os.path.basename(src)]}{ext}"
        else:
            outputs_seen[base] = 1
        dest = os.path.join(out_dir, base)
        entry = {"input": src, "output": dest, "ok": True}
        if counter is not None:
            entry["counter"] = counter
        if dry_run:
            if counter is not None:
                counter += _page_count(src)
            plan["files"].append(entry)
            continue
        err = _run_one(src, dest, steps, counter, cli_main)
        if err:
            entry.update(ok=False, error=err)
            plan["failed"] += 1
        else:
            plan["processed"] += 1
            if counter is not None:
                counter += _page_count(src)
        plan["files"].append(entry)

    if counter is not None:
        plan["counter_end"] = counter
    plan["ok"] = plan["failed"] == 0 if not dry_run else True
    if not dry_run and plan["failed"]:
        plan["error"] = f"{plan['failed']} file(s) failed; see the report"
    return plan


def _run_one(src, dest, steps, counter, cli_main):
    """One file through all steps. Returns an error string, or None on success."""
    with tempfile.TemporaryDirectory(prefix="pdfblah-batch-") as tmp:
        cur = src
        for i, (cmd, args) in enumerate(steps):
            nxt = os.path.join(tmp, f"step-{i}.pdf")
            fixed, prev = [], None
            for a in args:
                fixed.append(str(counter) if (prev == "--start" and a == "auto"
                                              and counter is not None) else a)
                prev = a
            args = fixed
            if cmd == "replace":
                argv = [cur, nxt] + args
            elif cmd in _POSITIONAL:
                argv = [cmd, cur, nxt] + args
            else:
                argv = [cmd, cur, "-o", nxt] + args
            buf = io.StringIO()
            try:
                with redirect_stdout(buf), redirect_stderr(buf):
                    rc = cli_main(argv)
            except SystemExit as e:
                rc = e.code if isinstance(e.code, int) else 1
            except Exception as e:
                return f"step {i + 1} ({cmd}): {e}"
            if rc:
                said = " ".join(buf.getvalue().split())[-200:]
                return f"step {i + 1} ({cmd}) failed: {said or f'exit {rc}'}"
            cur = nxt
        try:
            import shutil
            shutil.copyfile(cur, dest)
        except OSError as e:
            return f"couldn't write the output: {e}"
    return None


def _auto_start(steps):
    """The starting counter, or None when no step uses --start auto."""
    for cmd, args in steps:
        for flag, val in zip(args, args[1:]):
            if flag == "--start" and val == "auto":
                return 1
    return None


def _page_count(path):
    import pikepdf
    try:
        with pikepdf.open(path) as pdf:
            return len(pdf.pages)
    except Exception:
        return 0


def _collect(inputs):
    out = []
    for p in inputs:
        if os.path.isdir(p):
            out.extend(os.path.join(p, n) for n in sorted(os.listdir(p))
                       if n.lower().endswith(".pdf"))
        elif p.lower().endswith(".pdf"):
            out.append(p)
    return out
