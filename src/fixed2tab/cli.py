"""Command-line interface.

The only module that maps errors to exit statuses or touches ``sys``; everything
below it raises instead, which keeps the parser importable and testable.
"""

from __future__ import annotations

import argparse
import codecs
import re
import sys
from pathlib import Path

from fixed2tab import __version__
from fixed2tab.carve import HeaderMode, write_outputs
from fixed2tab.detect import (
    LineSource,
    ShortLinePolicy,
    blank_runs,
    classify_input,
    derive_fields,
    profile_lengths,
)
from fixed2tab.header import name_fields
from fixed2tab.model import (
    Diagnostic,
    ExitCode,
    Field,
    Fixed2TabError,
    Geometry,
    InputError,
    RejectReason,
    StrictViolation,
    UsageError,
)
from fixed2tab.report import render_report

__all__ = ["build_parser", "main", "parse_columns", "parse_widths"]

COLUMN_RE = re.compile(r"^(\d+)-(\d+)(?::([A-Za-z0-9_]+))?$")
WIDTH_RE = re.compile(r"^(\d*)([xX])$|^(\d+)(?::([A-Za-z0-9_]+))?$")

EPILOG = """\
examples
--------
  Convert a file, letting the tool find the columns:

    fixed2tab --input observations.txt --table out.tsv \\
              --report geometry.txt --rejected skipped.txt

  Read geometry.txt. If a boundary is wrong, copy the --columns line it prints,
  edit the numbers, and run again with it. That loop replaces the live preview a
  spreadsheet tool would give you:

    fixed2tab --input observations.txt --table out.tsv \\
              --report geometry.txt --rejected skipped.txt \\
              --columns '1-12:DATE,18-27:BEST_TIME,31-37:DELTAT_s'

when a column comes out named colN
----------------------------------
  Some files put one heading above several columns — "Jewish Date Scheme" over a
  year, a month and a day, say. The heading overlaps only one of them, so the
  others are named col5, col7 and so on, and a warning says which. The data is
  correct; only the names are unhelpful.

  Repeated headings are suffixed with the column index instead: two "Hsun"
  columns become Hsun and Hsun_7.

  Fix either by naming the columns yourself. Copy the --columns line from the
  report and add or edit the names after each range:

    --columns '54-57:Jewish_year,63-74:Jewish_month,80-81:Jewish_day'

  A name you write always wins over one derived from the heading.

outputs
-------
  All three are written on every successful run, so that

      table rows + rejected lines == input lines

  always holds and nothing can disappear unnoticed. The rejected file is often
  empty; that is a result, not a failure.

positions
---------
  Every character position is 1-based and inclusive, in --columns, in the report
  and in this help.

migrating a workflow that used a hand-made conversion
-----------------------------------------------------
  fixed2tab writes no preamble rows. If your previous file carried a header or a
  carved title above the data, and a later step skips a fixed number of lines,
  reduce that number by however many preamble rows the old file had — otherwise
  the same step now silently selects a different slice of your data.

a Galaxy upload hazard
----------------------
  Do not let Galaxy's uploader apply "convert spaces to tabs" to a fixed-width
  file. It destroys the column positions before this tool ever sees the data. A
  TAB inside a record is treated as fatal here for that reason.

exit status
-----------
  0 success   2 bad parameters   3 unusable input   4 --strict tripped
"""


def parse_columns(spec: str, record_width: int) -> tuple[Field, ...]:
    """Parse a ``--columns`` string into fields, validating as we go.

    Rejects malformed elements, reversed ranges, positions past the record width
    and overlapping ranges. Overlap is an error rather than a warning because
    two columns sharing characters is almost always a typo in a hand-edited
    spec, and silently duplicating data would be worse than stopping.
    """
    fields: list[Field] = []
    for element in (part.strip() for part in spec.split(",")):
        if not element:
            raise UsageError(
                f"--columns has an empty element in {spec!r}; expected "
                "START-END[:NAME] separated by commas"
            )
        match = COLUMN_RE.match(element)
        if not match:
            raise UsageError(
                f"--columns element {element!r} is malformed; expected "
                "START-END[:NAME] with 1-based inclusive positions, "
                "e.g. 1-12:DATE"
            )
        start, end, name = int(match[1]), int(match[2]), match[3]
        if start < 1:
            raise UsageError(f"--columns element {element!r} starts before position 1")
        if end < start:
            raise UsageError(
                f"--columns element {element!r} ends before it starts; "
                "positions are inclusive and must ascend"
            )
        if end > record_width:
            raise UsageError(
                f"--columns element {element!r} extends past the record width "
                f"of {record_width}"
            )
        fields.append(Field(start, end, name))

    if not fields:
        raise UsageError("--columns is empty")

    ordered = sorted(fields, key=lambda f: f.start)
    for left, right in zip(ordered, ordered[1:]):
        if right.start <= left.end:
            raise UsageError(
                f"--columns ranges {left.start}-{left.end} and "
                f"{right.start}-{right.end} overlap"
            )
    return tuple(fields)


