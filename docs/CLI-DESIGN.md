# The pdfblah CLI: design notes and how to extend it

This is the file to read before adding or renaming a command. It records the
decisions from the July 2026 multi-pass surface scan, so the CLI keeps feeling
like one tool as it grows.

## The shape of the surface

Two layers, deliberately:

1. **Commands** (`pdfblah tidy in.pdf -o out.pdf`) — one job each, argparse,
   stable forever once shipped. Guides and scripts point at these; renames are
   breaking changes and need overwhelming cause.
2. **The pipeline** (`pdfblah do 'tidy | rotate auto | compress target=200kb'`)
   — the composition layer, modeled on the one DSL everyone already loves, the
   Unix pipe. It also *hides* the historical inconsistencies of layer 1 (three
   output conventions grew over time; the pipeline has exactly one).

A recipe file is a pipeline with newlines instead of `|`. Same parser
(`pipeline.parse`), same sugar, same validation. Never fork them.

## The registry is the source of truth

`surface.py` maps every command to a group, a kind, a one-line summary, and a
worked example. `pdfblah help`, `pdfblah verbs`, shell completions, and the
pipeline's validation all read it. **A command that isn't registered doesn't
exist** — tests enforce this. Flags are never duplicated into the registry;
completions introspect the real parsers so they can't drift.

Kinds, and what they license:
- `transform` — PDF in, PDF out. Automatically a pipeline verb. Must be wired
  into `batch._POSITIONAL` (takes `input output`) or `batch._FLAGGED`
  (takes `input -o output`) so the runner can drive it.
- `inspect` — reads and reports (find, links, compare). Never a pipeline step.
- `produce` — PDF in, other files out (render, extract, split).
- `util` — everything else (doctor, combine, batch, do).

## Grammar (complete)

    pipeline := step ('|' step)*          # newline == '|'
    step     := verb arg*
    arg      := key=value | key | shorthand
    # comments and blank lines ignored

`key=value` → `--key value`. Bare `key` → `--key`. First `=` splits, so
`scheme=ink=navy` works.

## Sugar policy

Sugar exists to make pipelines read aloud, and scarcity is what keeps it
readable. Current, complete list (`pipeline.SUGAR_VERBS` + per-verb shorthands):

    dark | sepia | ink navy        recolor schemes as words
    shrink                          compress
    straighten                      rotate --auto
    rotate auto / rotate 180        the two things rotate means
    compress 200kb                  a bare size is the target
    watermark "DRAFT"               a bare word is the text
    protect s3cret                  a bare word is the password

Rules for new sugar: it must read like English in a pipeline, must map to ONE
canonical form, and must be documented in this table, the pipeline docstring,
and the guide in the same change. When in doubt, no sugar.

## Conventions for new commands

- Verb-first names when the command acts (`repair`, `sanitize`); noun names
  only for surfaces over a document part (`pages`, `links`, `form`).
- New transforms take `input -o output` (the `_FLAGGED` style). The positional
  `input output` style is legacy; don't add to it.
- `--dry-run` on anything destructive or batch-shaped; the dry run prints the
  full plan.
- Reports are dicts with `ok`, honest failure `code`s, and human messages that
  name the fix (`pdfblah ocr --get-lang deu`), never stack traces.
- Every command ships with: tests, a registry entry, and (if user-facing
  enough) a guide. `pdfblah help <cmd>` shows the registry example, so write
  one that teaches.

## Expansion hints (deliberately unbuilt, good next moves)

- `pdfblah do` reading stdin/stdout streams (`-` as input/output) for real
  Unix piping between processes.
- Named pipelines: `pdfblah do @~/.config/pdfblah/recipes/<name>` with a
  `pdfblah recipes` lister (the workbench already stores recipes; unify).
- A `--explain` flag on `do`: print what each step will do to THIS file
  (pages, images, fonts) before running.
- Fish-shell completions (same introspection, third emitter).
- `pdfblah undo`: keep the last N originals in a local stash.
