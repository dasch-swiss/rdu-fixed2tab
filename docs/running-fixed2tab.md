# Running `fixed2tab` on a file, step by step

`fixed2tab` converts a fixed-width text file into a tab-separated table. It
finds the column boundaries itself, by locating character positions that are
blank in every record. This guide walks through one conversion from start to
finish, including how to read the report and correct the result.

`fixed2tab --help` is the complete reference for every option.

## 1. Install a local build

The conda-forge package is not yet available, so build it from the
repository. You need [rattler-build](https://rattler-build.prefix.dev/) once:

```console
$ conda install -n base -c conda-forge rattler-build
```

Then, from the repository root:

```console
$ rattler-build build --recipe recipe
$ conda create -n fixed2tab-local -c ./output -c conda-forge fixed2tab
$ conda activate fixed2tab-local
```

**Rebuilding after a code change needs a new build number.** Raise `number:`
in `recipe/recipe.yaml` before each rebuild. conda identifies a package by
name, version and build number, so rebuilding with unchanged numbers silently
reinstalls the previous package. Replace the environment after rebuilding:

```console
$ conda env remove -n fixed2tab-local
$ conda create -n fixed2tab-local -c ./output -c conda-forge fixed2tab
$ conda activate fixed2tab-local
$ conda list fixed2tab       # the Build column must end in the new number
```

`fixed2tab --version` does not distinguish builds of the same version.

## 2. Run the tool with no extra options

All four file arguments are required. Quote paths that contain spaces or
special characters.

```console
$ fixed2tab --input "/path/to/input.txt" \
            --table out.tab --report report.txt --rejected rejected.txt
$ echo $?
```

The tool writes whatever name you pass to `--table`; `.tab` and `.tsv` are the
same tab-separated format.

### Exit status

| Status | Meaning |
|---|---|
| `0` | success |
| `2` | bad parameters |
| `3` | the input cannot be used: it cannot be decoded, contains a TAB inside a record, is empty, or has no records |
| `4` | `--strict` tripped |

A message on stderr accompanies every non-zero status. On success nothing is
written to stderr: warnings go into the report instead.

### Outputs

Every successful run writes all three files:

- **The table.** One row per record, cells separated by TABs. It has no header
  row unless you pass `--header plain`. The column names are always in the
  report.
- **The report.** Shows what was detected and how to correct it (see step 3).
- **The rejected file.** Every input line that was not converted, as a TSV of
  three columns: `line number <TAB> reason <TAB> the line verbatim`. The reason
  is one of `length`, `control-char`, `preamble-identity` or `rule-line`. It
  usually holds only the title and heading lines, and it may be empty.

Table rows plus rejected lines always equal the number of input lines, so
nothing disappears unnoticed.

When the run fails with status 2 or 3, no output files are written. Status 4
is decided after the conversion, so all three files exist then.

## 3. Read the report

Check these sections, in this order:

1. **Reconciliation** must end in `[OK]`.
2. **Input** shows the record width and the line chosen as the header. Check
   that the header really is the column headings, not the title or a data row.
3. **Columns** lists each column with its range, width, blank rate, name, and
   the values from the first three records. Check that the samples look like
   the right data.
4. **Warnings** (at the end, only when there are any) names what to change.
   Each warning starts with a code in brackets.

## 4. Correct and run again, if needed

Add the relevant option to the command from step 2 and run it again. Each run
overwrites the three output files, so repeat steps 2–4 until the report looks
right.

| The report shows | Add |
|---|---|
| `[right-trimmed]` | the command it prints: `--record-width W --short-lines pad --header-line N`, with N the line holding the column headings, or `0` if there are none |
| the wrong line as header | `--header-line N` |
| `[byte-vs-char]`, or a decode error | `--encoding latin-1` |
| every column split or merged the same way | `--min-gutter 1` (split more) or `--min-gutter 3` (merge more) |
| one wrong boundary | an edited `--columns` string (below) |
| columns named `col5` and so on (`[fields-without-heading]`, `[field-unnamed]`) | names in an edited `--columns` string (below) |
| `[under-determined]` | nothing to add, but check the column ranges carefully: the file has very few records |

`--short-lines pad` always needs `--header-line`. Without it the run stops with
status 2.

### Correcting a boundary or a name with `--columns`

1. In the report, find the section "Reproducing or correcting this geometry".
   It holds one line such as:

   ```
   --columns '1-12:DATE,18-27:BEST_TIME,31-37:DELTAT_s'
   ```

   Each element is `start-end:NAME`. Positions are counted from 1, and both
   ends are included.

2. Copy the whole line and change only what is wrong. For example, if
   `BEST_TIME` really ends at position 29:

   ```
   --columns '1-12:DATE,18-29:BEST_TIME,31-37:DELTAT_s'
   ```

   To split a column, replace its element with two ranges; to merge two,
   replace both with one range. Ranges must not overlap. A name you write
   always takes precedence over a derived one.

3. Run again with the edited line added:

   ```console
   $ fixed2tab --input "/path/to/input.txt" \
               --table out.tab --report report.txt --rejected rejected.txt \
               --columns '1-12:DATE,18-29:BEST_TIME,31-37:DELTAT_s'
   ```

4. Check the new report. `[uncovered-positions]` means some characters that
   carry data are in no range and would be dropped; it lists the positions.

The same geometry can be written as widths with `--widths`, which is how a
FORTRAN `FORMAT` states it. These two are equivalent:

```
--widths  '12:DATE,5x,10:BEST_TIME'
--columns '1-12:DATE,18-27:BEST_TIME'
```

A bare `N` takes the next N positions as a column, `N:NAME` does the same and
names it, and `Nx` skips N positions. Pass either `--columns` or `--widths`,
not both.

## Galaxy upload hazard

If the file reaches the tool through a Galaxy upload, the uploader must not
apply "convert spaces to tabs". That destroys the column positions before the
tool sees the data. A TAB inside a record is therefore fatal (status 3).

## Notes for the Galaxy wrapper

- **Error detection by exit code.** The tool writes to stderr only when it
  exits non-zero; every warning goes into the report. The wrapper can
  therefore use `detect_errors="exit_code"`. Galaxy's legacy stdio handling
  treats any stderr output as failure, which is why warnings never go there.
  The exact status values (table above) are fixed and asserted by the test
  suite, although Galaxy only needs to tell zero from non-zero.
- **Reproducible output.** The report contains no input paths, timestamps or
  host names. Galaxy randomises the dataset path per job, so this is what makes
  a rerun on identical input byte-identical, table and report alike.
- **Shared test data.** The fixtures in `test-data/` are meant to be shared
  with the wrapper, so the CLI tests and the Galaxy tests exercise the same
  files. CI keeps every file there under 1 MB, which is the Tool Shed's limit,
  and the directory under 100 KB in total, because every instance that
  installs the tool downloads it. The 6.4 MB golden reference file therefore
  lives outside the repository.
- **Tool version.** The wrapper's `@TOOL_VERSION@` should equal
  `fixed2tab.__version__` in `src/fixed2tab/__init__.py`, the single source of
  the version. A CI check for this is intended once the wrapper exists.
