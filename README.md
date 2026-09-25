# fixed2tab

Convert fixed-width column text into TSV, detecting the column boundaries
automatically.

Legacy FORTRAN and C scientific codes still emit results as fixed-width columns.
Delimiter-based tools cannot read them: multi-token cells such as `4h  6m 22s`
are shattered into three columns, and on rows where a sparse column is blank
every later field shifts left by one. Neither failure raises an error — you get
a table, and it is wrong.

`fixed2tab` finds the boundaries by locating character positions that are blank
in *every* record, so the common case needs no configuration.

```console
$ fixed2tab --input observations.txt \
            --table observations.tsv \
            --report geometry.txt \
            --rejected skipped.txt
```

Every successful run writes three files. The table is the data; the report shows the
detected geometry and how to correct it; the rejected file contains every line
that was not parsed, verbatim and numbered. Input line count always equals table
rows plus rejected lines, so nothing can disappear unnoticed.

## Documentation

```console
$ fixed2tab --help
```

`--help` is the documentation. It covers every parameter with its default and
domain, a worked example, and how to read the geometry report and feed its
`--columns` string back in when detection needs correcting. There is no separate
manual to fall out of date.

## Installation

Requires Python 3.10 or newer and has no other dependencies.

Not yet published to conda-forge or PyPI, so install it from a local build:

```console
$ cd rdu-fixed2tab
$ conda build conda-recipe
$ conda create -n fixed2tab-local -c local fixed2tab
$ conda activate fixed2tab-local
$ fixed2tab --version
```

**Rebuilding after a code change needs a build-number bump** in
`conda-recipe/meta.yaml`. conda keys its cache on name-version-build, so
rebuilding unchanged coordinates reinstalls the previous package silently — even
with `--force-reinstall`, and while reporting success.

For development, an editable install avoids the rebuild entirely:

```console
$ python -m venv .venv && .venv/bin/pip install -e ".[dev]"
$ .venv/bin/fixed2tab --help
$ .venv/bin/python -m pytest
```

## Status

Pre-release. See the [PRD and implementation plan](https://github.com/dasch-swiss/dasch-specs/tree/main/specs/2026-08-03-fixed2tab).

## Licence

MIT — see [LICENSE](LICENSE).
