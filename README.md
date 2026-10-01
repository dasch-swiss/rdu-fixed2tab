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
that was not parsed. Input line count always equals table rows plus rejected
lines, so nothing can disappear unnoticed.

The table has **no header row** by default: later steps often address rows by
position, and an extra first row would shift every offset by one without
anything visibly breaking. The column names are always in the report; pass
`--header plain` to write them as the first row as well.

The rejected file is itself a TSV with three columns: the input line number, the
rejection reason (`length`, `control-char`, `preamble-identity` or `rule-line`),
and the line verbatim.

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

Install the package from conda-forge into a new environment:

```console
$ conda create -n fixed2tab -c conda-forge fixed2tab
$ conda activate fixed2tab
$ fixed2tab --version
```

With pixi, run `pixi add fixed2tab` in a project that uses the conda-forge
channel.

### Development

An editable install is the fastest way to work on the code:

```console
$ python -m venv .venv && .venv/bin/pip install -e ".[dev]"
$ .venv/bin/fixed2tab --help
$ .venv/bin/python -m pytest
```

To test the conda package itself, build it locally from `recipe/recipe.yaml`
with [rattler-build](https://rattler-build.prefix.dev/):

```console
$ conda install -n base -c conda-forge rattler-build   # once
$ cd rdu-fixed2tab
$ rattler-build build --recipe recipe
$ conda create -n fixed2tab-local -c ./output -c conda-forge fixed2tab
$ conda activate fixed2tab-local
$ fixed2tab --version
```

**Rebuilding after a code change needs a build-number bump** in
`recipe/recipe.yaml`. conda keys its cache on name-version-build, so
rebuilding unchanged coordinates reinstalls the previous package silently — even
with `--force-reinstall`, and while reporting success. Replace the environment
after each rebuild:

```console
$ conda env remove -n fixed2tab-local
$ conda create -n fixed2tab-local -c ./output -c conda-forge fixed2tab
$ conda list -n fixed2tab-local fixed2tab   # the Build column must end in the new number
```

`fixed2tab --version` does not distinguish builds of the same version.

## Previewing the Galaxy tool

The Galaxy wrapper lives only in
[tools-iuc](https://github.com/galaxyproject/tools-iuc/tree/main/tools/fixed2tab),
at `tools/fixed2tab/`. Before you open a tools-iuc PR, preview the wrapper from
the branch in your fork of tools-iuc.

Install [planemo](https://planemo.readthedocs.io/) once, in its own virtual
environment:

```console
$ uv venv ~/.venvs/planemo
$ uv pip install -p ~/.venvs/planemo/bin/python planemo
```

Then, in your tools-iuc checkout, on the wrapper branch:

```console
$ ~/.venvs/planemo/bin/planemo shed_lint --tools --ensure_metadata --urls --fail_level warn --recursive tools/fixed2tab/
$ ~/.venvs/planemo/bin/planemo test tools/fixed2tab/
$ ~/.venvs/planemo/bin/planemo serve tools/fixed2tab/
```

1. The lint command is the one that tools-iuc CI runs. Every warning fails it.
2. `planemo test` runs the tests in the wrapper and writes `tool_test_output.html`.
3. `planemo serve` starts a local Galaxy with only this tool. Open
   http://127.0.0.1:9090 and select the tool in the tool panel. Upload
   `tools/fixed2tab/test-data/fixedwidth_input.txt`, and do not select
   "convert spaces to tabs". Then run the tool on it.
4. Stop the server with Ctrl+C.

The first run of `planemo test` or `planemo serve` downloads Galaxy and takes
several minutes. Both commands get `fixed2tab` from conda-forge through conda.
tools-iuc CI uses containers instead (`--biocontainers`). Do not use
`--biocontainers` on macOS: Docker Desktop does not share the macOS temporary
directory, and on Apple Silicon Galaxy does not find the image that it builds.

## Releasing a new version

A code change reaches Galaxy through three repositories: this one, the
conda-forge feedstock, and the Galaxy wrapper in
[tools-iuc](https://github.com/galaxyproject/tools-iuc/tree/main/tools/fixed2tab).
Nobody uploads to the Tool Shed by hand. tools-iuc CI does that after a merge.

1. Merge the code change. In the same PR, raise `__version__` in
   `src/fixed2tab/__init__.py` and `context.version` in `recipe/recipe.yaml`.
   CI fails if the two differ.
2. Tag `vX.Y.Z` on `main` and publish a GitHub release for the tag.
3. Merge the version PR on
   [conda-forge/fixed2tab-feedstock](https://github.com/conda-forge/fixed2tab-feedstock).
   The conda-forge bot opens it, usually within a day of the release. If the
   release changes the dependencies or the Python floor, edit the feedstock
   recipe in that PR. Make the same edit in `recipe/recipe.yaml` here.
4. Get a version PR on tools-iuc. The `planemo-autoupdate` bot opens one every
   Monday when conda-forge has a new version. It sets `@TOOL_VERSION@` in
   `tools/fixed2tab/macros.xml`. To be faster, open the same PR yourself: set
   `@TOOL_VERSION@` to the new version and `@VERSION_SUFFIX@` to `0`.
5. If the release changes CLI options or outputs, edit the wrapper's inputs,
   command, tests and help in that PR. The bot changes only the version.
6. When CI is green, comment "please review" on the PR. After one IUC
   approval and the merge, tools-iuc CI publishes the tool to the
   [Galaxy Tool Shed](https://toolshed.g2.bx.psu.edu) as owner `iuc`. The
   container follows within about a day.
7. Ask the usegalaxy.ch admin to install the new revision.

A fix to the wrapper only, with no new fixed2tab release, raises
`@VERSION_SUFFIX@` by one in a tools-iuc PR, then follows steps 6 and 7.

## Status

Version 0.1.0, published on
[conda-forge](https://anaconda.org/conda-forge/fixed2tab). See the [PRD and implementation plan](https://github.com/dasch-swiss/dasch-specs/tree/main/specs/2026-08-03-fixed2tab).

## Licence

MIT — see [LICENSE](LICENSE).