def parse_widths(spec: str, record_width: int) -> tuple[Field, ...]:
    """Parse a ``--widths`` string into fields, consuming positions left to right.

    Each element is either a field width, or a skip written ``Nx`` — which is
    what makes a FORTRAN FORMAT transcribe directly:

        FORMAT(2X, I5, 2X, F8.3)   ->   --widths '2x,5,2x,8'

    Widths are how these files were written, so they are usually easier to get
    right than absolute positions: a format statement gives them to you, whereas
    ranges have to be derived by arithmetic. Internally they become the same
    ranges as ``--columns``, so the round-trip guarantee is unaffected.
    """
    fields: list[Field] = []
    cursor = 1
    for element in (part.strip() for part in spec.split(",")):
        if not element:
            raise UsageError(
                f"--widths has an empty element in {spec!r}; expected widths and "
                "skips separated by commas, e.g. 2x,5,2x,8"
            )
        match = WIDTH_RE.match(element)
        if not match:
            raise UsageError(
                f"--widths element {element!r} is malformed; expected a width "
                "such as 12, a named width such as 12:DATE, or a skip such as 2x"
            )
        if match[2]:  # a skip: Nx, or bare x meaning one position
            skip = int(match[1]) if match[1] else 1
            if skip < 1:
                raise UsageError(f"--widths skip {element!r} must be at least 1")
            cursor += skip
            continue
        width = int(match[3])
        if width < 1:
            raise UsageError(f"--widths element {element!r} must be at least 1")
        end = cursor + width - 1
        if end > record_width:
            raise UsageError(
                f"--widths element {element!r} would end at position {end}, past "
                f"the record width of {record_width}"
            )
        fields.append(Field(cursor, end, match[4]))
        cursor = end + 1

    if not fields:
        raise UsageError("--widths defines no columns")
    return tuple(fields)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="fixed2tab",
        description=(
            "Convert fixed-width column text to TSV, detecting the column "
            "boundaries automatically. Boundaries are found by locating "
            "character positions blank in every record, so the common case "
            "needs no configuration."
        ),
        epilog=EPILOG,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("--version", action="version", version=f"fixed2tab {__version__}")

    io_group = parser.add_argument_group("input and outputs (all required)")
    io_group.add_argument("--input", required=True, type=Path, help="fixed-width text file to read")
    io_group.add_argument("--table", required=True, type=Path, help="TSV output")
    io_group.add_argument("--report", required=True, type=Path, help="geometry report; read this when a boundary looks wrong")
    io_group.add_argument("--rejected", required=True, type=Path, help="every unparsed line, verbatim, with its input line number")

    geo = parser.add_argument_group("geometry")
    geo.add_argument("--columns", metavar="SPEC", help="explicit columns, e.g. '1-12:DATE,18-27:BEST_TIME'; overrides detection. Positions are 1-based inclusive")
    geo.add_argument("--widths", metavar="SPEC", help="explicit column widths instead of ranges, e.g. '2x,5,2x,8' — a FORTRAN FORMAT such as 2X,I5,2X,F8.3 transcribes directly. Nx skips N positions; N:NAME names a column. Mutually exclusive with --columns")
    geo.add_argument("--min-gutter", type=int, default=2, metavar="N", help="blank positions needed to separate two columns (default: 2). Lower splits inside cells like '4h  6m 22s'; higher merges narrow columns")
    geo.add_argument("--record-width", type=int, metavar="N", help="override the detected record width, e.g. for a file whose trailing blanks were stripped")
    geo.add_argument("--header-line", type=int, metavar="N", help="1-based line to use as the column headings; also extends the preamble through that line")

    read = parser.add_argument_group("reading")
    read.add_argument("--encoding", default="utf-8", metavar="NAME", help="input encoding (default: utf-8). Try latin-1 for byte-padded legacy output")
    read.add_argument("--short-lines", choices=ShortLinePolicy.CHOICES, default=ShortLinePolicy.DEFAULT, help="lines shorter than the record width: reject them (default) or pad with spaces. Padding can promote page numbers and footers to data rows, so it is opt-in")

    out = parser.add_argument_group("output shape")
    out.add_argument("--collapse-spaces", action="store_true", help="reduce runs of spaces inside each cell to one. Off by default. Applies to EVERY cell, so meaningful internal spacing is flattened too: '4h  6m 22s' becomes '4h 6m 22s'. Useful when a range merges several layout columns into one value, such as a date written as year, month and day")
    out.add_argument("--header", choices=HeaderMode.CHOICES, default=HeaderMode.DEFAULT, help="write column names as the table's first row, or not (default: none). Names always appear in the report")

    strict = parser.add_argument_group("strictness")
    strict.add_argument("--strict", action="store_true", help="exit non-zero if any line is rejected for a reason not in --allow-rejects")
    strict.add_argument("--allow-rejects", metavar="R", nargs="*", choices=RejectReason.names(), default=list(RejectReason.names()), help=f"rejection reasons tolerated under --strict (default: all). One or more of: {', '.join(RejectReason.names())}")
    return parser


