"""The pipeline: pdfblah's little language, shaped like the one DSL everyone
already loves, the Unix pipe.

    pdfblah do 'tidy | rotate auto | clean | ocr lang=eng+deu | compress target=200kb' \\
               scan.pdf -o clean.pdf

Grammar (all of it):

    pipeline := step ('|' step)*        # newlines work like '|', so a recipe
    step     := verb arg*               # file IS a pipeline, one step per line
    arg      := key=value | key | shorthand

- Every TRANSFORM command (PDF in, PDF out; see surface.py) is a verb.
- key=value becomes the command's --key value; a bare key is a bare --flag.
- A little sugar, kept scarce on purpose:
      rotate auto            rotate 180
      watermark "DRAFT"      protect s3cret       compress 200kb
      dark | sepia | ink navy      (recolor schemes as verbs)
      shrink                       (compress, for people who think in words)
- '# comments' and blank lines are ignored, so pipelines read aloud and save
  as recipe files unchanged: `pdfblah do @discovery.recipe evidence/ -o out/`.

One language, three homes: inline after `do`, saved as a recipe for `batch`
(same parser), and conceptually the workbench's step stack. Pipelines run each
file through temp intermediates; originals are never touched, and `bates
start=auto` numbers across files exactly like batch, because it IS batch.
"""
import re

from .surface import COMMANDS, transforms

# scheme verbs and friendly aliases -> (real verb, injected argv)
SUGAR_VERBS = {
    "dark":   ("recolor", ["--scheme", "dark"]),
    "sepia":  ("recolor", ["--scheme", "sepia"]),
    "ink":    ("recolor", None),          # ink navy -> recolor --scheme ink=navy
    "shrink": ("compress", []),
    "straighten": ("rotate", ["--auto"]),
}

_SIZE = re.compile(r"^\d+(\.\d+)?(kb?|mb?)$", re.IGNORECASE)


def parse(text):
    """Pipeline text -> [(verb, argv)]. Raises ValueError with a position on
    anything a pipeline can't run."""
    import shlex

    steps = []
    lines = []
    for line in text.splitlines():
        lines.extend(line.split("|"))
    for n, chunk in enumerate([c for c in lines], 1):
        chunk = chunk.strip()
        if not chunk or chunk.startswith("#"):
            continue
        try:
            words = shlex.split(chunk)
        except ValueError as e:
            raise ValueError(f"step {n}: {e}")
        verb, args = words[0], words[1:]
        verb, argv = _expand(verb, args, n)
        steps.append((verb, argv))
    if not steps:
        raise ValueError("the pipeline has no steps")
    return steps


def _expand(verb, args, n):
    """One step -> (real verb, argv), applying sugar and key=value mapping."""
    if verb in SUGAR_VERBS:
        real, injected = SUGAR_VERBS[verb]
        if verb == "ink":
            if not args or "=" in args[0]:
                raise ValueError(f"step {n}: ink wants a color, like: ink navy")
            injected, args = ["--scheme", f"ink={args[0]}"], args[1:]
        verb, pre = real, list(injected)
    else:
        pre = []
    if verb not in COMMANDS:
        raise ValueError(f"step {n}: '{verb}' is not a pdfblah command "
                         f"(`pdfblah verbs` lists them)")
    if COMMANDS[verb]["kind"] != "transform":
        raise ValueError(f"step {n}: '{verb}' doesn't turn a PDF into a PDF, so it "
                         f"can't be a pipeline step; run it on the result instead")
    argv = pre
    prev_was_flag = False
    for a in args:
        if a.startswith("-"):
            # explicit --flag syntax passes through verbatim (recipe files and
            # muscle memory both speak it)
            argv.append(a)
            prev_was_flag = True
            continue
        if prev_was_flag:
            argv.append(a)          # the flag's value
            prev_was_flag = False
            continue
        if "=" in a:
            k, v = a.split("=", 1)
            argv += [f"--{k}", v]
        elif verb == "rotate" and a == "auto":
            argv += ["--auto"]
        elif verb == "rotate" and re.fullmatch(r"-?\d+", a):
            argv += ["--degrees", a]
        elif verb == "compress" and _SIZE.match(a):
            argv += ["--target", a]
        elif verb == "watermark" and not a.startswith("-") and "--text" not in argv:
            argv += ["--text", a]
        elif verb == "protect" and not a.startswith("-") and "--password" not in argv:
            argv += ["--password", a]
        else:
            argv += [f"--{a}"]
    return verb, argv


def to_recipe_lines(steps):
    """Steps -> batch recipe lines (the two formats are the same language)."""
    import shlex
    out = []
    for verb, argv in steps:
        quoted = " ".join(shlex.quote(a) for a in argv)
        out.append(f"{verb} {quoted}".strip())
    return out


def run_pipeline(text, inputs, output=None, out_dir=None, dry_run=False):
    """Run a pipeline over one file (-> output) or many (-> out_dir).

    Returns the batch report; single-file runs get {ok, output, steps}."""
    import os
    import shutil
    import tempfile

    from .batch import run_batch

    if text.startswith("@"):
        try:
            with open(text[1:], encoding="utf-8") as f:
                text = f.read()
        except OSError as e:
            return {"ok": False, "error": f"can't read the recipe: {e}"}
    try:
        steps = parse(text)
    except ValueError as e:
        return {"ok": False, "error": str(e)}
    lines = to_recipe_lines(steps)

    single = (len(inputs) == 1 and os.path.isfile(inputs[0]) and output)
    if single:
        with tempfile.TemporaryDirectory(prefix="pdfblah-pipe-") as tmp:
            r = run_batch(lines, inputs, tmp, dry_run=dry_run)
            if dry_run or not r.get("ok"):
                r.setdefault("steps", lines)
                return r
            produced = r["files"][0]["output"]
            shutil.move(produced, output)
        return {"ok": True, "output": output, "steps": lines,
                "pages": None, "files": r["files"]}
    if not out_dir:
        return {"ok": False,
                "error": "several inputs (or a folder) need -o OUT_DIR; "
                         "a single file takes -o OUT.pdf"}
    r = run_batch(lines, inputs, out_dir, dry_run=dry_run)
    r.setdefault("steps", lines)
    return r
