"""Tests for the pipeline DSL, the surface registry, help, and completions."""
import pytest
from reportlab.pdfgen import canvas

import pdfblah as pb
from pdfblah.pipeline import parse, run_pipeline, to_recipe_lines


def _pdf(path, texts=("hello one", "hello two")):
    c = canvas.Canvas(str(path), pagesize=(400, 300))
    for t in texts:
        c.setFont("Helvetica", 12)
        c.drawString(30, 150, t)
        c.showPage()
    c.save()
    return str(path)


def _text(path):
    import pdfplumber
    with pdfplumber.open(str(path)) as pdf:
        return "\n".join(p.extract_text() or "" for p in pdf.pages)


# ---------- grammar ----------
def test_parse_pipes_and_newlines_are_the_same():
    a = parse("tidy | watermark DRAFT")
    b = parse("tidy\nwatermark DRAFT")
    assert a == b == [("tidy", []), ("watermark", ["--text", "DRAFT"])]


def test_parse_key_value_and_bare_flags():
    (verb, argv), = parse("compress target=200kb grayscale")
    assert verb == "compress" and argv == ["--target", "200kb", "--grayscale"]


def test_parse_comments_and_blanks():
    steps = parse("# clean up first\n\ntidy\n# then mark\nwatermark X\n")
    assert [v for v, _ in steps] == ["tidy", "watermark"]


def test_sugar():
    assert parse("dark") == [("recolor", ["--scheme", "dark"])]
    assert parse("sepia") == [("recolor", ["--scheme", "sepia"])]
    assert parse("ink navy") == [("recolor", ["--scheme", "ink=navy"])]
    assert parse("shrink 1.5mb") == [("compress", ["--target", "1.5mb"])]
    assert parse("straighten") == [("rotate", ["--auto"])]
    assert parse("rotate auto") == [("rotate", ["--auto"])]
    assert parse("rotate 180") == [("rotate", ["--degrees", "180"])]
    assert parse('watermark "TOP SECRET"') == [("watermark", ["--text", "TOP SECRET"])]
    assert parse("protect s3cret") == [("protect", ["--password", "s3cret"])]
    assert parse("scrub types=email") == [("scrub", ["--types", "email"])]


def test_parse_errors_teach():
    with pytest.raises(ValueError, match="not a pdfblah command"):
        parse("frobnicate hard")
    with pytest.raises(ValueError, match="can't be a pipeline step"):
        parse("tidy | find needle")
    with pytest.raises(ValueError, match="ink wants a color"):
        parse("ink")
    with pytest.raises(ValueError, match="no steps"):
        parse("# only a comment\n")


def test_to_recipe_lines_round_trips():
    lines = to_recipe_lines(parse('watermark "TOP SECRET" | compress 200kb'))
    assert lines == ["watermark --text 'TOP SECRET'", "compress --target 200kb"]
    assert parse("\n".join(lines)) == parse('watermark "TOP SECRET" | compress 200kb')


# ---------- execution ----------
def test_run_pipeline_single_file(tmp_path):
    src = _pdf(tmp_path / "in.pdf")
    out = tmp_path / "out.pdf"
    r = run_pipeline("replace find=hello replace=goodbye scope=all | watermark DRAFT",
                     [src], output=str(out))
    assert r["ok"], r
    assert out.exists()
    t = _text(out)
    assert "goodbye" in t and "hello" not in t
    # the diagonal watermark extracts letter-by-letter; check the letters landed
    assert set("DRAFT") <= set(t)


def test_run_pipeline_folder_with_cross_file_bates(tmp_path):
    d = tmp_path / "in"
    d.mkdir()
    _pdf(d / "a.pdf")
    _pdf(d / "b.pdf", texts=("x",))
    out = tmp_path / "out"
    r = run_pipeline("bates prefix=EXH- digits=4 start=auto", [str(d)],
                     out_dir=str(out))
    assert r["ok"] and r["processed"] == 2
    assert "EXH-0003" in _text(out / "b.pdf")


def test_run_pipeline_at_recipe_file(tmp_path):
    src = _pdf(tmp_path / "in.pdf")
    recipe = tmp_path / "steps.recipe"
    recipe.write_text("# a comment\nwatermark RECIPE\n")
    out = tmp_path / "out.pdf"
    r = run_pipeline(f"@{recipe}", [src], output=str(out))
    assert r["ok"] and set("RECIPE") <= set(_text(out))


def test_run_pipeline_dry_run_and_errors(tmp_path):
    src = _pdf(tmp_path / "in.pdf")
    r = run_pipeline("tidy", [src], output=str(tmp_path / "o.pdf"), dry_run=True)
    assert r["ok"] and not (tmp_path / "o.pdf").exists()
    r = run_pipeline("frobnicate", [src], output=str(tmp_path / "o.pdf"))
    assert not r["ok"] and "frobnicate" in r["error"]
    r = run_pipeline("tidy", [str(tmp_path)], output=None, out_dir=None)
    assert not r["ok"] and "OUT_DIR" in r["error"]


# ---------- surface registry ----------
def test_every_command_is_registered():
    from pdfblah.cli import HANDLERS
    from pdfblah.cli_tools import TOOL_HANDLERS
    from pdfblah.surface import COMMANDS
    real = (set(HANDLERS) | set(TOOL_HANDLERS) | {"replace"}) - \
        {"gui", "help", "verbs", "completions"}
    assert real == set(COMMANDS), real ^ set(COMMANDS)
    for name, info in COMMANDS.items():
        assert info["summary"] and info["example"], name


def test_every_transform_is_batch_runnable():
    from pdfblah.batch import _FLAGGED, _POSITIONAL
    from pdfblah.surface import transforms
    runnable = _POSITIONAL | _FLAGGED | {"replace"}
    missing = set(transforms()) - runnable
    assert not missing, f"transforms the batch runner can't drive: {missing}"


# ---------- help / verbs / completions ----------
def test_verbs_and_help(capsys):
    from pdfblah.cli import main
    assert main(["verbs"]) == 0
    out = capsys.readouterr().out
    assert "| tidy" in out and "pipeline step" in out
    assert main(["help"]) == 0
    out = capsys.readouterr().out
    assert "Swiss army knife" in out and "pdfblah do" in out
    assert main(["help", "compress"]) == 0
    out = capsys.readouterr().out
    assert "--target" in out and "example:" in out and "related:" in out


def test_completions_scripts(capsys):
    from pdfblah.cli import main
    assert main(["completions", "zsh"]) == 0
    z = capsys.readouterr().out
    assert z.startswith("#compdef pdfblah")
    assert "tidy) _arguments" in z and "'--dry-run'" in z
    assert main(["completions", "bash"]) == 0
    b = capsys.readouterr().out
    assert "complete -F _pdfblah pdfblah" in b and "--target" in b


def test_do_cli(tmp_path, capsys):
    from pdfblah.cli import main
    src = _pdf(tmp_path / "in.pdf")
    out = tmp_path / "out.pdf"
    assert main(["do", "watermark DSL | tidy keep-blank",
                 src, "-o", str(out)]) == 0
    printed = capsys.readouterr().out
    assert "watermark --text DSL" in printed and str(out) in printed
    assert set("DSL") <= set(_text(out))