def _encoding_diagnostic(source: LineSource, char_widths: int) -> Diagnostic | None:
    """Warn when byte lengths are uniform but character lengths are not.

    That combination means the generating program padded by bytes while the
    chosen encoding is multi-byte, so character offsets no longer line up with
    the columns. The output would be quietly wrong rather than visibly broken,
    which is the failure mode worth spending a pass to catch.
    """
    byte_hist = source.byte_lengths()
    if len(byte_hist) == 1 and char_widths > 1:
        return Diagnostic(
            "byte-vs-char",
            "every line has the same length in bytes but not in characters "
            f"under --encoding {source.encoding}. The file is probably "
            "byte-padded legacy output; try --encoding latin-1.",
        )
    return None


def run(args: argparse.Namespace) -> int:
    if args.min_gutter < 1:
        raise UsageError(f"--min-gutter must be at least 1, got {args.min_gutter}")
    if args.record_width is not None and args.record_width < 1:
        raise UsageError(f"--record-width must be at least 1, got {args.record_width}")
    if args.header_line is not None and args.header_line < 1:
        raise UsageError(f"--header-line is 1-based, got {args.header_line}")
    try:
        codecs.lookup(args.encoding)
    except LookupError as exc:
        raise UsageError(f"unknown --encoding {args.encoding!r}") from exc

    source = LineSource(args.input, args.encoding)

    profile, diagnostics = profile_lengths(source)
    width = args.record_width or profile.record_width
    if enc_note := _encoding_diagnostic(source, len(profile.histogram)):
        diagnostics.append(enc_note)

    classified, class_notes = classify_input(
        source, width, args.short_lines, args.header_line
    )
    diagnostics += class_notes

    if classified.counts.table_rows == 0:
        # Exiting non-zero rather than writing an empty table. With no records
        # the blank map is vacuously all-blank, so every position looks like a
        # gutter and the run would "succeed" with zero columns and zero rows —
        # a total failure wearing the appearance of a clean conversion.
        raise InputError(
            "no line was classified as a record.\n"
            f"  detected record width: {width}\n"
            "  line lengths: "
            + ", ".join(f"{n}x{c}" for n, c in profile.histogram[:8])
            + "\n  rejected by reason: "
            + ", ".join(f"{k}={v}" for k, v in classified.by_reason.items() if v)
            + "\n  If the width is wrong, set --record-width; if records are "
            "shorter than it, add --short-lines pad."
        )

    if classified.counts.table_rows < 6 and not args.columns:
        # Detection needs enough records for a position to prove itself
        # non-blank. On a small sample, positions that are merely usually blank
        # look always blank, so the field count can come out right while the
        # boundaries are wrong — the failure is silent and plausible.
        diagnostics.append(
            Diagnostic(
                "under-determined",
                f"only {classified.counts.table_rows} record(s) were classified; "
                "the detected geometry may be under-determined by so small a "
                "sample. Check the column ranges below against a wider file.",
            )
        )

    # The blank-run scan runs even under --columns: the report promises a
    # per-field blank rate and a run listing, and those are how a user judges
    # whether their supplied geometry is right.
    runs = blank_runs(classified.blank)
    if args.columns is not None and args.widths is not None:
        raise UsageError("--columns and --widths both describe the geometry; supply one")
    # Tested against None, not truthiness: an explicitly empty value is a
    # mistake to report, not a reason to quietly fall back to detection.
    if args.columns is not None or args.widths is not None:
        fields = (
            parse_columns(args.columns, width)
            if args.columns is not None
            else parse_widths(args.widths, width)
        )
        detected = False
        covered = {p for f in fields for p in range(f.start, f.end + 1)}
        uncovered = [
            i + 1
            for i, is_blank in enumerate(classified.blank)
            if not is_blank and (i + 1) not in covered
        ]
        if uncovered:
            # Collapsed into ranges rather than listed position by position. A
            # dropped column is a contiguous run, and an itemised list truncated
            # at twenty entries would show the first column's positions and hide
            # every later one — the opposite of what this warning is for.
            spans: list[str] = []
            run_start = previous = uncovered[0]
            for position in uncovered[1:]:
                if position != previous + 1:
                    spans.append(
                        str(run_start) if run_start == previous else f"{run_start}-{previous}"
                    )
                    run_start = position
                previous = position
            spans.append(str(run_start) if run_start == previous else f"{run_start}-{previous}")
            diagnostics.append(
                Diagnostic(
                    "uncovered-positions",
                    f"{len(uncovered)} character position(s) carrying data are in "
                    f"no --columns range and will be dropped: {', '.join(spans)}",
                )
            )
        if args.min_gutter != 2:
            diagnostics.append(
                Diagnostic(
                    "min-gutter-ignored",
                    "--min-gutter does not affect the table when the geometry is "
                    "supplied explicitly.",
                )
            )
    else:
        fields, runs = derive_fields(classified.blank, args.min_gutter)
        detected = True
        if len(fields) == 1:
            diagnostics.append(
                Diagnostic(
                    "single-field",
                    f"no run of {args.min_gutter} or more blank positions was "
                    "found, so the whole record is one column. Lower "
                    "--min-gutter if the columns are separated by a single "
                    "space, or supply --columns.",
                )
            )

    # Supplied names go *in* rather than being pasted over the derived ones
    # afterwards. Overwriting left the report warning about duplicates it had
    # just resolved — and a report that flags a problem it has already fixed
    # undermines the one artefact this tool asks the reader to trust.
    named, name_notes = name_fields(
        fields,
        classified.preamble.header,
        supplied=[f.name for f in fields] if not detected else None,
    )
    diagnostics += name_notes

    # In formatted output a single heading commonly sits above several fields —
    # a date written as year, month and day. Whitespace cannot express that
    # grouping, so detection splits them and the outer ones end up unnamed. Say
    # so, without pretending to know which ones belong together.
    headingless = [
        i + 1 for i, f in enumerate(named) if f.name and re.fullmatch(r"col\d+", f.name)
    ]
    if headingless and classified.preamble.header and detected:
        diagnostics.append(
            Diagnostic(
                "fields-without-heading",
                f"column(s) {', '.join(str(i) for i in headingless)} have no heading "
                "of their own. In formatted output one heading often covers several "
                "columns; if adjacent columns are parts of one value, merge them with "
                "--columns or --widths.",
            )
        )

    geometry = Geometry(width, named, runs, args.min_gutter, detected)

    counts, stats, by_reason = write_outputs(
        source,
        geometry,
        classified.preamble,
        args.table,
        args.rejected,
        args.short_lines,
        args.header_line,
        args.header,
        args.collapse_spaces,
    )
    parameters = {
        "collapse-spaces": args.collapse_spaces,
        "columns": args.columns or "(detected)",
        "encoding": args.encoding,
        "header": args.header,
        "header-line": args.header_line if args.header_line else "(auto)",
        "min-gutter": args.min_gutter,
        "record-width": args.record_width if args.record_width else "(detected)",
        "short-lines": args.short_lines,
        "strict": args.strict,
        "widths": args.widths or "(not used)",
    }
    args.report.write_text(
        render_report(
            geometry,
            profile,
            classified.preamble,
            counts,
            stats,
            by_reason,
            diagnostics,
            parameters,
        ),
        encoding="utf-8",
        newline="",
    )

    if args.strict:
        allowed = set(args.allow_rejects)
        offending = {r: n for r, n in by_reason.items() if n and r not in allowed}
        if offending:
            raise StrictViolation(
                "--strict: "
                + ", ".join(f"{n} line(s) rejected as {r}" for r, n in sorted(offending.items()))
            )
    return ExitCode.SUCCESS


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        return int(run(args))
    except BrokenPipeError:
        # A downstream reader closed early. Not an error worth a traceback.
        return int(ExitCode.SUCCESS)
    except OSError as exc:
        print(f"fixed2tab: cannot write output: {exc}", file=sys.stderr)
        return int(ExitCode.INPUT)
    except Fixed2TabError as exc:
        # stderr carries messages only when the exit status is non-zero. Warnings
        # go to the report instead: Galaxy's legacy stdio handling treats any
        # stderr output as job failure, so a warning here would turn a correct
        # conversion into a red dataset.
        print(f"fixed2tab: {exc}", file=sys.stderr)
        return int(exc.exit_code)


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
